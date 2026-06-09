"""Budget-to-Actual Variance Review workflow (Workflow 2).

DETERMINISTIC code (this module) does ALL of:
  - join budget & actuals by fund/account/department/object
  - dollar variance and percentage variance calculation
  - threshold flagging
  - missing-account / actual-only / budget-only detection
  - grouping by fund and department
  - source-row tracking, validation, export formatting

The LLM may ONLY explain/summarize/draft/classify/flag and must cite the
source-row references produced deterministically. It NEVER calculates a
variance, decides a flag, or invents an account/fund/amount.

Integration note
----------------
At implementation time the Foundation agent's integration contract was
truncated and the reference `bank_reconciliation.py` did not yet exist (see
docs/decisions.md). This module is therefore self-contained: it depends only
on the already-present shared schemas, cleaning utilities, and loaders. It
self-registers into a module-level ``WORKFLOW_REGISTRY`` via the
``register_workflow`` decorator. To wire it into a global registry the
Integration agent only needs to import this module
(``from src.workflows import budget_variance``); no edits to other modules are
required here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

from src.core.schemas import (
    ArtifactType,
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
from src.ingest.csv_loader import load_csv
from src.normalize.cleaning import normalize_columns, parse_amount, to_snake_case

# --------------------------------------------------------------------------- #
# Registry (self-contained; no other module is edited)
# --------------------------------------------------------------------------- #
WORKFLOW_REGISTRY: dict[str, type] = {}


def register_workflow(workflow_type: str) -> Callable[[type], type]:
    """Class decorator: register a workflow class under ``workflow_type``."""

    def _decorator(cls: type) -> type:
        cls.workflow_type = workflow_type
        WORKFLOW_REGISTRY[workflow_type] = cls
        return cls

    return _decorator


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
DEFAULT_DOLLAR_THRESHOLD = Decimal("10000")
DEFAULT_PCT_THRESHOLD = Decimal("10")  # percent

# Candidate join-key columns (those present in BOTH files are used).
KEY_COLUMNS = ("fund", "account", "department", "object")

PROMPT_TEMPLATE_VERSION = "budget_variance.v1"


# --------------------------------------------------------------------------- #
# PREFLIGHT / CAPABILITY layer
# --------------------------------------------------------------------------- #
# Semantic field <-> deterministic column convention for this workflow:
#   join keys   : fund, account_code (=> "account"), department, object
#   amount       : budget_amount (budget) / actual_amount (actuals)
# At minimum we require ONE shared join key (``fund``) plus the amount column in
# BOTH files; the remaining join keys are optional refinements of the join.
CAPABILITY = CapabilitySpec(
    workflow_type="budget_variance",
    required_inputs=["budget", "actuals"],
    optional_inputs=["chart_of_accounts"],
    accepted_file_types={"*": ["csv", "xlsx"]},
    required_semantic_columns={
        "budget": ["fund", "amount"],
        "actuals": ["fund", "amount"],
    },
    optional_semantic_columns={
        "budget": ["account_code", "department", "object"],
        "actuals": ["account_code", "department", "object"],
        "chart_of_accounts": ["fund", "account_code", "department", "object"],
    },
    # No date columns in this workflow; amount confidence still gates parseable $.
    min_amount_confidence=0.8,
    supported_patterns=[
        "join_by_fund_account_department_object",
        "dollar_variance",
        "pct_variance",
        "threshold_flags",
        "budget_only_actual_only_missing",
    ],
    partially_supported_patterns=[
        "possible_account_rollup",
        "possible_budget_basis_mismatch",
    ],
    unsupported_patterns=[
        "account_rollup_hierarchies",
        "budget_basis_conversion_annual_vs_ytd",
    ],
    notes=(
        "Joins budget vs actuals on the shared key columns and computes "
        "dollar/percentage variance with threshold flags. Rollup hierarchies "
        "and budget-basis conversion are NOT performed; when detected the run "
        "is PARTIAL and flags the condition for human review."
    ),
)

# Tolerances for the conservative domain detectors.
_ROLLUP_REL_TOL = Decimal("0.005")      # 0.5% match between a row and a subtotal
_ROLLUP_MIN_CHILDREN = 2                # a subtotal must cover >= 2 detail rows
_BASIS_ROWCOUNT_RATIO = 3.0            # one side >= 3x the other's row count
_BASIS_MAGNITUDE_RATIO = Decimal("8")  # totals differ by >= 8x (order-of-mag)


def _semantic_column(
    mappings: dict[str, dict[str, ColumnMapping]],
    input_key: str,
    semantic: str,
) -> Optional[str]:
    """Return the mapped column for ``semantic`` on ``input_key`` (or None)."""
    m = mappings.get(input_key, {}).get(semantic)
    if m is None or m.mapped_column is None:
        return None
    return m.mapped_column


def _amounts_from_input(
    inputs: dict, input_key: str, amount_col: Optional[str]
) -> list[Decimal]:
    """Load the parseable amount values for ``input_key`` (positional order).

    Re-reads the raw input (path or ParsedTable) deterministically; returns only
    the non-blank, parseable amounts. Returns ``[]`` when the column is unknown
    or the input cannot be read.
    """
    raw = inputs.get(input_key)
    if raw is None or amount_col is None:
        return []
    try:
        parsed = _as_parsed(raw, input_key)
        df = normalize_columns(parsed.dataframe)
    except Exception:
        return []
    if amount_col not in df.columns:
        return []
    out: list[Decimal] = []
    for v in df[amount_col].tolist():
        d = parse_amount(v)
        if d is not None:
            out.append(d)
    return out


def _has_subtotal_rollup(amounts: list[Decimal]) -> bool:
    """Conservative rollup signal.

    True when some row's amount equals (within a small relative tolerance) the
    sum of >= ``_ROLLUP_MIN_CHILDREN`` of the OTHER rows. That strongly suggests
    a parent/subtotal line was concatenated into the detail rows (a rollup the
    deterministic join cannot net out). Only large, non-zero candidates are
    considered to avoid coincidental small-number matches.
    """
    vals = [a for a in amounts if a != 0]
    n = len(vals)
    if n < _ROLLUP_MIN_CHILDREN + 1:
        return False
    total = sum(vals)
    for i, candidate in enumerate(vals):
        if candidate == 0:
            continue
        others = total - candidate
        # candidate could be the subtotal of all the others...
        if n - 1 >= _ROLLUP_MIN_CHILDREN and others != 0:
            rel = abs(candidate - others) / abs(candidate)
            if rel <= _ROLLUP_REL_TOL:
                return True
        # ...or of a contiguous block immediately following it (typical layout:
        # a subtotal row preceding/following its detail children).
        run = Decimal("0")
        children = 0
        for j in range(n):
            if j == i:
                continue
            run += vals[j]
            children += 1
            if children >= _ROLLUP_MIN_CHILDREN and candidate != 0:
                if abs(candidate - run) / abs(candidate) <= _ROLLUP_REL_TOL:
                    return True
    return False


def detect_conditions(
    profiles: dict[str, FileProfile],
    mappings: dict[str, dict[str, ColumnMapping]],
    inputs: dict,
    config: Optional[dict],
) -> list[PreflightFinding]:
    """Domain-specific unsupported-condition detection for budget variance.

    Emits (all NON-blocking -> PARTIAL, never FAIL):

    * ``POSSIBLE_ACCOUNT_ROLLUP`` — a side appears to embed subtotal/parent rows
      whose amount equals the sum of finer detail rows (rollup hierarchy the
      deterministic join does not net out).
    * ``POSSIBLE_BUDGET_BASIS_MISMATCH`` — budget and actuals appear to be on
      different bases: one side has far fewer (aggregated) rows, or the total
      magnitudes differ by an order suggesting annual-vs-YTD.

    Conservative by design: only flags on real signal so a clean demo PASSes.
    Returns ``[]`` when nothing applies.
    """
    findings: list[PreflightFinding] = []

    b_prof = profiles.get("budget")
    a_prof = profiles.get("actuals")
    if b_prof is None or a_prof is None or not b_prof.present or not a_prof.present:
        return findings  # generic engine already handles missing files.

    b_amount_col = _semantic_column(mappings, "budget", "amount")
    a_amount_col = _semantic_column(mappings, "actuals", "amount")
    b_amounts = _amounts_from_input(inputs, "budget", b_amount_col)
    a_amounts = _amounts_from_input(inputs, "actuals", a_amount_col)

    # --- POSSIBLE_ACCOUNT_ROLLUP ---------------------------------------- #
    if b_amount_col and _has_subtotal_rollup(b_amounts):
        findings.append(
            PreflightFinding(
                code=PreflightConditionCode.POSSIBLE_ACCOUNT_ROLLUP,
                severity=Severity.MEDIUM,
                message=(
                    "Budget rows appear to include a rollup/subtotal line whose "
                    "amount equals the sum of finer detail accounts. The "
                    "deterministic join treats every row as a leaf account, so a "
                    "subtotal would be double-counted."
                ),
                affected_input="budget",
                affected_column=b_amount_col,
                suggested_action=(
                    "Remove subtotal/parent rows from the budget file (keep only "
                    "leaf accounts), or split the rollup hierarchy out for human "
                    "review before running variance."
                ),
                blocks_run=False,
            )
        )
    if a_amount_col and _has_subtotal_rollup(a_amounts):
        findings.append(
            PreflightFinding(
                code=PreflightConditionCode.POSSIBLE_ACCOUNT_ROLLUP,
                severity=Severity.MEDIUM,
                message=(
                    "Actuals rows appear to include a rollup/subtotal line whose "
                    "amount equals the sum of finer detail accounts. The "
                    "deterministic join treats every row as a leaf account, so a "
                    "subtotal would be double-counted."
                ),
                affected_input="actuals",
                affected_column=a_amount_col,
                suggested_action=(
                    "Remove subtotal/parent rows from the actuals file (keep only "
                    "leaf accounts), or split the rollup hierarchy out for human "
                    "review before running variance."
                ),
                blocks_run=False,
            )
        )

    # --- POSSIBLE_BUDGET_BASIS_MISMATCH --------------------------------- #
    b_rows = len(b_amounts)
    a_rows = len(a_amounts)
    basis_reason: Optional[str] = None
    if b_rows and a_rows:
        hi, lo = max(b_rows, a_rows), min(b_rows, a_rows)
        if lo > 0 and hi / lo >= _BASIS_ROWCOUNT_RATIO:
            fewer = "budget" if b_rows < a_rows else "actuals"
            basis_reason = (
                f"{fewer} has {lo} account rows vs {hi} on the other side "
                f"(>= {_BASIS_ROWCOUNT_RATIO:g}x), suggesting one side is "
                "aggregated to a coarser level."
            )

    if basis_reason is None and b_amounts and a_amounts:
        b_total = abs(sum(b_amounts))
        a_total = abs(sum(a_amounts))
        if b_total > 0 and a_total > 0:
            hi_t, lo_t = max(b_total, a_total), min(b_total, a_total)
            if lo_t > 0 and hi_t / lo_t >= _BASIS_MAGNITUDE_RATIO:
                basis_reason = (
                    f"total magnitudes differ by ~{(hi_t / lo_t):.0f}x "
                    f"(budget {b_total}, actuals {a_total}), suggesting "
                    "different bases (e.g. annual budget vs year-to-date actuals)."
                )

    if basis_reason is not None:
        findings.append(
            PreflightFinding(
                code=PreflightConditionCode.POSSIBLE_BUDGET_BASIS_MISMATCH,
                severity=Severity.MEDIUM,
                message=(
                    "Budget and actuals may be on different bases: " + basis_reason
                ),
                affected_input="actuals",
                suggested_action=(
                    "Confirm budget and actuals cover the same period and level "
                    "of detail (e.g. both annual and both at account level) "
                    "before relying on the variance; basis conversion is not "
                    "performed automatically."
                ),
                blocks_run=False,
            )
        )

    return findings


@dataclass
class VarianceThresholds:
    dollar_threshold: Decimal = DEFAULT_DOLLAR_THRESHOLD
    pct_threshold: Decimal = DEFAULT_PCT_THRESHOLD

    @classmethod
    def from_config(cls, cfg: Any) -> "VarianceThresholds":
        """Build thresholds from a dict or a path to a JSON file (or None)."""
        if cfg is None:
            return cls()
        if isinstance(cfg, (str, Path)):
            cfg = json.loads(Path(cfg).read_text(encoding="utf-8"))
        return cls(
            dollar_threshold=Decimal(str(cfg.get("dollar_threshold", DEFAULT_DOLLAR_THRESHOLD))),
            pct_threshold=Decimal(str(cfg.get("pct_threshold", DEFAULT_PCT_THRESHOLD))),
        )


# --------------------------------------------------------------------------- #
# Result containers
# --------------------------------------------------------------------------- #
@dataclass
class DeterministicOutput:
    """What the deterministic stage produces. ``summary`` + ``findings`` mirror
    the shape the shared prompt builder expects."""

    findings: list[DeterministicFinding] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    result_tables: dict[str, pd.DataFrame] = field(default_factory=dict)


@dataclass
class WorkflowResult:
    workflow_type: str
    deterministic: DeterministicOutput
    llm_response: Optional[LLMResponse] = None
    validation: Optional[ValidationResult] = None


# --------------------------------------------------------------------------- #
# Mock LLM provider (default path: no API key, no internet)
# --------------------------------------------------------------------------- #
class MockLLMProvider(_CoreMockLLMProvider):
    """Deterministic mock for budget variance.

    Subclasses the canonical ``src.llm.provider.MockLLMProvider`` (sharing its
    method surface and offline contract) and overrides ``_build`` to add
    variance-specific output (follow-up questions, themes) that only references
    the FLAGGED variance findings. Derived ONLY from the deterministic findings;
    it never calculates a variance or invents data.
    """

    # The prompt embeds the findings as JSON; parse them back out so the mock
    # only ever references real finding/source ids and never fabricates numbers.
    def _build(self, prompt: str) -> dict:
        findings = _extract_findings_from_prompt(prompt)
        flagged = [f for f in findings if f.get("finding_type") == FindingType.VARIANCE.value]
        ref_ids: list[str] = []
        for f in findings:
            ref_ids.extend(f.get("source_row_ids", []))

        categorized = []
        for f in flagged:
            categorized.append(
                {
                    "category": f.get("severity", "medium"),
                    "description": "Flagged variance for staff explanation: "
                    + f.get("description", ""),
                    "referenced_source_rows": f.get("source_row_ids", []),
                }
            )
        # Themes/keys appearing in budget-only / actual-only / missing findings.
        themes = sorted(
            {
                f.get("finding_type")
                for f in findings
                if f.get("finding_type")
                in (
                    FindingType.MISSING_ACCOUNT.value,
                    "budget_only",
                    "actual_only",
                )
            }
        )
        return {
            "summary": (
                f"{len(flagged)} budget line(s) exceed the configured variance "
                "thresholds and may need a staff explanation. See referenced "
                "source rows for each item."
            ),
            "categorized_exceptions": categorized,
            "referenced_source_rows": sorted(set(ref_ids)),
            "suggested_review_steps": [
                "Confirm each flagged variance with the responsible department head.",
                "Request a written explanation for variances above threshold.",
                "Verify budget-only and actual-only accounts are intentional.",
            ],
            "follow_up_questions": [
                f"What drove the variance on {f.get('description', '')}?"
                for f in flagged
            ],
            "themes": themes,
            "draft_memo": (
                "DRAFT (for human review): The variance review identified "
                f"{len(flagged)} item(s) above threshold. This draft must be "
                "reviewed and approved by finance staff before use."
            ),
        }


# --------------------------------------------------------------------------- #
# Deterministic helpers
# --------------------------------------------------------------------------- #
def _as_parsed(table_or_path: Any, default_name: str) -> ParsedTable:
    if isinstance(table_or_path, ParsedTable):
        return table_or_path
    return load_csv(table_or_path, table_name=default_name)


def _prepared(parsed: ParsedTable) -> tuple[pd.DataFrame, list[str], str]:
    """Normalize columns and return (df, key_columns_present, table_name)."""
    df = normalize_columns(parsed.dataframe).copy()
    df = df.reset_index(drop=True)
    keys = [k for k in KEY_COLUMNS if k in df.columns]
    return df, keys, parsed.table_name


def _key_tuple(row: pd.Series, keys: list[str]) -> tuple:
    return tuple(str(row[k]).strip() for k in keys)


def _amount_column(df: pd.DataFrame, preferred: list[str]) -> Optional[str]:
    for c in preferred:
        if c in df.columns:
            return c
    return None


# Semantic join-key field <-> local key-column name. The preflight layer names
# the account key 'account_code' (a SEMANTIC_SYNONYMS key); locally it is the
# 'account' column. Fund/department/object share their names.
_KEY_SEMANTIC_TO_COLUMN = {
    "fund": "fund",
    "account_code": "account",
    "department": "department",
    "object": "object",
}


def _override_key_columns(
    column_mappings: Optional[dict[str, dict[str, str]]],
    input_key: str,
    df: pd.DataFrame,
) -> dict[str, str]:
    """Map local key-column name -> overridden source column for one input."""
    out: dict[str, str] = {}
    if not column_mappings:
        return out
    sem_map = column_mappings.get(input_key, {})
    for semantic, local_col in _KEY_SEMANTIC_TO_COLUMN.items():
        override = sem_map.get(semantic)
        if override:
            snake = to_snake_case(override)
            if snake in df.columns:
                out[local_col] = snake
    return out


def _resolve_join_keys(
    column_mappings: Optional[dict[str, dict[str, str]]],
    b_df: pd.DataFrame,
    b_keys: list[str],
    a_df: pd.DataFrame,
    a_keys: list[str],
) -> list[str]:
    """Determine the join-key columns, honoring approved overrides.

    With no override this is the intersection of available KEY_COLUMNS (current
    behavior). When an override renames a key column on BOTH sides, the frame is
    aliased so the canonical local key name (e.g. 'account') points at the
    overridden source column, keeping the join logic unchanged downstream.
    """
    b_over = _override_key_columns(column_mappings, "budget", b_df)
    a_over = _override_key_columns(column_mappings, "actuals", a_df)
    # Apply renames in-place so the canonical local key name resolves the data.
    for local_col, src in b_over.items():
        if src != local_col:
            b_df[local_col] = b_df[src]
            if local_col not in b_keys:
                b_keys.append(local_col)
    for local_col, src in a_over.items():
        if src != local_col:
            a_df[local_col] = a_df[src]
            if local_col not in a_keys:
                a_keys.append(local_col)
    return [k for k in KEY_COLUMNS if k in b_keys and k in a_keys]


def _source_ref(
    parsed_file_id: str, table_name: str, row_index: int, row: pd.Series
) -> SourceRowRef:
    cols = list(row.index)
    return SourceRowRef(
        file_id=parsed_file_id,
        table_name=table_name,
        row_index=int(row_index),
        column_names=cols,
        source_values={c: ("" if pd.isna(row[c]) else row[c]) for c in cols},
    )


def _dec(v: Any) -> Optional[Decimal]:
    return parse_amount(v)


# --------------------------------------------------------------------------- #
# Core deterministic analysis
# --------------------------------------------------------------------------- #
def _mapped(
    column_mappings: Optional[dict[str, dict[str, str]]],
    input_key: str,
    semantic: str,
) -> Optional[str]:
    """Look up a human/approved column override (snake_cased) or None."""
    if not column_mappings:
        return None
    col = column_mappings.get(input_key, {}).get(semantic)
    return to_snake_case(col) if col else None


def analyze(
    budget: Any,
    actuals: Any,
    *,
    chart_of_accounts: Any = None,
    thresholds: Any = None,
    column_mappings: Optional[dict[str, dict[str, str]]] = None,
) -> DeterministicOutput:
    """Run the full deterministic variance analysis.

    Parameters accept either a ``ParsedTable`` or a path to a CSV.

    ``column_mappings`` is an optional preflight override mapping
    ``input_key -> {semantic_name: column}`` (e.g.
    ``{"budget": {"amount": "approved_budget", "account_code": "gl"}}``). When
    provided, the mapped columns are used instead of auto-detection; ``None``
    keeps the current auto-detect behavior unchanged. Source-row indices are
    preserved regardless of the mapping used.
    """
    thr = (
        thresholds
        if isinstance(thresholds, VarianceThresholds)
        else VarianceThresholds.from_config(thresholds)
    )

    b_parsed = _as_parsed(budget, "budget")
    a_parsed = _as_parsed(actuals, "actuals")
    b_df, b_keys, b_name = _prepared(b_parsed)
    a_df, a_keys, a_name = _prepared(a_parsed)

    # Join keys: honor an explicit override (the semantic 'account_code' maps to
    # the local 'account' key column) else use the intersection of available
    # key columns for a stable join.
    keys = _resolve_join_keys(column_mappings, b_df, b_keys, a_df, a_keys)
    if not keys:
        raise ValueError(
            "No common join key columns among "
            f"{KEY_COLUMNS}; budget has {b_keys}, actuals has {a_keys}."
        )

    b_amount_col = _mapped(column_mappings, "budget", "amount") or _amount_column(
        b_df, ["budget_amount", "budget", "amount", "budgeted"]
    )
    a_amount_col = _mapped(column_mappings, "actuals", "amount") or _amount_column(
        a_df, ["actual_amount", "actual", "amount", "actuals"]
    )
    if (
        b_amount_col is None
        or a_amount_col is None
        or b_amount_col not in b_df.columns
        or a_amount_col not in a_df.columns
    ):
        raise ValueError("Could not locate budget/actual amount columns.")

    # Index rows by key.
    b_by_key: dict[tuple, tuple[int, pd.Series]] = {}
    for i, row in b_df.iterrows():
        b_by_key[_key_tuple(row, keys)] = (i, row)
    a_by_key: dict[tuple, tuple[int, pd.Series]] = {}
    for i, row in a_df.iterrows():
        a_by_key[_key_tuple(row, keys)] = (i, row)

    findings: list[DeterministicFinding] = []
    variance_rows: list[dict] = []
    flagged_rows: list[dict] = []

    b_keys_set = set(b_by_key)
    a_keys_set = set(a_by_key)

    # --- Joined lines (present in both): compute variances --------------- #
    for key in sorted(b_keys_set & a_keys_set):
        bi, brow = b_by_key[key]
        ai, arow = a_by_key[key]
        budget_amt = _dec(brow[b_amount_col]) or Decimal("0")
        actual_amt = _dec(arow[a_amount_col]) or Decimal("0")
        dollar_var = actual_amt - budget_amt
        if budget_amt != 0:
            pct_var: Optional[Decimal] = (dollar_var / budget_amt) * Decimal("100")
        else:
            pct_var = None

        flagged = abs(dollar_var) >= thr.dollar_threshold or (
            pct_var is not None and abs(pct_var) >= thr.pct_threshold
        )
        src_b = _source_ref(b_parsed.file_id, b_name, bi, brow)
        src_a = _source_ref(a_parsed.file_id, a_name, ai, arow)
        key_map = dict(zip(keys, key))
        record = {
            **key_map,
            "budget_amount": float(budget_amt),
            "actual_amount": float(actual_amt),
            "dollar_variance": float(dollar_var),
            "pct_variance": (float(pct_var) if pct_var is not None else None),
            "flagged": flagged,
        }
        variance_rows.append(record)

        if flagged:
            flagged_rows.append(record)
            severity = (
                Severity.HIGH
                if abs(dollar_var) >= thr.dollar_threshold
                else Severity.MEDIUM
            )
            reasons = []
            if abs(dollar_var) >= thr.dollar_threshold:
                reasons.append(
                    f"dollar variance {dollar_var} exceeds {thr.dollar_threshold}"
                )
            if pct_var is not None and abs(pct_var) >= thr.pct_threshold:
                reasons.append(
                    f"percentage variance {pct_var:.2f}% exceeds {thr.pct_threshold}%"
                )
            findings.append(
                DeterministicFinding(
                    finding_type=FindingType.VARIANCE,
                    severity=severity,
                    description=(
                        f"Variance flagged for {key_map}: "
                        f"budget {budget_amt}, actual {actual_amt} "
                        f"({'; '.join(reasons)})."
                    ),
                    source_rows=[src_b, src_a],
                    computed_values={
                        "key": key_map,
                        "budget_amount": str(budget_amt),
                        "actual_amount": str(actual_amt),
                        "dollar_variance": str(dollar_var),
                        "pct_variance": (
                            f"{pct_var:.4f}" if pct_var is not None else None
                        ),
                        "dollar_threshold": str(thr.dollar_threshold),
                        "pct_threshold": str(thr.pct_threshold),
                    },
                    rule_used="abs(dollar_var) >= dollar_threshold OR abs(pct_var) >= pct_threshold",
                    requires_human_review=True,
                )
            )

    # --- Budget-only (in budget, not in actuals) ------------------------- #
    for key in sorted(b_keys_set - a_keys_set):
        bi, brow = b_by_key[key]
        key_map = dict(zip(keys, key))
        findings.append(
            DeterministicFinding(
                finding_type=FindingType.MISSING_ACCOUNT,
                severity=Severity.MEDIUM,
                description=f"Budget-only account (no actuals recorded): {key_map}.",
                source_rows=[_source_ref(b_parsed.file_id, b_name, bi, brow)],
                computed_values={
                    "key": key_map,
                    "subtype": "budget_only",
                    "budget_amount": str(_dec(brow[b_amount_col]) or Decimal("0")),
                },
                rule_used="key in budget AND key not in actuals",
                requires_human_review=True,
            )
        )

    # --- Actual-only (in actuals, not in budget) ------------------------- #
    for key in sorted(a_keys_set - b_keys_set):
        ai, arow = a_by_key[key]
        key_map = dict(zip(keys, key))
        findings.append(
            DeterministicFinding(
                finding_type=FindingType.MISSING_ACCOUNT,
                severity=Severity.MEDIUM,
                description=f"Actual-only (new) account not in budget: {key_map}.",
                source_rows=[_source_ref(a_parsed.file_id, a_name, ai, arow)],
                computed_values={
                    "key": key_map,
                    "subtype": "actual_only",
                    "actual_amount": str(_dec(arow[a_amount_col]) or Decimal("0")),
                },
                rule_used="key in actuals AND key not in budget",
                requires_human_review=True,
            )
        )

    # --- Missing accounts (in chart of accounts, in neither file) -------- #
    missing_count = 0
    if chart_of_accounts is not None:
        coa_parsed = _as_parsed(chart_of_accounts, "chart_of_accounts")
        coa_df, coa_keys, coa_name = _prepared(coa_parsed)
        coa_join = [k for k in keys if k in coa_keys]
        if coa_join:
            present = b_keys_set | a_keys_set
            for i, row in coa_df.iterrows():
                ckey = _key_tuple(row, coa_join)
                # Compare on the same key columns used for present-set membership.
                if coa_join == keys:
                    full_key = ckey
                else:
                    # Pad to full key positions not available -> cannot match; skip.
                    full_key = ckey
                if full_key not in present:
                    missing_count += 1
                    key_map = dict(zip(coa_join, ckey))
                    findings.append(
                        DeterministicFinding(
                            finding_type=FindingType.MISSING_ACCOUNT,
                            severity=Severity.HIGH,
                            description=(
                                "Account in chart of accounts is missing from "
                                f"both budget and actuals: {key_map}."
                            ),
                            source_rows=[
                                _source_ref(coa_parsed.file_id, coa_name, i, row)
                            ],
                            computed_values={"key": key_map, "subtype": "missing_account"},
                            rule_used="key in chart_of_accounts AND key not in (budget UNION actuals)",
                            requires_human_review=True,
                        )
                    )

    # --- Grouping by fund and department --------------------------------- #
    var_df = pd.DataFrame(variance_rows)
    by_fund = _group_totals(var_df, "fund")
    by_dept = _group_totals(var_df, "department")

    summary = {
        "join_keys": keys,
        "joined_lines": len(variance_rows),
        "flagged_variances": len(flagged_rows),
        "budget_only": len(b_keys_set - a_keys_set),
        "actual_only": len(a_keys_set - b_keys_set),
        "missing_accounts": missing_count,
        "dollar_threshold": str(thr.dollar_threshold),
        "pct_threshold": str(thr.pct_threshold),
        "total_budget": float(sum(r["budget_amount"] for r in variance_rows)) if variance_rows else 0.0,
        "total_actual": float(sum(r["actual_amount"] for r in variance_rows)) if variance_rows else 0.0,
    }

    result_tables = {
        "variances": var_df,
        "flagged_variances": pd.DataFrame(flagged_rows),
        "by_fund": by_fund,
        "by_department": by_dept,
    }
    return DeterministicOutput(
        findings=findings, summary=summary, result_tables=result_tables
    )


def _df_to_markdown(df: pd.DataFrame) -> str:
    """Render a DataFrame as a GitHub markdown table (no external deps)."""
    if df.empty:
        return "_none_"
    cols = list(df.columns)
    header = "| " + " | ".join(str(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = [
        "| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |"
        for row in df.itertuples(index=False, name=None)
    ]
    return "\n".join([header, sep, *body])


def _group_totals(var_df: pd.DataFrame, col: str) -> pd.DataFrame:
    if var_df.empty or col not in var_df.columns:
        return pd.DataFrame(
            columns=[col, "budget_amount", "actual_amount", "dollar_variance"]
        )
    grouped = (
        var_df.groupby(col)[["budget_amount", "actual_amount", "dollar_variance"]]
        .sum()
        .reset_index()
    )
    return grouped


# --------------------------------------------------------------------------- #
# Prompt + LLM + validation
# --------------------------------------------------------------------------- #
_GUARDRAILS = (
    "You are a finance-review assistant for a small municipal finance team.\n"
    "STRICT RULES: you may ONLY explain, summarize, draft, classify and flag. "
    "You MUST NOT calculate variances or invent accounts, funds, amounts, or "
    "dates. Use only numbers and source-row ids present in the data below. "
    "Every claim must cite source_row ids. All output is a DRAFT for human "
    "review.\n"
)


def build_prompt(det: DeterministicOutput) -> str:
    rows = []
    for f in det.findings:
        rows.append(
            {
                "finding_id": f.finding_id,
                "finding_type": f.finding_type.value,
                "severity": f.severity.value,
                "description": f.description,
                "computed_values": f.computed_values,
                "source_row_ids": [
                    f"{s.table_name}:{s.row_index}" for s in f.source_rows
                ],
            }
        )
    return (
        _GUARDRAILS
        + "\nWORKFLOW: Budget-to-actual variance review.\n"
        + "TASK: draft variance commentary, translate the variance findings to "
        "plain English, identify which variances need staff explanation, "
        "suggest follow-up questions for department heads, and group variances "
        "into themes. Do NOT calculate any variance.\n\n"
        + f"DETERMINISTIC SUMMARY:\n{json.dumps(det.summary, default=str, indent=2)}\n\n"
        + f"DETERMINISTIC FINDINGS:\n{json.dumps(rows, default=str, indent=2)}\n\n"
        + "Return JSON with keys: summary, categorized_exceptions "
        "(list of {category, description, referenced_source_rows}), "
        "referenced_source_rows, suggested_review_steps, follow_up_questions, "
        "themes, draft_memo.\n"
    )


def run_llm(det: DeterministicOutput, provider: Any = None) -> LLMResponse:
    """Call the provider (or the built-in mock) and wrap as an LLMResponse."""
    provider = provider or MockLLMProvider()
    prompt = build_prompt(det)
    raw = provider.generate_structured_response(prompt, schema=None)
    referenced = list(raw.get("referenced_source_rows", []))
    return LLMResponse(
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        model_provider=getattr(provider, "model_provider", "mock"),
        model_name=getattr(provider, "model_name", "mock-llm"),
        response_json=raw,
        referenced_source_rows=referenced,
    )


def validate_llm(det: DeterministicOutput, llm: LLMResponse) -> ValidationResult:
    """Thin wrapper: the canonical validator lives in ``src.core.validation``.

    Budget variance receives an ``LLMResponse`` (not a bare dict). The
    LLMResponse's ``referenced_source_rows`` are merged into the response JSON so
    the canonical validator sees every cited id (the response_json and the
    LLMResponse field can differ). Budget historically treated missing
    references as a WARNING, so that knob is set here.
    """
    response_json = dict(llm.response_json)
    merged_refs = list(
        dict.fromkeys(
            list(response_json.get("referenced_source_rows", []) or [])
            + list(llm.referenced_source_rows)
        )
    )
    response_json["referenced_source_rows"] = merged_refs
    return _core_validate_llm_output(
        response_json,
        det,
        require_references=True,
        missing_references_is_error=False,
    )


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #
def export_artifacts(
    result: WorkflowResult,
    out_dir: str | Path,
    *,
    run_id: Optional[str] = None,
    audit_events: Optional[list[dict]] = None,
) -> list[ExportArtifact]:
    """Write the spec-required export artifacts and return their manifests.

    Files: variance_summary.md, flagged_variances.csv,
    variance_commentary_draft.md, validation_report.json, audit_log.json.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    det = result.deterministic
    artifacts: list[ExportArtifact] = []

    # File-writing + hashing go through the shared src.core.exports primitives.
    def _write(name: str, text: str, atype: ArtifactType) -> None:
        if atype == ArtifactType.MARKDOWN:
            artifacts.append(write_markdown(out / name, text, run_id=run_id))
        else:
            artifacts.append(write_json(out / name, text, run_id=run_id))

    def _write_csv(name: str, df: pd.DataFrame) -> None:
        artifacts.append(write_csv(out / name, df, run_id=run_id))

    # variance_summary.md
    s = det.summary
    lines = [
        "# Budget-to-Actual Variance Summary",
        "",
        f"- Join keys: {', '.join(s.get('join_keys', []))}",
        f"- Joined lines: {s.get('joined_lines', 0)}",
        f"- Flagged variances: {s.get('flagged_variances', 0)}",
        f"- Budget-only accounts: {s.get('budget_only', 0)}",
        f"- Actual-only (new) accounts: {s.get('actual_only', 0)}",
        f"- Missing accounts (in chart of accounts only): {s.get('missing_accounts', 0)}",
        f"- Dollar threshold: {s.get('dollar_threshold')}",
        f"- Percentage threshold: {s.get('pct_threshold')}%",
        "",
        "## Totals by fund",
        _df_to_markdown(det.result_tables["by_fund"]),
        "",
        "## Totals by department",
        _df_to_markdown(det.result_tables["by_department"]),
        "",
        "_All figures computed deterministically. AI commentary is a draft for "
        "human review._",
    ]
    _write("variance_summary.md", "\n".join(lines), ArtifactType.MARKDOWN)

    # flagged_variances.csv
    _write_csv("flagged_variances.csv", det.result_tables["flagged_variances"])

    # variance_commentary_draft.md
    commentary = ["# Variance Commentary (DRAFT — for human review)", ""]
    if result.llm_response is not None:
        rj = result.llm_response.response_json
        commentary.append(rj.get("summary", ""))
        commentary.append("")
        if rj.get("categorized_exceptions"):
            commentary.append("## Items needing staff explanation")
            for ce in rj["categorized_exceptions"]:
                refs = ", ".join(ce.get("referenced_source_rows", []))
                commentary.append(
                    f"- [{ce.get('category', '')}] {ce.get('description', '')} "
                    f"(source rows: {refs})"
                )
            commentary.append("")
        if rj.get("follow_up_questions"):
            commentary.append("## Suggested follow-up questions")
            for q in rj["follow_up_questions"]:
                commentary.append(f"- {q}")
            commentary.append("")
        if rj.get("draft_memo"):
            commentary.append("## Draft memo")
            commentary.append(rj["draft_memo"])
    else:
        commentary.append("_No LLM commentary generated._")
    _write(
        "variance_commentary_draft.md",
        "\n".join(commentary),
        ArtifactType.MARKDOWN,
    )

    # validation_report.json
    val = result.validation.model_dump() if result.validation else {}
    _write(
        "validation_report.json",
        json.dumps(val, default=str, indent=2),
        ArtifactType.JSON,
    )

    # audit_log.json
    _write(
        "audit_log.json",
        json.dumps(audit_events or [], default=str, indent=2),
        ArtifactType.JSON,
    )

    return artifacts


