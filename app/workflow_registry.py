"""Workflow registry + uniform run adapter for the Streamlit MVP UI.

This module is the ONLY place that knows how to drive each concrete workflow
module's (heterogeneous) entry point. It exposes a single uniform pipeline,
``run_workflow(...)``, that:

  1. creates a run-ledger entry (WorkflowRun),
  2. records input-file metadata + audit events,
  3. invokes the selected workflow (mock LLM by default),
  4. persists findings / LLM response / validation result,
  5. generates the spec-required export artifacts,
  6. writes audit events at every stage,

returning a plain dict the UI renders. It deliberately contains NO Streamlit
imports so it is unit-testable and so the UI layer stays separated from core
logic (spec sections 0.3 / 1.3). Provider-specific code never leaks into the
``src/workflows`` modules — only the mock default is used here unless a real
provider object is injected.

Each workflow module was built by a separate agent with a slightly different
public surface (see the integration notes), so this adapter pins each one
explicitly rather than assuming a single shared protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from src.core import preflight as preflight_engine
from src.core.audit_log import AuditLog
from src.core.review_packet import (
    generate_failed_preflight_packet,
    generate_review_packet,
)
from src.core.run_ledger import RunLedger
from src.core.schemas import (
    DeterministicFinding,
    FindingType,
    LLMResponse,
    PreflightReport,
    PreflightStatus,
    RetentionCategory,
    RunStatus,
    Severity,
    SourceRowRef,
    ValidationResult,
    WorkflowRun,
    make_id,
)
from src.ingest.csv_loader import load_csv, to_input_file
from src.ingest.presets import normalize_csv
from src.workflows import (
    ap_duplicate_review,
    bank_reconciliation,
    budget_variance,
    freeform,
    je_upload_prep,
    po_invoice_review,
    report_review,
    transaction_search,
)

# Source-format choices for the per-upload "source format" selector. Each is
# (label shown to staff, preset name or None). ``None`` means standard /
# auto-detect (no aliasing). Presets are generic, local-file column-alias maps
# (see src/ingest/presets.py) — NOT vendor integrations.
SOURCE_FORMAT_CHOICES: tuple[tuple[str, Optional[str]], ...] = (
    ("Standard / auto-detect", None),
    ("Generic ERP export", "generic_erp"),
    ("Tyler/Munis-style export", "tyler_munis_style"),
    ("OpenGov-style export", "opengov_style"),
    ("Chart of accounts export", "chart_of_accounts"),
)

# Repo root (this file lives in app/).
REPO_ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC_DIR = REPO_ROOT / "data" / "synthetic"


# --------------------------------------------------------------------------- #
# Workflow descriptors
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class UploadField:
    """A single upload slot shown on the Run Workflow page."""

    key: str            # the inputs[...] key the workflow expects
    label: str          # plain-language label for finance staff
    required: bool
    file_types: tuple[str, ...] = ("csv",)
    help: str = ""


@dataclass(frozen=True)
class WorkflowDescriptor:
    workflow_type: str
    title: str
    description: str
    uploads: tuple[UploadField, ...]
    example_files: dict[str, str]   # inputs key -> path under data/synthetic
    available: bool = True
    note: str = ""


BANK_RECONCILIATION = WorkflowDescriptor(
    workflow_type="bank_reconciliation",
    title="Bank reconciliation",
    description=(
        "Compare a bank statement against a ledger export, match transactions "
        "deterministically by amount and date, and produce a reviewable "
        "exception packet. All matching is done by code; the AI only explains "
        "the unmatched items."
    ),
    uploads=(
        UploadField("bank", "Bank statement (CSV)", True),
        UploadField("ledger", "Ledger export (CSV)", True),
        UploadField("chart_of_accounts", "Chart of accounts (CSV)", False),
        UploadField("reconciliation_config", "Reconciliation config (JSON)",
                    False, ("json",)),
    ),
    example_files={
        "bank": "bank_reconciliation/bank.csv",
        "ledger": "bank_reconciliation/ledger.csv",
        "reconciliation_config": "bank_reconciliation/reconciliation_config.json",
    },
)

BUDGET_VARIANCE = WorkflowDescriptor(
    workflow_type="budget_variance",
    title="Budget-to-actual variance review",
    description=(
        "Join a budget file to an actuals file by fund/account/department/"
        "object, compute dollar and percentage variances, and flag the lines "
        "that exceed your thresholds. All math is deterministic; the AI only "
        "drafts plain-language commentary citing the flagged rows."
    ),
    uploads=(
        UploadField("budget", "Budget (CSV)", True),
        UploadField("actuals", "Actuals (CSV)", True),
        UploadField("chart_of_accounts", "Chart of accounts (CSV)", False),
        UploadField("thresholds", "Variance thresholds (JSON)", False, ("json",)),
    ),
    example_files={
        "budget": "budget_variance/budget.csv",
        "actuals": "budget_variance/actuals.csv",
        "chart_of_accounts": "budget_variance/chart_of_accounts.csv",
        "thresholds": "budget_variance/thresholds.json",
    },
)

REPORT_REVIEW = WorkflowDescriptor(
    workflow_type="report_review",
    title="Financial report consistency review",
    description=(
        "Check a draft financial report or schedule for inconsistencies before "
        "a human finalizes it: subtotal mismatches, invalid account codes, "
        "duplicate lines, missing sections, large changes from a prior version, "
        "and inconsistent naming. The AI only explains the flagged issues."
    ),
    uploads=(
        UploadField("report_table", "Report table (CSV)", True),
        UploadField("chart_of_accounts", "Chart of accounts (CSV)", False),
        UploadField("prior_version", "Prior version (CSV)", False),
        UploadField("checklist_config", "Review checklist (JSON)", False, ("json",)),
    ),
    example_files={
        "report_table": "report_review/report_table.csv",
        "chart_of_accounts": "report_review/chart_of_accounts.csv",
        "prior_version": "report_review/prior_version.csv",
        "checklist_config": "report_review/checklist_config.json",
    },
)

FREEFORM = WorkflowDescriptor(
    workflow_type="freeform",
    title="Guided freeform task",
    description=(
        "A controlled fallback for tasks that are not yet a formal workflow. "
        "You describe the task with structured fields (not a chat box); the "
        "request is routed through the same logging and validation system. "
        "Output is always a DRAFT requiring human review."
    ),
    uploads=(
        UploadField("uploaded_files", "Supporting files (optional)", False,
                    ("csv", "xlsx", "txt", "md", "pdf", "json")),
    ),
    example_files={},
)

TRANSACTION_SEARCH = WorkflowDescriptor(
    workflow_type="transaction_search",
    title="Transaction search (plain English)",
    description=(
        "Search across Tyler/Munis exports (GL detail, AP invoices, checks, "
        "purchase orders) using a plain-English query. The query is parsed "
        "into structured criteria, schema-validated, and then executed as "
        "deterministic filters; every match cites its source row. The AI "
        "never selects rows itself."
    ),
    uploads=(
        UploadField("gl_detail", "GL detail (CSV/XLSX)", False, ("csv", "xlsx")),
        UploadField("ap_invoices", "AP invoice detail (CSV/XLSX)", False,
                    ("csv", "xlsx")),
        UploadField("checks", "Check register (CSV/XLSX)", False, ("csv", "xlsx")),
        UploadField("purchase_orders", "Purchase orders (CSV/XLSX)", False,
                    ("csv", "xlsx"),
                    help="At least one data file is required."),
    ),
    example_files={
        "gl_detail": "tyler/gl_detail.csv",
        "ap_invoices": "tyler/ap_invoice_detail.csv",
        "checks": "tyler/check_register.csv",
        "purchase_orders": "tyler/purchase_orders.csv",
    },
)

# Example query used when running transaction search on the example files
# (matches the module's bundled SAMPLE_INPUTS / known-answer dataset).
TRANSACTION_SEARCH_EXAMPLE_QUERY = transaction_search.SAMPLE_INPUTS["query"]

AP_DUPLICATE_REVIEW = WorkflowDescriptor(
    workflow_type="ap_duplicate_review",
    title="AP duplicate / suspicious payment review",
    description=(
        "Review a Tyler/Munis AP invoice export for duplicate, suspicious, "
        "and policy-violating payments (exact duplicates, near-date same-"
        "amount pairs, similar vendor names, missing POs, inactive/unknown "
        "vendors, split payments). All checks are deterministic; the AI only "
        "summarizes and drafts review notes citing the flagged rows."
    ),
    uploads=(
        UploadField("ap_invoices", "AP invoice detail (CSV/XLSX)", True,
                    ("csv", "xlsx")),
        UploadField("vendor_list", "Vendor list (CSV/XLSX)", False,
                    ("csv", "xlsx")),
        UploadField("check_register", "Check register (CSV/XLSX)", False,
                    ("csv", "xlsx")),
        UploadField("purchase_orders", "Purchase orders (CSV/XLSX)", False,
                    ("csv", "xlsx")),
        UploadField("config", "Review thresholds (JSON)", False, ("json",)),
    ),
    example_files={
        "ap_invoices": "tyler/ap_invoice_detail.csv",
        "vendor_list": "tyler/vendor_list.csv",
        "check_register": "tyler/check_register.csv",
        "purchase_orders": "tyler/purchase_orders.csv",
        "config": "ap_duplicate_review/review_config.json",
    },
)

JE_UPLOAD_PREP = WorkflowDescriptor(
    workflow_type="je_upload_prep",
    title="Journal entry upload prep",
    description=(
        "Validate a draft journal entry against the chart of accounts and "
        "fiscal configuration (balanced debits/credits, valid accounts, "
        "dates in period, no duplicates), then produce an upload-ready "
        "workbook. If ANY blocking error exists the upload file is NOT "
        "written - only a structured error report. The AI only drafts a "
        "plain-language summary of the deterministic validation results."
    ),
    uploads=(
        UploadField("je_draft", "Draft journal entry (CSV/XLSX)", True,
                    ("csv", "xlsx")),
        UploadField("chart_of_accounts", "Chart of accounts (CSV/XLSX)", True,
                    ("csv", "xlsx")),
        UploadField("gl_detail", "GL detail history (CSV/XLSX)", False,
                    ("csv", "xlsx"),
                    help="Optional: enables the fund/org/object combination "
                         "plausibility warning."),
        UploadField("config", "Fiscal period config (JSON)", False, ("json",)),
    ),
    example_files={
        "je_draft": "je_upload_prep/je_draft_valid.csv",
        "chart_of_accounts": "tyler/chart_of_accounts.csv",
        "gl_detail": "tyler/gl_detail.csv",
        "config": "je_upload_prep/je_config.json",
    },
)

PO_INVOICE_REVIEW = WorkflowDescriptor(
    workflow_type="po_invoice_review",
    title="PO / invoice mismatch review",
    description=(
        "Join AP invoices to purchase-order lines and flag every known "
        "mismatch pattern: invoice over PO total, wrong vendor, missing PO, "
        "closed-PO usage, unit-price/quantity mismatches, received-vs-"
        "invoiced gaps. All matching is deterministic; the AI only explains "
        "the flagged issues and suggests human follow-up."
    ),
    uploads=(
        UploadField("purchase_orders", "Purchase orders (CSV/XLSX)", True,
                    ("csv", "xlsx")),
        UploadField("ap_invoices", "AP invoice detail (CSV/XLSX)", True,
                    ("csv", "xlsx")),
        UploadField("vendor_list", "Vendor list (CSV/XLSX)", False,
                    ("csv", "xlsx")),
        UploadField("check_register", "Check register (CSV/XLSX)", False,
                    ("csv", "xlsx")),
        UploadField("config", "Match tolerances (JSON)", False, ("json",)),
    ),
    example_files={
        "purchase_orders": "tyler/purchase_orders.csv",
        "ap_invoices": "tyler/ap_invoice_detail.csv",
        "vendor_list": "tyler/vendor_list.csv",
        "check_register": "tyler/check_register.csv",
        "config": "po_invoice_review/match_config.json",
    },
)

DESCRIPTORS: dict[str, WorkflowDescriptor] = {
    d.workflow_type: d
    for d in (BANK_RECONCILIATION, BUDGET_VARIANCE, REPORT_REVIEW,
              TRANSACTION_SEARCH, AP_DUPLICATE_REVIEW, JE_UPLOAD_PREP,
              PO_INVOICE_REVIEW, FREEFORM)
}

# Each workflow module exposes a module-level ``CAPABILITY: CapabilitySpec`` and
# ``detect_conditions(profiles, mappings, inputs, config)`` per the preflight
# integration convention. The runner uses these to run preflight before any
# workflow/LLM call.
WORKFLOW_MODULES: dict[str, Any] = {
    "bank_reconciliation": bank_reconciliation,
    "budget_variance": budget_variance,
    "report_review": report_review,
    "transaction_search": transaction_search,
    "ap_duplicate_review": ap_duplicate_review,
    "je_upload_prep": je_upload_prep,
    "po_invoice_review": po_invoice_review,
    "freeform": freeform,
}

# The Tyler-era workflows share one self-managing run() contract
# (run(inputs, *, provider, ledger, audit, run_id, actor, export_dir, config)
# -> dict) and are driven by a single generic adapter below.
_SELF_MANAGED_MODULES: dict[str, Any] = {
    "transaction_search": transaction_search,
    "ap_duplicate_review": ap_duplicate_review,
    "je_upload_prep": je_upload_prep,
    "po_invoice_review": po_invoice_review,
}

# Workflows whose run() appends <run_id> to the export_dir it is given (the
# others write directly into the directory passed). The runner hands these the
# PARENT export dir so artifacts land in the same per-run subdirectory either
# way.
_APPENDS_RUN_ID_TO_EXPORT_DIR = frozenset({"transaction_search", "je_upload_prep"})

# Display order for the Run Workflow page.
WORKFLOW_ORDER = (
    "bank_reconciliation",
    "budget_variance",
    "report_review",
    "transaction_search",
    "ap_duplicate_review",
    "je_upload_prep",
    "po_invoice_review",
    "freeform",
)


def list_descriptors() -> list[WorkflowDescriptor]:
    return [DESCRIPTORS[k] for k in WORKFLOW_ORDER]


def get_descriptor(workflow_type: str) -> WorkflowDescriptor:
    return DESCRIPTORS[workflow_type]


def example_path(rel: str) -> Path:
    """Resolve a synthetic example file path under data/synthetic."""
    return SYNTHETIC_DIR / rel


def make_id_safe() -> str:
    """A filesystem-safe unique id (for temp upload subdirectories)."""
    return make_id()


# --------------------------------------------------------------------------- #
# Uniform run result
# --------------------------------------------------------------------------- #
@dataclass
class UniformRunResult:
    run_id: str
    workflow_type: str
    findings: list[DeterministicFinding]
    summary: dict[str, Any]
    result_tables: dict[str, Any]
    llm_response: Optional[LLMResponse]
    validation: Optional[ValidationResult]
    export_paths: dict[str, str]
    refused: bool = False
    refusal_reason: str = ""
    # Preflight / capability layer.
    preflight: Optional[PreflightReport] = None
    preflight_status: str = "pass"   # pass | partial | fail
    partial: bool = False
    blocked: bool = False            # True when preflight FAILed (workflow not run)
    llm_called: bool = False


# --------------------------------------------------------------------------- #
# Internal helpers to normalize each workflow's output
# --------------------------------------------------------------------------- #
def _input_files_for(inputs: dict[str, Any]) -> list:
    """Build InputFile metadata for any CSV paths present in ``inputs``."""
    out = []
    for key, val in inputs.items():
        if not isinstance(val, (str, Path)):
            continue
        p = Path(val)
        if p.is_file() and p.suffix.lower() == ".csv":
            try:
                parsed = load_csv(p, table_name=key)
                out.append(to_input_file(p, parsed))
            except Exception:
                # Metadata best-effort; never block a run on it.
                pass
    return out


def _run_budget_variance(inputs, provider, export_dir, run_id, column_mappings=None):
    wf = budget_variance.BudgetVarianceWorkflow()
    if column_mappings:
        inputs = dict(inputs)
        inputs["column_mappings"] = column_mappings
    result = wf.run(inputs, provider=provider)
    det = result.deterministic
    export_paths: dict[str, str] = {}
    if export_dir is not None:
        artifacts = budget_variance.export_artifacts(
            result, export_dir, run_id=run_id
        )
        export_paths = {a.file_name: a.path for a in artifacts}
    return UniformRunResult(
        run_id=run_id,
        workflow_type="budget_variance",
        findings=det.findings,
        summary=det.summary,
        result_tables=det.result_tables,
        llm_response=result.llm_response,
        validation=result.validation,
        export_paths=export_paths,
    )


def _run_bank_reconciliation(
    inputs, provider, export_dir, run_id, ledger, audit, actor, config,
    column_mappings=None,
):
    res = bank_reconciliation.run(
        inputs,
        provider=provider,
        ledger=ledger,
        audit=audit,
        run_id=run_id,
        actor=actor,
        export_dir=export_dir,
        config=config,
        column_mappings=column_mappings,
    )
    det = res["deterministic"]
    return UniformRunResult(
        run_id=run_id,
        workflow_type="bank_reconciliation",
        findings=res["findings"],
        summary=res["summary"],
        result_tables=getattr(det, "result_tables", {}) or {},
        llm_response=res["llm_response"],
        validation=res["validation"],
        export_paths={k: str(v) for k, v in res.get("export_paths", {}).items()},
    )


def _run_report_review(
    inputs, provider, export_dir, run_id, ledger, audit, actor,
    column_mappings=None,
):
    res = report_review.run(
        inputs,
        provider=provider,
        ledger=ledger,
        audit=audit,
        run_id=run_id,
        actor=actor,
        export_dir=export_dir,
        column_mappings=column_mappings,
    )
    det = res["deterministic"]
    return UniformRunResult(
        run_id=run_id,
        workflow_type="report_review",
        findings=res["findings"],
        summary=res["summary"],
        result_tables=getattr(det, "result_tables", {}) or {},
        llm_response=res["llm_response"],
        validation=res["validation"],
        export_paths={k: str(v) for k, v in res.get("export_paths", {}).items()},
    )


def _run_freeform(
    inputs, provider, export_dir, run_id, ledger, audit, actor,
    column_mappings=None,
):
    res = freeform.run(
        inputs,
        provider=provider,
        ledger=ledger,
        audit=audit,
        run_id=run_id,
        actor=actor,
        export_dir=export_dir,
        column_mappings=column_mappings,
    )
    det = res["deterministic"]
    return UniformRunResult(
        run_id=run_id,
        workflow_type="freeform",
        findings=res["findings"],
        summary=res["summary"],
        result_tables=getattr(det, "result_tables", {}) or {},
        llm_response=res["llm_response"],
        validation=res["validation"],
        export_paths={k: str(v) for k, v in res.get("export_paths", {}).items()},
    )


def _run_self_managed(
    module, workflow_type, inputs, provider, export_dir, run_id, ledger, audit,
    actor, config,
):
    """Drive a Tyler-era self-managing workflow module through its uniform
    ``run()`` and normalize the returned dict into a UniformRunResult.

    ``export_dir`` must already account for the module's own run_id nesting
    (see ``_APPENDS_RUN_ID_TO_EXPORT_DIR``).
    """
    res = module.run(
        inputs,
        provider=provider,
        ledger=ledger,
        audit=audit,
        run_id=run_id,
        actor=actor,
        export_dir=export_dir,
        config=config,
    )
    det = res.get("deterministic")
    return UniformRunResult(
        run_id=run_id,
        workflow_type=workflow_type,
        findings=list(res.get("findings") or []),
        summary=dict(res.get("summary") or {}),
        result_tables=getattr(det, "result_tables", {}) or {},
        llm_response=res.get("llm_response"),
        validation=res.get("validation"),
        export_paths={k: str(v) for k, v in (res.get("export_paths") or {}).items()},
    )


# --------------------------------------------------------------------------- #
# Audit lifecycle ownership
# --------------------------------------------------------------------------- #
class _LifecycleSuppressingAudit:
    """Audit proxy that forwards granular events but no-ops runner-owned ones.

    Some workflow modules (bank_reconciliation, freeform) emit
    ``run_created`` / ``run_completed`` / ``run_failed`` / ``export_generated``
    internally. ``run_workflow`` is the single, consistent owner of those events,
    so when it calls a workflow it passes this proxy to suppress the duplicates
    while still letting the workflow's granular events
    (deterministic_analysis_completed, llm_request_sent, llm_response_received,
    validation_completed) flow through to the real audit log. This keeps the
    audit trail uniform across all four workflows.
    """

    _OWNED_BY_RUNNER = {
        "run_created", "run_completed", "run_failed", "export_generated",
    }

    def __init__(self, inner: Optional[AuditLog]):
        self._inner = inner

    def __getattr__(self, name: str):
        if name in self._OWNED_BY_RUNNER:
            return lambda *a, **k: None
        return getattr(self._inner, name)


_ARTIFACT_TYPE_BY_SUFFIX = {
    ".md": "markdown", ".csv": "csv", ".json": "json", ".zip": "zip",
}


def _ensure_export_artifacts_recorded(
    ledger: RunLedger, run_id: str, export_paths: dict[str, str]
) -> None:
    """Record any workflow export files not already in the ledger.

    bank_reconciliation/freeform self-store their artifacts; budget_variance and
    report_review only return paths. This makes every workflow's artifacts appear
    in the ledger uniformly (idempotent by file_name, so self-stored ones are not
    duplicated). sha256 is computed from the file on disk.
    """
    from src.core.exports import sha256_file
    from src.core.schemas import ArtifactType, ExportArtifact

    existing = {a.get("file_name") for a in ledger.list_export_artifacts(run_id)}
    for name, path in export_paths.items():
        if name in existing:
            continue
        p = Path(path)
        if not p.is_file():
            continue
        atype = _ARTIFACT_TYPE_BY_SUFFIX.get(p.suffix.lower(), "other")
        ledger.store_export_artifact(run_id, ExportArtifact(
            run_id=run_id, artifact_type=ArtifactType(atype),
            file_name=name, path=str(p), sha256=sha256_file(p)))


def _apply_source_formats(
    inputs: dict[str, Any],
    source_formats: Optional[dict[str, str]],
    work_dir: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Apply column-alias presets to selected CSV inputs (import adapter).

    For each input key mapped to a non-empty preset whose value is an existing
    CSV path, writes an aliased copy into ``work_dir`` and substitutes its path.
    Returns ``(new_inputs, applied)`` where ``applied`` maps input key -> preset
    name actually applied. Non-CSV values, missing files, and ``None``/empty
    preset selections are left untouched. The workflow still reads a plain CSV
    path, so its deterministic logic is unchanged.
    """
    if not source_formats:
        return inputs, {}
    new_inputs = dict(inputs)
    applied: dict[str, str] = {}
    for key, preset in source_formats.items():
        if not preset:
            continue
        val = inputs.get(key)
        if not isinstance(val, (str, Path)):
            continue
        p = Path(val)
        if not (p.is_file() and p.suffix.lower() == ".csv"):
            continue
        try:
            new_inputs[key] = str(normalize_csv(p, preset, work_dir))
            applied[key] = preset
        except Exception:
            # Never block a run on a preset failure; fall back to the raw file.
            new_inputs[key] = str(p)
    return new_inputs, applied


