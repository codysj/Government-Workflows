"""Workflow 1 — Bank Reconciliation.

Spec: docs/Project_Outline_Master.md, "Workflow 1 — Bank Reconciliation".

DETERMINISTIC code (this module) owns ALL of:
  * exact match by amount and date (configurable amount/date tolerance),
  * unmatched bank items / unmatched ledger items,
  * potential timing differences (amount matches, date within tolerance),
  * potential duplicate payments/deposits (within-table),
  * summary totals,
  * source-row tracking, validation, export formatting, audit logging.

The LLM may ONLY summarize unmatched items, draft plain-language explanations
of likely causes, suggest human review steps, group exceptions into categories,
and draft reconciliation memo language. It MUST NOT perform matching, calculate,
or invent accounts/amounts/dates, and it must cite the deterministic source-row
references.

This module contains NO Streamlit and NO provider-specific code. It plugs into
the shared pipeline the same way report_review/freeform do:
  * ``WORKFLOW_TYPE`` — the registry key.
  * ``register(registry)`` — dict/callable registry helper.
  * ``run(inputs, ...)`` — the workflow entry point.
  * ``WORKFLOW`` / ``WORKFLOW_REGISTRY`` — metadata / self-registration.

The authoritative matcher is ``src.normalize.matching`` (the LLM never matches).
Mock mode is the DEFAULT path (no API key, no internet).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.core.schemas import (
    CapabilitySpec,
    ColumnMapping,
    DeterministicFinding,
    ExportArtifact,
    FileProfile,
    FindingType,
    LLMResponse,
    ParsedTable,
    PreflightConditionCode,
    PreflightFinding,
    Severity,
    SourceRowRef,
    ValidationResult,
    make_id,
)
from src.core.exports import write_csv, write_json, write_markdown
from src.core.validation import validate_llm_output as _core_validate_llm_output
from src.llm.provider import MockLLMProvider as _CoreMockLLMProvider
from src.llm.provider import _extract_findings_from_prompt
from src.ingest.excel_loader import load_table
from src.normalize.cleaning import (
    derive_signed_amount,
    normalize_columns,
    parse_amount,
    parse_date,
    to_snake_case,
)
from src.normalize.matching import (
    MatchCandidate,
    detect_duplicates,
    match_records,
)

WORKFLOW_TYPE = "bank_reconciliation"
PROMPT_TEMPLATE_VERSION = "bank_reconciliation.v1"

WORKFLOW_REGISTRY: dict[str, Any] = {}

EXPORT_FILE_NAMES = (
    "reconciliation_summary.md",
    "matched_transactions.csv",
    "unmatched_bank_items.csv",
    "unmatched_ledger_items.csv",
    "validation_report.json",
    "audit_log.json",
)

DEFAULT_AMOUNT_TOLERANCE = Decimal("0")
DEFAULT_DATE_TOLERANCE_DAYS = 0

# Candidate column names (after snake_case normalization).
_DATE_COLUMNS = ("date", "txn_date", "transaction_date", "posted_date", "post_date")
_AMOUNT_COLUMNS = ("amount", "value", "txn_amount", "transaction_amount")
_DESC_COLUMNS = ("description", "memo", "payee", "vendor", "details", "narrative")


# --------------------------------------------------------------------------- #
# PREFLIGHT / CAPABILITY layer (shared contract; see src.core.preflight)
# --------------------------------------------------------------------------- #
# What this workflow can deterministically handle. The runner runs the uploaded
# input set through ``run_preflight(CAPABILITY, inputs, detect_conditions=...)``
# BEFORE any reconciliation or LLM call. PASS -> run; PARTIAL -> run supported
# logic and surface the flagged conditions (LLM may only explain deterministic
# findings); FAIL -> do not run and do not call the LLM.
CAPABILITY = CapabilitySpec(
    workflow_type=WORKFLOW_TYPE,
    required_inputs=["bank", "ledger"],
    optional_inputs=["chart_of_accounts"],
    accepted_file_types={"*": ["csv", "xlsx"]},
    required_semantic_columns={
        "bank": ["date", "amount"],
        "ledger": ["date", "amount"],
    },
    optional_semantic_columns={
        "bank": ["description"],
        "ledger": ["description"],
    },
    supported_patterns=[
        "exact_amount_and_date_match_within_tolerance",
        "potential_timing_difference",
        "within_table_duplicate_detection",
        "unmatched_bank_items",
        "unmatched_ledger_items",
    ],
    partially_supported_patterns=[
        "possible_sign_convention_mismatch",
        "possible_batch_matching",
        "possible_prior_period_items",
    ],
    unsupported_patterns=[
        "many_to_one_batch_matching",
        "multi_currency_reconciliation",
    ],
    notes=(
        "Deterministic 1:1 reconciliation by amount and date (within tolerance), "
        "plus within-table duplicate detection and unmatched-item reporting. "
        "Many-to-one batch matching and multi-currency are NOT supported; the "
        "detector flags likely instances as PARTIAL conditions for human review."
    ),
)

# Thresholds for the conservative domain detectors below. Tuned so a clean
# demo never trips them.
_SIGN_MISMATCH_MIN_OVERLAP = 4  # need this many shared |amounts| to claim a pattern
_SIGN_MISMATCH_MIN_FRACTION = 0.6  # ...and this fraction opposite-signed
_BATCH_MIN_COMPONENTS = 3  # one side total == sum of >= this many of the other
_PRIOR_PERIOD_GAP_DAYS = 45  # date this far outside the bulk window = prior-period


def _mapped_or_auto(
    profile: Optional[FileProfile],
    mapping: dict[str, ColumnMapping],
    semantic: str,
    candidates: tuple[str, ...],
) -> Optional[str]:
    """Resolve a semantic column for a detector.

    Prefer an approved/auto mapping (``source != 'unmapped'``); else fall back
    to the first matching candidate column actually present in the profile.
    """
    m = mapping.get(semantic)
    if m is not None and m.mapped_column and m.source != "unmapped":
        return m.mapped_column
    if profile is not None:
        for c in candidates:
            if c in profile.detected_columns:
                return c
    return None


def _amounts(parsed: Any, df: pd.DataFrame, amount_col: str) -> list[Decimal]:
    vals: list[Decimal] = []
    for v in df[amount_col].tolist():
        a = parse_amount(v)
        if a is not None:
            vals.append(a)
    return vals


def detect_conditions(
    profiles: dict[str, FileProfile],
    mappings: dict[str, dict[str, ColumnMapping]],
    inputs: dict[str, Any],
    config: Optional[dict],
) -> list[PreflightFinding]:
    """Domain-specific unsupported-condition detection for bank reconciliation.

    Emits only NON-blocking ``possible_*`` findings (PARTIAL, never FAIL). Each
    is CONSERVATIVE: it fires only on real signal so a clean demo always PASSes.

    Conditions:
      * POSSIBLE_SIGN_CONVENTION_MISMATCH — bank vs ledger amounts that share
        the same magnitude are systematically opposite-signed (e.g. bank shows
        debits negative, ledger positive).
      * POSSIBLE_BATCH_MATCHING — one row on one side plausibly equals the sum
        of several rows on the other (many small bank items -> one ledger
        total, or vice versa); this is the unsupported many-to-one case.
      * POSSIBLE_PRIOR_PERIOD_ITEM — dates far outside the bulk transaction
        window (likely a prior-period item carried in).
    """
    findings: list[PreflightFinding] = []

    bank_prof = profiles.get("bank")
    ledger_prof = profiles.get("ledger")
    if (
        bank_prof is None
        or ledger_prof is None
        or not bank_prof.present
        or not ledger_prof.present
    ):
        return findings  # generic engine already flags the missing required file

    b_map = mappings.get("bank", {})
    l_map = mappings.get("ledger", {})

    b_amt_col = _mapped_or_auto(bank_prof, b_map, "amount", _AMOUNT_COLUMNS)
    l_amt_col = _mapped_or_auto(ledger_prof, l_map, "amount", _AMOUNT_COLUMNS)
    b_date_col = _mapped_or_auto(bank_prof, b_map, "date", _DATE_COLUMNS)
    l_date_col = _mapped_or_auto(ledger_prof, l_map, "date", _DATE_COLUMNS)
    if not (b_amt_col and l_amt_col):
        return findings  # without amounts there's no reliable domain signal

    # Re-read the parsed frames the same way the engine profiled them.
    try:
        b_parsed = _as_parsed(inputs.get("bank"), "bank")
        l_parsed = _as_parsed(inputs.get("ledger"), "ledger")
        b_df = normalize_columns(b_parsed.dataframe).reset_index(drop=True)
        l_df = normalize_columns(l_parsed.dataframe).reset_index(drop=True)
        # Mirror the engine/reconcile derivation so Tyler/Munis-style
        # debit/credit exports keep their domain detectors active.
        b_df, _ = derive_signed_amount(b_df)
        l_df, _ = derive_signed_amount(l_df)
    except Exception:
        return findings  # the generic engine owns unreadable inputs

    if b_amt_col not in b_df.columns or l_amt_col not in l_df.columns:
        return findings

    bank_amts = _amounts(b_parsed, b_df, b_amt_col)
    ledger_amts = _amounts(l_parsed, l_df, l_amt_col)

    # --- POSSIBLE_SIGN_CONVENTION_MISMATCH ------------------------------- #
    # Look at shared magnitudes. If the items that share a magnitude are
    # systematically opposite-signed across the two tables, the sign convention
    # likely differs (the matcher matches on signed amount and would miss them).
    bank_by_mag: dict[Decimal, list[Decimal]] = {}
    for a in bank_amts:
        bank_by_mag.setdefault(abs(a), []).append(a)
    ledger_by_mag: dict[Decimal, list[Decimal]] = {}
    for a in ledger_amts:
        ledger_by_mag.setdefault(abs(a), []).append(a)

    shared_mags = [m for m in bank_by_mag if m in ledger_by_mag and m != 0]
    opposite = 0
    same = 0
    for m in shared_mags:
        b_sign = bank_by_mag[m][0] < 0
        l_sign = ledger_by_mag[m][0] < 0
        if b_sign != l_sign:
            opposite += 1
        else:
            same += 1
    overlap = opposite + same
    if overlap >= _SIGN_MISMATCH_MIN_OVERLAP and opposite >= round(
        _SIGN_MISMATCH_MIN_FRACTION * overlap
    ):
        findings.append(
            PreflightFinding(
                code=PreflightConditionCode.POSSIBLE_SIGN_CONVENTION_MISMATCH,
                severity=Severity.MEDIUM,
                message=(
                    f"{opposite} of {overlap} shared amounts are opposite-signed "
                    "between bank and ledger; the two files may use different "
                    "debit/credit sign conventions."
                ),
                affected_input="bank",
                affected_column=b_amt_col,
                suggested_action=(
                    "Confirm the sign convention for both files (e.g. whether "
                    "withdrawals are negative). Normalize signs to match before "
                    "reconciling; exact matching is on signed amounts."
                ),
                blocks_run=False,
            )
        )

    # --- POSSIBLE_BATCH_MATCHING (many-to-one; UNSUPPORTED) -------------- #
    # If a single row on one side equals the sum of several distinct rows on
    # the other (and isn't simply a 1:1 match), flag many-to-one batch matching.
    def _batch_signal(
        one_side: list[Decimal], many_side: list[Decimal]
    ) -> Optional[Decimal]:
        many_set = [a for a in many_side if a != 0]
        one_singletons = set(many_set)
        for total in one_side:
            if total == 0:
                continue
            # Skip totals that already have a 1:1 partner (that's supported).
            if total in one_singletons:
                continue
            components = [a for a in many_set if abs(a) < abs(total)]
            if len(components) < _BATCH_MIN_COMPONENTS:
                continue
            if _subset_sums_to(components, total, _BATCH_MIN_COMPONENTS):
                return total
        return None

    batch_total = _batch_signal(ledger_amts, bank_amts)
    side = "bank"
    affected_col = b_amt_col
    if batch_total is None:
        batch_total = _batch_signal(bank_amts, ledger_amts)
        side = "ledger"
        affected_col = l_amt_col
    if batch_total is not None:
        findings.append(
            PreflightFinding(
                code=PreflightConditionCode.POSSIBLE_BATCH_MATCHING,
                severity=Severity.MEDIUM,
                message=(
                    f"A total of {batch_total} on one side appears to equal the "
                    f"sum of several individual {side} items (many-to-one batch "
                    "deposit/payment). Many-to-one batch matching is not "
                    "supported; only 1:1 matching runs."
                ),
                affected_input=side,
                affected_column=affected_col,
                suggested_action=(
                    "Split the batched total into its component transactions, or "
                    "review the batch manually. The tool matches 1:1 only."
                ),
                blocks_run=False,
            )
        )

    # --- POSSIBLE_PRIOR_PERIOD_ITEM ------------------------------------- #
    if b_date_col or l_date_col:
        dates: list[Any] = []
        cols = [(b_df, b_date_col), (l_df, l_date_col)]
        for frame, col in cols:
            if col and col in frame.columns:
                for v in frame[col].tolist():
                    d = parse_date(v)
                    if d is not None:
                        dates.append(d)
        if len(dates) >= 4:
            ordered = sorted(dates)
            # Bulk window = the inter-quartile span; anything far outside it is
            # a likely prior-period (or future) item.
            lo = ordered[len(ordered) // 4]
            hi = ordered[(3 * len(ordered)) // 4]
            outliers = [
                d
                for d in ordered
                if (lo - d).days > _PRIOR_PERIOD_GAP_DAYS
                or (d - hi).days > _PRIOR_PERIOD_GAP_DAYS
            ]
            if outliers:
                findings.append(
                    PreflightFinding(
                        code=PreflightConditionCode.POSSIBLE_PRIOR_PERIOD_ITEM,
                        severity=Severity.MEDIUM,
                        message=(
                            f"{len(outliers)} transaction date(s) fall well "
                            "outside the bulk period (e.g. "
                            f"{outliers[0]}); these may be prior-period items "
                            "carried into this reconciliation."
                        ),
                        affected_input="bank",
                        affected_column=b_date_col or l_date_col,
                        suggested_action=(
                            "Confirm whether the outlier-dated items belong to "
                            "this reconciliation period or a prior period before "
                            "treating them as unmatched."
                        ),
                        blocks_run=False,
                    )
                )

    return findings


def _subset_sums_to(
    values: list[Decimal], target: Decimal, min_terms: int
) -> bool:
    """True if some subset of ``values`` (size >= ``min_terms``) sums to target.

    Bounded, conservative subset-sum over a small fixture-sized list. Caps the
    search so it never runs long on real inputs (returns False if too large).
    """
    pool = [v for v in values if v != 0]
    if len(pool) > 18:  # keep it cheap; large inputs won't be flagged here
        return False
    # DP over reachable sums tracking the minimum number of terms is overkill;
    # a bounded DFS is clearer for these tiny lists.
    n = len(pool)

    def dfs(start: int, remaining: Decimal, count: int) -> bool:
        if remaining == 0 and count >= min_terms:
            return True
        if start >= n or count > 12:
            return False
        for i in range(start, n):
            v = pool[i]
            if abs(v) > abs(remaining) and (remaining - v) * remaining < 0:
                # overshoot in the wrong direction; skip
                continue
            if dfs(i + 1, remaining - v, count + 1):
                return True
        return False

    return dfs(0, target, 0)


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class ReconciliationConfig:
    """Deterministic tolerances. No LLM input."""

    amount_tolerance: Decimal = DEFAULT_AMOUNT_TOLERANCE
    date_tolerance_days: int = DEFAULT_DATE_TOLERANCE_DAYS

    @classmethod
    def from_config(cls, cfg: Any) -> "ReconciliationConfig":
        if cfg is None:
            return cls()
        if isinstance(cfg, ReconciliationConfig):
            return cfg
        if isinstance(cfg, (str, Path)):
            cfg = json.loads(Path(cfg).read_text(encoding="utf-8"))
        return cls(
            amount_tolerance=Decimal(str(cfg.get("amount_tolerance", DEFAULT_AMOUNT_TOLERANCE))),
            date_tolerance_days=int(cfg.get("date_tolerance_days", DEFAULT_DATE_TOLERANCE_DAYS)),
        )


# --------------------------------------------------------------------------- #
# Deterministic output container (exposes .findings + .summary like the others)
# --------------------------------------------------------------------------- #
@dataclass
class ReconciliationOutput:
    findings: list[DeterministicFinding] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    result_tables: dict[str, pd.DataFrame] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _as_parsed(table_or_path: Any, default_name: str) -> ParsedTable:
    if isinstance(table_or_path, ParsedTable):
        return table_or_path
    # CSV or Excel by extension (the CAPABILITY accepts both).
    return load_table(table_or_path, table_name=default_name)


def _first_col(df: pd.DataFrame, candidates: tuple[str, ...]) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _short_ref(ref: SourceRowRef) -> str:
    return f"{ref.table_name}:{ref.row_index}"


def _row_ref(file_id: str, table_name: str, row_index: int, row: pd.Series) -> SourceRowRef:
    cols = list(row.index)
    return SourceRowRef(
        file_id=file_id,
        table_name=table_name,
        row_index=int(row_index),
        column_names=cols,
        source_values={c: ("" if pd.isna(row[c]) else row[c]) for c in cols},
    )


def _candidates(
    df: pd.DataFrame, amount_col: str, date_col: str
) -> list[MatchCandidate]:
    cands: list[MatchCandidate] = []
    for i, row in df.iterrows():
        cands.append(
            MatchCandidate(
                index=int(i),
                amount=parse_amount(row[amount_col]),
                txn_date=parse_date(row[date_col]),
                raw=row.to_dict(),
            )
        )
    return cands


# --------------------------------------------------------------------------- #
# Core deterministic reconciliation
# --------------------------------------------------------------------------- #
def _resolve_col(
    df: pd.DataFrame,
    mapping: Optional[dict[str, str]],
    semantic: str,
    candidates: tuple[str, ...],
) -> Optional[str]:
    """Resolve a semantic column, preferring a human-approved mapping override.

    When ``mapping`` supplies ``semantic`` and that (snake-cased) column exists
    in ``df``, it wins; otherwise fall back to the current auto-detection by
    candidate name. ``None``/absent mapping = unchanged current behavior.
    """
    if mapping:
        col = mapping.get(semantic)
        if col:
            snake = to_snake_case(col)
            if snake in df.columns:
                return snake
    return _first_col(df, candidates)


def reconcile(
    bank: Any,
    ledger: Any,
    *,
    config: ReconciliationConfig | None = None,
    column_mappings: Optional[dict[str, dict[str, str]]] = None,
) -> ReconciliationOutput:
    """Run the full deterministic reconciliation.

    ``bank`` / ``ledger`` accept a ``ParsedTable`` or a path to a CSV.

    ``column_mappings`` is an optional ``{input_key: {semantic: column}}`` map
    (e.g. derived from an approved preflight report's ``column_mappings`` where
    ``source != 'unmapped'``). When provided, the mapped columns override
    auto-detection for that input/semantic; ``None`` keeps current auto-detect
    behavior unchanged. Source-row indices are preserved regardless.
    """
    config = config or ReconciliationConfig()
    column_mappings = column_mappings or {}
    b_overrides = column_mappings.get("bank")
    l_overrides = column_mappings.get("ledger")

    b_parsed = _as_parsed(bank, "bank")
    l_parsed = _as_parsed(ledger, "ledger")
    b_df = normalize_columns(b_parsed.dataframe).reset_index(drop=True)
    l_df = normalize_columns(l_parsed.dataframe).reset_index(drop=True)

    # Source-row refs always cite the file's REAL (normalized) columns.
    b_src_df = b_df
    l_src_df = l_df

    # Tyler/Munis-style exports: derive the signed 'amount' (= debit - credit,
    # same semantics as src.ingest.tyler) when the file carries separate
    # debit/credit columns and no amount column. Deterministic; original
    # columns kept; surfaced in the summary below.
    b_df, b_amt_derived = derive_signed_amount(b_df)
    l_df, l_amt_derived = derive_signed_amount(l_df)

    b_amt = _resolve_col(b_df, b_overrides, "amount", _AMOUNT_COLUMNS)
    b_date = _resolve_col(b_df, b_overrides, "date", _DATE_COLUMNS)
    l_amt = _resolve_col(l_df, l_overrides, "amount", _AMOUNT_COLUMNS)
    l_date = _resolve_col(l_df, l_overrides, "date", _DATE_COLUMNS)
    if not (b_amt and b_date and l_amt and l_date):
        raise ValueError(
            "Could not locate amount/date columns. "
            f"bank has {list(b_df.columns)}; ledger has {list(l_df.columns)}."
        )

    bank_cands = _candidates(b_df, b_amt, b_date)
    ledger_cands = _candidates(l_df, l_amt, l_date)

    result = match_records(
        bank_cands,
        ledger_cands,
        amount_tolerance=config.amount_tolerance,
        date_tolerance_days=config.date_tolerance_days,
    )

    bank_dups = detect_duplicates(
        bank_cands,
        amount_tolerance=config.amount_tolerance,
        date_tolerance_days=config.date_tolerance_days,
    )
    ledger_dups = detect_duplicates(
        ledger_cands,
        amount_tolerance=config.amount_tolerance,
        date_tolerance_days=config.date_tolerance_days,
    )

    findings: list[DeterministicFinding] = []
    matched_rows: list[dict] = []
    unmatched_bank_rows: list[dict] = []
    unmatched_ledger_rows: list[dict] = []

    def bank_ref(idx: int) -> SourceRowRef:
        return _row_ref(b_parsed.file_id, "bank", idx, b_src_df.loc[idx])

    def ledger_ref(idx: int) -> SourceRowRef:
        return _row_ref(l_parsed.file_id, "ledger", idx, l_src_df.loc[idx])

    # --- Matched (exact amount + date) ---------------------------------- #
    for a, b in result.matched_pairs:
        sb = bank_ref(a.index)
        sl = ledger_ref(b.index)
        matched_rows.append(
            {
                "bank_row": a.index,
                "ledger_row": b.index,
                "amount": str(a.amount),
                "bank_date": str(a.txn_date),
                "ledger_date": str(b.txn_date),
            }
        )
        findings.append(
            DeterministicFinding(
                finding_type=FindingType.MATCHED,
                severity=Severity.INFO,
                description=(
                    f"Matched bank row {a.index} to ledger row {b.index} "
                    f"(amount {a.amount}, date {a.txn_date})."
                ),
                source_rows=[sb, sl],
                computed_values={
                    "amount": str(a.amount),
                    "bank_date": str(a.txn_date),
                    "ledger_date": str(b.txn_date),
                },
                rule_used="exact_amount_and_date_match",
                requires_human_review=False,
            )
        )

    # --- Timing differences (amount matches, dates within tolerance) ---- #
    for a, b in result.timing_pairs:
        sb = bank_ref(a.index)
        sl = ledger_ref(b.index)
        findings.append(
            DeterministicFinding(
                finding_type=FindingType.TIMING_DIFFERENCE,
                severity=Severity.MEDIUM,
                description=(
                    f"Potential timing difference: bank row {a.index} "
                    f"(date {a.txn_date}) and ledger row {b.index} "
                    f"(date {b.txn_date}) share amount {a.amount} but differ in date."
                ),
                source_rows=[sb, sl],
                computed_values={
                    "amount": str(a.amount),
                    "bank_date": str(a.txn_date),
                    "ledger_date": str(b.txn_date),
                },
                rule_used="amount_match_within_date_tolerance",
                requires_human_review=True,
            )
        )

    # --- Unmatched bank items ------------------------------------------- #
    for a in result.unmatched_left:
        sb = bank_ref(a.index)
        unmatched_bank_rows.append(
            {
                "bank_row": a.index,
                "amount": str(a.amount),
                "date": str(a.txn_date),
            }
        )
        findings.append(
            DeterministicFinding(
                finding_type=FindingType.UNMATCHED_BANK,
                severity=Severity.HIGH,
                description=(
                    f"Unmatched bank item: bank row {a.index} "
                    f"(amount {a.amount}, date {a.txn_date}) has no ledger match."
                ),
                source_rows=[sb],
                computed_values={"amount": str(a.amount), "date": str(a.txn_date)},
                rule_used="bank_item_without_ledger_match",
                requires_human_review=True,
            )
        )

    # --- Unmatched ledger items ----------------------------------------- #
    for b in result.unmatched_right:
        sl = ledger_ref(b.index)
        unmatched_ledger_rows.append(
            {
                "ledger_row": b.index,
                "amount": str(b.amount),
                "date": str(b.txn_date),
            }
        )
        findings.append(
            DeterministicFinding(
                finding_type=FindingType.UNMATCHED_LEDGER,
                severity=Severity.HIGH,
                description=(
                    f"Unmatched ledger item: ledger row {b.index} "
                    f"(amount {b.amount}, date {b.txn_date}) has no bank match."
                ),
                source_rows=[sl],
                computed_values={"amount": str(b.amount), "date": str(b.txn_date)},
                rule_used="ledger_item_without_bank_match",
                requires_human_review=True,
            )
        )

    # --- Potential duplicates (within each table) ----------------------- #
    for a, b in bank_dups:
        findings.append(
            DeterministicFinding(
                finding_type=FindingType.DUPLICATE,
                severity=Severity.MEDIUM,
                description=(
                    f"Potential duplicate bank entry: rows {a.index} and {b.index} "
                    f"share amount {a.amount} and date {a.txn_date}."
                ),
                source_rows=[bank_ref(a.index), bank_ref(b.index)],
                computed_values={"amount": str(a.amount), "date": str(a.txn_date)},
                rule_used="duplicate_within_bank",
                requires_human_review=True,
            )
        )
    for a, b in ledger_dups:
        findings.append(
            DeterministicFinding(
                finding_type=FindingType.DUPLICATE,
                severity=Severity.MEDIUM,
                description=(
                    f"Potential duplicate ledger entry: rows {a.index} and {b.index} "
                    f"share amount {a.amount} and date {a.txn_date}."
                ),
                source_rows=[ledger_ref(a.index), ledger_ref(b.index)],
                computed_values={"amount": str(a.amount), "date": str(a.txn_date)},
                rule_used="duplicate_within_ledger",
                requires_human_review=True,
            )
        )

    # --- Summary totals -------------------------------------------------- #
    def _total(cands: list[MatchCandidate]) -> Decimal:
        return sum((c.amount or Decimal(0)) for c in cands)

    summary = {
        "workflow_type": WORKFLOW_TYPE,
        "bank_rows": len(bank_cands),
        "ledger_rows": len(ledger_cands),
        "matched": len(result.matched_pairs),
        "timing_differences": len(result.timing_pairs),
        "unmatched_bank": len(result.unmatched_left),
        "unmatched_ledger": len(result.unmatched_right),
        "duplicate_bank_pairs": len(bank_dups),
        "duplicate_ledger_pairs": len(ledger_dups),
        "amount_tolerance": str(config.amount_tolerance),
        "date_tolerance_days": config.date_tolerance_days,
        "total_bank_amount": str(_total(bank_cands)),
        "total_ledger_amount": str(_total(ledger_cands)),
        "requires_human_review": any(f.requires_human_review for f in findings),
    }
    # Audit note: surface any deterministic debit/credit -> amount derivation.
    # Key added ONLY when a derivation happened so existing runs are unchanged.
    derived_amounts = {}
    if b_amt_derived:
        derived_amounts["bank"] = "amount = debit - credit"
    if l_amt_derived:
        derived_amounts["ledger"] = "amount = debit - credit"
    if derived_amounts:
        summary["derived_amount_columns"] = derived_amounts

    result_tables = {
        "matched_transactions": pd.DataFrame(matched_rows),
        "unmatched_bank_items": pd.DataFrame(unmatched_bank_rows),
        "unmatched_ledger_items": pd.DataFrame(unmatched_ledger_rows),
    }
    return ReconciliationOutput(
        findings=findings, summary=summary, result_tables=result_tables
    )


# --------------------------------------------------------------------------- #
# LLM prompt (advisory only — references deterministic findings + source rows)
# --------------------------------------------------------------------------- #
_GUARDRAILS = (
    "You are a finance-review assistant for a small municipal finance team.\n"
    "STRICT RULES:\n"
    "- You may ONLY summarize, explain, classify, flag, and draft memo language.\n"
    "- You MUST NOT perform matching, calculate, or invent account numbers, "
    "funds, vendors, amounts, or dates.\n"
    "- Use ONLY numbers and source-row ids that appear in the findings below.\n"
    "- Every claim must cite source_row ids in 'referenced_source_rows'.\n"
    "- All output is a DRAFT for human review.\n"
)

_OUTPUT_CONTRACT = (
    "Return JSON with keys: summary (str), categorized_exceptions (list of "
    "{category, description, referenced_source_rows}), referenced_source_rows "
    "(list of str), suggested_review_steps (list of str), draft_memo (str).\n"
)


def _findings_block(det: ReconciliationOutput) -> str:
    rows = []
    for f in det.findings:
        rows.append(
            {
                "finding_id": f.finding_id,
                "finding_type": f.finding_type.value,
                "rule_used": f.rule_used,
                "severity": f.severity.value,
                "description": f.description,
                "computed_values": f.computed_values,
                "source_row_ids": [_short_ref(s) for s in f.source_rows],
            }
        )
    return json.dumps(rows, default=str, indent=2)


def build_prompt(det: ReconciliationOutput) -> str:
    return (
        _GUARDRAILS
        + "\nWORKFLOW: Bank reconciliation exception review.\n"
        + "TASK: Summarize the unmatched and timing/duplicate exceptions, draft a "
        "plain-language explanation of likely causes, group exceptions into "
        "categories, suggest human review steps, and draft reconciliation memo "
        "language. Do NOT perform matching or compute any figure.\n\n"
        + f"DETERMINISTIC SUMMARY:\n{json.dumps(det.summary, default=str, indent=2)}\n\n"
        + f"DETERMINISTIC FINDINGS:\n{_findings_block(det)}\n\n"
        + _OUTPUT_CONTRACT
    )


# --------------------------------------------------------------------------- #
# Mock LLM (DEFAULT path; no API key, no internet)
# --------------------------------------------------------------------------- #
class MockLLMProvider(_CoreMockLLMProvider):
    """Deterministic mock for bank reconciliation.

    Subclasses the canonical ``src.llm.provider.MockLLMProvider`` (sharing its
    method surface, ``model_provider``/``model_name``, and the offline contract)
    and overrides ``_build`` to produce reconciliation-specific commentary that
    only references the EXCEPTION findings (unmatched/timing/duplicate). Output
    derives ONLY from the deterministic findings and cites the real source-row
    ids; it never matches, calculates, or invents.
    """

    def _build(self, prompt: str) -> dict:
        findings = _extract_findings_from_prompt(prompt)
        exception_types = {
            FindingType.UNMATCHED_BANK.value,
            FindingType.UNMATCHED_LEDGER.value,
            FindingType.TIMING_DIFFERENCE.value,
            FindingType.DUPLICATE.value,
        }
        exceptions = [f for f in findings if f.get("finding_type") in exception_types]
        ref_ids: list[str] = []
        categorized = []
        for f in exceptions:
            refs = f.get("source_row_ids", [])
            ref_ids.extend(refs)
            categorized.append(
                {
                    "category": f.get("finding_type", "other"),
                    "description": f.get("description", ""),
                    "referenced_source_rows": refs,
                }
            )
        return {
            "summary": (
                f"Deterministic reconciliation flagged {len(exceptions)} "
                "exception(s) for human review. See the categorized list; each "
                "item cites the source rows that triggered it."
            ),
            "categorized_exceptions": categorized,
            "referenced_source_rows": sorted(set(ref_ids)),
            "suggested_review_steps": [
                "Confirm each unmatched bank item against the ledger.",
                "Investigate timing differences for in-transit items.",
                "Verify potential duplicate payments/deposits before clearing.",
            ],
            "draft_memo": (
                "DRAFT — for human review only. The automated reconciliation "
                "identified items requiring finance staff confirmation before the "
                "reconciliation can be finalized. No figures were computed or "
                "invented by the assistant."
            ),
        }


def _call_llm(det: ReconciliationOutput, provider: Any = None) -> tuple[dict, str, str]:
    provider = provider or MockLLMProvider()
    prompt = build_prompt(det)
    raw = provider.generate_structured_response(prompt, schema=None)
    if not isinstance(raw, dict):
        raw = {"summary": str(raw), "referenced_source_rows": []}
    return (
        raw,
        getattr(provider, "model_provider", "mock"),
        getattr(provider, "model_name", "mock-llm"),
    )


# --------------------------------------------------------------------------- #
# Validation (deterministic guardrail check)
# --------------------------------------------------------------------------- #
def validate_llm_output(
    response_json: dict, det: ReconciliationOutput
) -> ValidationResult:
    """Thin wrapper: the canonical validator lives in ``src.core.validation``.

    Bank reconciliation historically treated missing source references as a
    WARNING (not a hard error) and did not run the numeric-claim / approval-
    phrase checks, so those knobs are set accordingly here.
    """
    return _core_validate_llm_output(
        response_json,
        det,
        require_references=True,
        missing_references_is_error=False,
        check_numeric_claims=False,
    )


# --------------------------------------------------------------------------- #
# Exports (file-writing + hashing via shared src.core.exports primitives)
# --------------------------------------------------------------------------- #
def export_artifacts(
    out_dir: str | Path,
    det: ReconciliationOutput,
    response_json: dict,
    validation: ValidationResult,
    audit_events: Optional[list[dict]] = None,
    *,
    run_id: Optional[str] = None,
) -> list[ExportArtifact]:
    """Write the six reconciliation artifacts. Returns artifact manifests."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    artifacts: list[ExportArtifact] = []

    def _write_md(name: str, text: str) -> None:
        artifacts.append(write_markdown(out / name, text, run_id=run_id))

    def _write_json(name: str, text: str) -> None:
        artifacts.append(write_json(out / name, text, run_id=run_id))

    def _write_csv(name: str, df: pd.DataFrame) -> None:
        artifacts.append(write_csv(out / name, df, run_id=run_id))

    s = det.summary
    summary_md = [
        "# Bank Reconciliation Summary",
        "",
        f"- Bank rows: {s.get('bank_rows', 0)}",
        f"- Ledger rows: {s.get('ledger_rows', 0)}",
        f"- Matched: {s.get('matched', 0)}",
        f"- Timing differences: {s.get('timing_differences', 0)}",
        f"- Unmatched bank items: {s.get('unmatched_bank', 0)}",
        f"- Unmatched ledger items: {s.get('unmatched_ledger', 0)}",
        f"- Potential duplicate bank pairs: {s.get('duplicate_bank_pairs', 0)}",
        f"- Potential duplicate ledger pairs: {s.get('duplicate_ledger_pairs', 0)}",
        f"- Amount tolerance: {s.get('amount_tolerance')}",
        f"- Date tolerance (days): {s.get('date_tolerance_days')}",
        f"- Total bank amount: {s.get('total_bank_amount')}",
        f"- Total ledger amount: {s.get('total_ledger_amount')}",
        "",
        "## AI Summary (DRAFT — human review required)",
        str(response_json.get("summary", "")),
        "",
        "## Reconciliation memo (DRAFT)",
        str(response_json.get("draft_memo", "")),
        "",
        "_All matching and figures computed deterministically. AI commentary is "
        "a draft for human review._",
    ]
    _write_md("reconciliation_summary.md", "\n".join(summary_md))

    _write_csv("matched_transactions.csv", det.result_tables["matched_transactions"])
    _write_csv("unmatched_bank_items.csv", det.result_tables["unmatched_bank_items"])
    _write_csv("unmatched_ledger_items.csv", det.result_tables["unmatched_ledger_items"])
    _write_json(
        "validation_report.json",
        json.dumps(validation.model_dump(), default=str, indent=2),
    )
    _write_json("audit_log.json", json.dumps(audit_events or [], default=str, indent=2))
    return artifacts


