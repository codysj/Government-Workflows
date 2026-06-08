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
    DeterministicFinding,
    ExportArtifact,
    FindingType,
    LLMResponse,
    ParsedTable,
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
from src.normalize.cleaning import normalize_columns, parse_amount, parse_date
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
    return load_csv(table_or_path, table_name=default_name)


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
def reconcile(
    bank: Any,
    ledger: Any,
    *,
    config: ReconciliationConfig | None = None,
) -> ReconciliationOutput:
    """Run the full deterministic reconciliation.

    ``bank`` / ``ledger`` accept a ``ParsedTable`` or a path to a CSV.
    """
    config = config or ReconciliationConfig()

    b_parsed = _as_parsed(bank, "bank")
    l_parsed = _as_parsed(ledger, "ledger")
    b_df = normalize_columns(b_parsed.dataframe).reset_index(drop=True)
    l_df = normalize_columns(l_parsed.dataframe).reset_index(drop=True)

    b_amt = _first_col(b_df, _AMOUNT_COLUMNS)
    b_date = _first_col(b_df, _DATE_COLUMNS)
    l_amt = _first_col(l_df, _AMOUNT_COLUMNS)
    l_date = _first_col(l_df, _DATE_COLUMNS)
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
        return _row_ref(b_parsed.file_id, "bank", idx, b_df.loc[idx])

    def ledger_ref(idx: int) -> SourceRowRef:
        return _row_ref(l_parsed.file_id, "ledger", idx, l_df.loc[idx])

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
    det = reconcile(inputs["bank"], inputs["ledger"], config=cfg)
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