# --------------------------------------------------------------------------- #
# Workflow class + registration
# --------------------------------------------------------------------------- #
@register_workflow("budget_variance")
class BudgetVarianceWorkflow:
    """Budget-to-actual variance workflow conforming to the local Workflow
    protocol (deterministic analysis -> LLM assist -> validation -> export)."""

    workflow_type = "budget_variance"
    prompt_template_version = PROMPT_TEMPLATE_VERSION

    # ---- protocol methods ---- #
    def run_deterministic(self, inputs: dict) -> DeterministicOutput:
        return analyze(
            inputs["budget"],
            inputs["actuals"],
            chart_of_accounts=inputs.get("chart_of_accounts"),
            thresholds=inputs.get("thresholds"),
            column_mappings=inputs.get("column_mappings"),
        )

    def build_llm_output(
        self, det: DeterministicOutput, provider: Any = None
    ) -> LLMResponse:
        return run_llm(det, provider)

    def validate_llm(
        self, det: DeterministicOutput, llm: LLMResponse
    ) -> ValidationResult:
        return validate_llm(det, llm)

    def export_artifacts(
        self,
        result: WorkflowResult,
        out_dir: str | Path,
        *,
        run_id: Optional[str] = None,
        audit_events: Optional[list[dict]] = None,
    ) -> list[ExportArtifact]:
        return export_artifacts(
            result, out_dir, run_id=run_id, audit_events=audit_events
        )

    # ---- convenience end-to-end runner (mock by default) ---- #
    def run(self, inputs: dict, provider: Any = None) -> WorkflowResult:
        det = self.run_deterministic(inputs)
        llm = self.build_llm_output(det, provider)
        val = self.validate_llm(det, llm)
        return WorkflowResult(
            workflow_type=self.workflow_type,
            deterministic=det,
            llm_response=llm,
            validation=val,
        )