# The deterministic config keys each Tyler-era workflow documents. Only these
# keys are threaded from a caller-supplied config dict into the workflow run;
# anything else (e.g. bank-specific Settings tolerances) is dropped so one
# workflow's settings never silently leak into another's thresholds.
_SELF_MANAGED_CONFIG_KEYS: dict[str, tuple[str, ...]] = {
    "transaction_search": (
        "max_results", "fuzzy_threshold", "missing_po_threshold",
    ),
    "ap_duplicate_review": (
        "near_date_window_days", "amount_tolerance", "split_threshold",
        "split_window_days", "missing_po_min_amount",
        "vendor_similarity_threshold",
    ),
    "je_upload_prep": (
        "fiscal_period_start", "fiscal_period_end", "allow_inactive_accounts",
        "round_dollar_warning_threshold", "min_description_length",
    ),
    "po_invoice_review": (
        "unit_price_tolerance_pct", "qty_tolerance",
        "invoice_over_po_tolerance_pct", "closed_po_grace_days",
        "missing_po_min_amount",
    ),
}


def _config_for(workflow_type: str, inputs: dict, config: Optional[dict]) -> Any:
    """Map UI/Settings config onto each workflow's expected shape.

    Settings tolerances/thresholds are applied to a run unless the user supplied
    an explicit config/threshold file in ``inputs`` (uploaded files win).
    """
    config = config or {}
    if workflow_type in _SELF_MANAGED_CONFIG_KEYS:
        if inputs.get("config"):
            return None  # uploaded config file takes precedence
        keys = _SELF_MANAGED_CONFIG_KEYS[workflow_type]
        out = {k: config[k] for k in keys if k in config}
        return out or None
    if workflow_type == "bank_reconciliation":
        if inputs.get("reconciliation_config"):
            return None  # uploaded config file takes precedence
        out: dict[str, Any] = {}
        if "amount_tolerance" in config:
            out["amount_tolerance"] = str(config["amount_tolerance"])
        if "date_tolerance_days" in config:
            out["date_tolerance_days"] = int(config["date_tolerance_days"])
        return out or None
    if workflow_type == "budget_variance":
        if not inputs.get("thresholds") and (
            "variance_dollar_threshold" in config
            or "variance_threshold_pct" in config
        ):
            inputs["thresholds"] = {
                "dollar_threshold": config.get("variance_dollar_threshold"),
                "pct_threshold": config.get("variance_threshold_pct"),
            }
    return None