# --------------------------------------------------------------------------- #
# Workflow entry point (same shape as report_review.run / freeform.run)
# --------------------------------------------------------------------------- #
def run(
    inputs: dict[str, Any],
    *,
    provider: Any = None,
    ledger: Any = None,
    audit: Any = None,
    run_id: Optional[str] = None,
    actor: str = "system",
    export_dir: str | Path | None = None,
    config: Any = None,
    column_mappings: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, Any]:
    """End-to-end bank reconciliation run.

    ``inputs`` keys:
        bank            (required) path to the bank statement CSV
        ledger          (required) path to the ledger export CSV
        chart_of_accounts (optional, reserved)
        reconciliation_config (optional) path to a tolerance JSON

    ``config`` may be a ``ReconciliationConfig``, a dict, or a path to JSON; it
    overrides ``inputs['reconciliation_config']`` when given.

    When ``provider`` is None the local mock LLM is used (default path). When
    ``ledger`` (RunLedger) / ``audit`` (AuditLog) are provided their findings/
    responses/validation/events are persisted via the shared method names.

    Returns a dict: run_id, deterministic output, llm response_json, validation
    result, and (when export_dir is set) artifact paths.
    """
    run_id = run_id or make_id()

    if config is None and inputs.get("reconciliation_config"):
        config = inputs["reconciliation_config"]
    cfg = ReconciliationConfig.from_config(config)

    if audit is not None and hasattr(audit, "run_created"):
        audit.run_created(run_id, actor, workflow_type=WORKFLOW_TYPE)

    # Deterministic reconciliation.
    det = reconcile(
        inputs["bank"], inputs["ledger"], config=cfg, column_mappings=column_mappings
    )
    if ledger is not None and hasattr(ledger, "store_findings"):
        ledger.store_findings(run_id, det.findings)
    if audit is not None and hasattr(audit, "deterministic_analysis_completed"):
        audit.deterministic_analysis_completed(
            run_id, actor, finding_count=len(det.findings)
        )

    # LLM assist (advisory; mock by default).
    if audit is not None and hasattr(audit, "llm_request_sent"):
        audit.llm_request_sent(run_id, actor, template=PROMPT_TEMPLATE_VERSION)
    response_json, model_provider, model_name = _call_llm(det, provider)
    llm_response = LLMResponse(
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        model_provider=model_provider,
        model_name=model_name,
        response_json=response_json,
        referenced_source_rows=list(response_json.get("referenced_source_rows", []) or []),
    )
    if ledger is not None and hasattr(ledger, "store_llm_response"):
        ledger.store_llm_response(run_id, llm_response)
    if audit is not None and hasattr(audit, "llm_response_received"):
        audit.llm_response_received(run_id, actor, model_name=model_name)

    # Validation.
    validation = validate_llm_output(response_json, det)
    if ledger is not None and hasattr(ledger, "store_validation_result"):
        ledger.store_validation_result(run_id, validation)
    if audit is not None and hasattr(audit, "validation_completed"):
        audit.validation_completed(run_id, actor, passed=validation.passed)

    result: dict[str, Any] = {
        "run_id": run_id,
        "workflow_type": WORKFLOW_TYPE,
        "deterministic": det,
        "findings": det.findings,
        "summary": det.summary,
        "llm_response": llm_response,
        "response_json": response_json,
        "validation": validation,
    }

    # Exports.
    if export_dir is not None:
        audit_events = (
            audit.list_events(run_id)
            if audit is not None and hasattr(audit, "list_events")
            else []
        )
        artifacts = export_artifacts(
            export_dir, det, response_json, validation, audit_events, run_id=run_id
        )
        if ledger is not None and hasattr(ledger, "store_export_artifact"):
            for a in artifacts:
                ledger.store_export_artifact(run_id, a)
        if audit is not None and hasattr(audit, "export_generated"):
            audit.export_generated(
                run_id, actor, artifacts=[a.file_name for a in artifacts]
            )
        result["export_artifacts"] = artifacts
        result["export_paths"] = {a.file_name: a.path for a in artifacts}

    if audit is not None and hasattr(audit, "run_completed"):
        audit.run_completed(run_id, actor, passed=validation.passed)

    return result


# --------------------------------------------------------------------------- #
# Registry hooks (single import line for the Integration agent)
# --------------------------------------------------------------------------- #
def register(registry: Any) -> None:
    """Register this workflow with a dict-like or callable registry."""
    if hasattr(registry, "register") and callable(registry.register):
        registry.register(WORKFLOW_TYPE, run)
    else:
        registry[WORKFLOW_TYPE] = run


WORKFLOW_REGISTRY[WORKFLOW_TYPE] = run

WORKFLOW = {
    "workflow_type": WORKFLOW_TYPE,
    "run": run,
    "prompt_template_version": PROMPT_TEMPLATE_VERSION,
    "export_files": list(EXPORT_FILE_NAMES),
}