# --------------------------------------------------------------------------- #
# Preflight integration helpers
# --------------------------------------------------------------------------- #
def _approved_mappings_from_report(
    report: PreflightReport,
) -> dict[str, dict[str, str]]:
    """Derive HUMAN-approved ``{input_key: {semantic: column}}`` overrides.

    Only mappings a human explicitly approved (``source == 'human'``) are passed
    to the workflow as overrides. The engine's own ``auto`` guesses are NOT
    forced on the workflow — each workflow keeps its own auto-detection as the
    default path, so a clean run behaves exactly as before. Human overrides win.
    """
    out: dict[str, dict[str, str]] = {}
    for m in report.column_mappings:
        if m.source == "human" and m.mapped_column:
            out.setdefault(m.input_key, {})[m.semantic_name] = m.mapped_column
    return out


def _preflight_findings_as_deterministic(
    report: PreflightReport,
) -> list[DeterministicFinding]:
    """Convert non-blocking preflight findings into DeterministicFindings.

    On a PARTIAL run these surface in the findings table / exports so reviewers
    see exactly which unsupported conditions were detected. They are marked
    ``requires_human_review=True`` and carry the preflight code/severity in
    ``computed_values`` (no source rows — preflight is file/column level, not a
    matched data row).
    """
    out: list[DeterministicFinding] = []
    for f in report.findings:
        if f.blocks_run:
            continue
        if f.severity == Severity.INFO:
            continue
        out.append(
            DeterministicFinding(
                finding_type=FindingType.OTHER,
                severity=f.severity,
                description=f"[preflight] {f.message}",
                source_rows=[],
                computed_values={
                    "preflight_code": f.code.value,
                    "affected_input": f.affected_input or "",
                    "affected_column": f.affected_column or "",
                    "suggested_action": f.suggested_action,
                },
                rule_used=f"preflight:{f.code.value}",
                requires_human_review=True,
            )
        )
    return out


def _record_preflight_metadata(
    summary: dict[str, Any],
    report: PreflightReport,
    *,
    approved_mappings: dict[str, dict[str, str]],
    llm_called: bool,
) -> None:
    """Stamp preflight provenance onto the run summary (in place)."""
    summary["preflight_status"] = report.status.value
    summary["partial"] = report.partial
    summary["llm_called"] = llm_called
    summary["detected_columns"] = {
        p.input_key: list(p.detected_columns) for p in report.file_profiles
    }
    # All mappings the engine resolved (auto + human), for the audit trail.
    resolved: dict[str, dict[str, str]] = {}
    for m in report.column_mappings:
        if m.source != "unmapped" and m.mapped_column:
            resolved.setdefault(m.input_key, {})[m.semantic_name] = m.mapped_column
    summary["column_mappings_resolved"] = resolved
    # The subset of human-approved overrides actually forced on the workflow.
    summary["column_mappings_used"] = approved_mappings
    summary["parse_confidence"] = {
        p.input_key: dict(p.parse_confidence) for p in report.file_profiles
    }
    summary["supported_checks"] = list(report.supported_checks)
    summary["unsupported_conditions"] = list(report.unsupported_conditions)


# --------------------------------------------------------------------------- #
# Uniform pipeline entry point
# --------------------------------------------------------------------------- #
def run_workflow(
    workflow_type: str,
    inputs: dict[str, Any],
    *,
    ledger: RunLedger,
    audit: Optional[AuditLog] = None,
    provider: Any = None,
    actor: str = "finance_staff",
    export_dir: Optional[str | Path] = None,
    config: Optional[dict] = None,
    source_formats: Optional[dict[str, str]] = None,
    retention_category: str = "draft_working",
    review_packet: bool = True,
    column_mappings: Optional[dict[str, dict[str, str]]] = None,
) -> UniformRunResult:
    """Drive any registered workflow through the shared ledger/audit/export
    pipeline and return a uniform result.

    ``provider`` defaults to None, which means each workflow uses its built-in
    mock LLM (no API key / no internet) — the spec's default path.

    ``config`` carries UI/Settings options (tolerances, thresholds). Uploaded
    config/threshold files in ``inputs`` always take precedence over it.

    ``source_formats`` maps an input key (e.g. ``"bank"``) to an import preset
    name (see ``src.ingest.presets``). Matching CSV inputs are column-aliased to
    the canonical names before the workflow reads them; the recorded input-file
    hashes still reflect the ORIGINAL uploads, and the applied presets are noted
    in the run summary + an audit event for traceability.

    ``retention_category`` tags the run for records-retention / public-records
    purposes (see ``RetentionCategory``). It is persisted on the ledger row, added
    to the run summary, and noted on the ``run_created`` audit event. Unknown
    values fall back to the ``draft_working`` default.

    When ``review_packet`` is True and ``export_dir`` is set, a consolidated
    review packet (``review_packet.md`` + ``run_manifest.json``) is generated
    after the run from the persisted ledger data.
    """
    descriptor = DESCRIPTORS.get(workflow_type)
    if descriptor is None or not descriptor.available:
        raise ValueError(f"Workflow '{workflow_type}' is not available.")

    run_id = make_id()
    # Each run gets its own export subdirectory so runs never overwrite each
    # other's artifacts (important for the Export Center / a live demo).
    run_export_dir: Optional[Path] = None
    if export_dir is not None:
        export_dir = Path(export_dir)
        run_export_dir = export_dir / run_id
        run_export_dir.mkdir(parents=True, exist_ok=True)

    wf_config = _config_for(workflow_type, inputs, config)
    # The workflow modules own their granular audit events; run_workflow owns the
    # run lifecycle. Suppress duplicate lifecycle events from self-managing modules.
    wf_audit = _LifecycleSuppressingAudit(audit) if audit is not None else None

    # Normalize the retention category (fall back to the default if unknown).
    try:
        retention = RetentionCategory(retention_category)
    except ValueError:
        retention = RetentionCategory.DRAFT_WORKING

    # 1. Ledger entry + input-file metadata.
    input_files = _input_files_for(inputs)
    run = WorkflowRun(
        run_id=run_id,
        workflow_type=workflow_type,
        created_by=actor,
        input_files=input_files,
        status=RunStatus.RUNNING,
        retention_category=retention,
    )
    ledger.create_run(run)
    if audit is not None:
        audit.run_created(run_id, actor, workflow_type=workflow_type,
                          retention_category=retention.value)
        for f in input_files:
            audit.file_uploaded(run_id, actor, file_name=f.file_name,
                                file_hash=f.file_hash)

    # Import adapter: apply source-format presets to CSV inputs (if any). The
    # ORIGINAL uploads are what input_files (above) hashed; the workflow reads
    # the aliased copies. Done after metadata so the source of record is intact.
    if run_export_dir is not None:
        norm_dir = run_export_dir / "_normalized_inputs"
    else:
        import tempfile
        norm_dir = Path(tempfile.mkdtemp(prefix="govwf_src_"))
    wf_inputs, applied_formats = _apply_source_formats(
        inputs, source_formats, norm_dir)
    if applied_formats and audit is not None:
        audit.file_parsed(run_id, actor, source_formats=applied_formats)

    # 2. PREFLIGHT / capability check (runs BEFORE the workflow + any LLM call).
    module = WORKFLOW_MODULES[workflow_type]
    report = preflight_engine.run_preflight(
        module.CAPABILITY,
        wf_inputs,
        approved_mappings=column_mappings,
        config=wf_config if isinstance(wf_config, dict) else config,
        detect_conditions=getattr(module, "detect_conditions", None),
    )
    ledger.store_preflight(run_id, preflight_engine.preflight_report_dict(report))
    if audit is not None:
        audit.file_parsed(
            run_id, actor, stage="preflight",
            preflight_status=report.status.value,
            unsupported_conditions=report.unsupported_conditions)
    # Override columns the engine resolved (human/auto); unmapped semantics fall
    # back to each workflow's own auto-detection.
    approved = _approved_mappings_from_report(report)

    # --- FAIL: do NOT run the workflow and do NOT call the LLM. --- #
    if report.status == PreflightStatus.FAIL:
        summary: dict[str, Any] = {}
        _record_preflight_metadata(
            summary, report, approved_mappings=approved, llm_called=False)
        summary["validation_status"] = "n/a"
        summary["retention_category"] = retention.value
        summary["blocked"] = True
        if applied_formats:
            summary["source_formats"] = applied_formats
        ledger.update_run_status(
            run_id, RunStatus.FAILED.value, summary=summary,
            retention_category=retention.value)
        # Failed-preflight export packet: the two preflight files only.
        export_paths: dict[str, str] = {}
        if run_export_dir is not None:
            try:
                packet = generate_failed_preflight_packet(
                    ledger, audit, run_id, export_dir,
                    preflight=preflight_engine.preflight_report_dict(report),
                    actor=actor)
                export_paths = {a.file_name: a.path for a in packet}
            except Exception:
                pass
        if audit is not None:
            audit.run_failed(
                run_id, actor, reason="preflight_failed",
                preflight_status="fail")
        return UniformRunResult(
            run_id=run_id,
            workflow_type=workflow_type,
            findings=[],
            summary=summary,
            result_tables={},
            llm_response=None,
            validation=None,
            export_paths=export_paths,
            refused=True,
            refusal_reason=(
                "Preflight failed; the workflow was not run. "
                + (report.next_steps[0] if report.next_steps else "")
            ).strip(),
            preflight=report,
            preflight_status="fail",
            partial=False,
            blocked=True,
            llm_called=False,
        )

    try:
        if workflow_type == "budget_variance":
            result = _run_budget_variance(
                wf_inputs, provider, run_export_dir, run_id,
                column_mappings=approved or None)
            # budget_variance does not self-persist; do it here.
            ledger.store_findings(run_id, result.findings)
            if audit is not None:
                audit.deterministic_analysis_completed(
                    run_id, actor, finding_count=len(result.findings))
            if result.llm_response is not None:
                ledger.store_llm_response(run_id, result.llm_response)
                if audit is not None:
                    audit.llm_request_sent(
                        run_id, actor,
                        template=result.llm_response.prompt_template_version)
                    audit.llm_response_received(
                        run_id, actor,
                        model_name=result.llm_response.model_name)
            if result.validation is not None:
                ledger.store_validation_result(run_id, result.validation)
                if audit is not None:
                    audit.validation_completed(
                        run_id, actor, passed=result.validation.passed)
        elif workflow_type == "bank_reconciliation":
            result = _run_bank_reconciliation(
                wf_inputs, provider, run_export_dir, run_id, ledger, wf_audit,
                actor, wf_config, column_mappings=approved or None)
        elif workflow_type == "report_review":
            # report_review self-persists findings/llm/validation when given
            # ledger+audit; it does not write a run row (we did that above).
            result = _run_report_review(
                wf_inputs, provider, run_export_dir, run_id, ledger, wf_audit,
                actor, column_mappings=approved or None)
        elif workflow_type == "freeform":
            result = _run_freeform(
                wf_inputs, provider, run_export_dir, run_id, ledger, wf_audit,
                actor, column_mappings=approved or None)
        elif workflow_type in _SELF_MANAGED_MODULES:
            # Tyler-era workflows: one uniform self-managing run() contract.
            # transaction_search / je_upload_prep append <run_id> themselves,
            # so they get the PARENT export dir; the others write directly
            # into the per-run subdirectory.
            if run_export_dir is None:
                wf_export: Optional[Path] = None
            elif workflow_type in _APPENDS_RUN_ID_TO_EXPORT_DIR:
                wf_export = Path(export_dir)
            else:
                wf_export = run_export_dir
            result = _run_self_managed(
                _SELF_MANAGED_MODULES[workflow_type], workflow_type,
                wf_inputs, provider, wf_export, run_id, ledger, wf_audit,
                actor, wf_config)
        else:  # pragma: no cover - guarded above
            raise ValueError(workflow_type)
    except freeform.SensitivityNotConfirmedError as exc:
        ledger.update_run_status(run_id, RunStatus.FAILED.value)
        if audit is not None:
            audit.run_failed(run_id, actor, reason="sensitivity_not_confirmed")
        return UniformRunResult(
            run_id=run_id,
            workflow_type=workflow_type,
            findings=[],
            summary={},
            result_tables={},
            llm_response=None,
            validation=None,
            export_paths={},
            refused=True,
            refusal_reason=str(exc) or "Sensitivity confirmation required.",
        )
    except Exception:
        ledger.update_run_status(run_id, RunStatus.FAILED.value)
        if audit is not None:
            audit.run_failed(run_id, actor)
        raise

    # 2b. PARTIAL: append the preflight unsupported-condition findings to the
    #     results so they appear in the findings table / exports, and re-persist
    #     the full finding set. The deterministic logic already ran; the LLM (if
    #     called) may only EXPLAIN deterministic findings (enforced in validation).
    llm_called = result.llm_response is not None
    if report.status == PreflightStatus.PARTIAL:
        extra = _preflight_findings_as_deterministic(report)
        if extra:
            result.findings = list(result.findings) + extra
            ledger.store_findings(run_id, result.findings)

    # 4/5. Ensure every workflow's export artifacts are recorded in the ledger
    # (bank_reconciliation/freeform self-store; budget_variance/report_review
    # only returned paths), then emit one workflow-artifacts export event.
    if run_export_dir is not None and result.export_paths:
        _ensure_export_artifacts_recorded(ledger, run_id, result.export_paths)
        if audit is not None:
            audit.export_generated(
                run_id, actor, artifacts=sorted(result.export_paths))

    # 6. Mark completed + persist summary + validation status.
    val = result.validation
    validation_status = (
        "passed" if (val is not None and val.passed)
        else "warnings" if (val is not None and val.warnings and not val.errors)
        else "failed" if val is not None else "n/a"
    )
    summary = dict(result.summary or {})
    summary["validation_status"] = validation_status
    summary["retention_category"] = retention.value
    if applied_formats:
        summary["source_formats"] = applied_formats
    # Stamp preflight provenance on every (PASS/PARTIAL) run.
    _record_preflight_metadata(
        summary, report, approved_mappings=approved, llm_called=llm_called)
    ledger.update_run_status(
        run_id, RunStatus.COMPLETED.value, summary=summary,
        retention_category=retention.value)
    if audit is not None:
        audit.run_completed(run_id, actor,
                            validation_status=validation_status,
                            preflight_status=report.status.value)
    result.summary = summary
    result.preflight = report
    result.preflight_status = report.status.value
    result.partial = report.status == PreflightStatus.PARTIAL
    result.llm_called = llm_called

    # 7. Consolidated review packet (built from persisted ledger data).
    if review_packet and export_dir is not None:
        try:
            packet = generate_review_packet(
                ledger, audit, run_id, export_dir, actor=actor)
            for a in packet:
                result.export_paths[a.file_name] = a.path
        except Exception:
            # A packet failure must never fail an otherwise-successful run.
            pass

    return result


# --------------------------------------------------------------------------- #
# Human review actions
# --------------------------------------------------------------------------- #
# The set of actions the spec requires per finding.
HUMAN_REVIEW_ACTIONS = (
    ("mark_reviewed", "Mark reviewed"),
    ("mark_resolved", "Mark resolved"),
    ("needs_follow_up", "Needs follow-up"),
    ("add_note", "Add note"),
    ("reject_ai_explanation", "Reject AI explanation"),
    ("approve_draft", "Approve draft for export"),
)


def record_human_review_action(
    ledger: RunLedger,
    audit: Optional[AuditLog],
    *,
    run_id: str,
    action: str,
    actor: str,
    finding_id: Optional[str] = None,
    note: Optional[str] = None,
) -> str:
    """Persist a HumanReviewAction to the ledger + an audit event.

    Returns the new action_id. This is the single path the UI uses for every
    per-finding review control so persistence + audit always stay in sync.
    """
    from src.core.schemas import HumanReviewAction

    hra = HumanReviewAction(
        run_id=run_id,
        finding_id=finding_id,
        action=action,
        actor=actor,
        note=note,
    )
    ledger.store_human_review_action(run_id, hra)
    if audit is not None:
        audit.human_review_action(
            run_id, actor, action=action, finding_id=finding_id, note=note)
    return hra.action_id
