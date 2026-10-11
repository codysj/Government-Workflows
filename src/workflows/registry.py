"""The single workflow registry.

One ``WorkflowDescriptor`` per workflow carries everything any caller needs:
the module that runs it, upload slots and text inputs (API/console), bundled
sample data, CLI names, and catalog metadata. ``src.core.pipeline.run_workflow``
is the only thing that executes a descriptor's module.

Every workflow module exposes the same entry point::

    run(inputs, *, provider, ledger, audit, run_id, actor, export_dir,
        config, column_mappings) -> dict

(``freeform`` takes no ``config``; the Tyler-era modules take no
``column_mappings``. The pipeline passes only the keywords a module accepts.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

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

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_DIR = REPO_ROOT / "data" / "synthetic"


@dataclass(frozen=True)
class UploadField:
    """One file slot a workflow accepts."""

    key: str            # the inputs[...] key the workflow expects
    label: str          # plain-language label for finance staff
    required: bool
    file_types: tuple[str, ...] = ("csv",)
    help: str = ""


@dataclass(frozen=True)
class TextInput:
    """One non-file input (multipart text field with the same key)."""

    key: str
    label: str
    required: bool
    help: str = ""
    example: Optional[str] = None


@dataclass(frozen=True)
class WorkflowDescriptor:
    workflow_type: str
    title: str
    description: str
    module: ModuleType
    category: str                               # review | search | prep | other
    cli_name: str
    aliases: tuple[str, ...]
    uploads: tuple[UploadField, ...]
    example_files: dict[str, str]               # inputs key -> path under data/synthetic
    sample_text: dict[str, Any] = field(default_factory=dict)  # non-file sample inputs
    text_inputs: tuple[TextInput, ...] = ()
    sample_description: Optional[str] = None
    # transaction_search / je_upload_prep write into <export_dir>/<run_id>
    # themselves, so the pipeline hands them the PARENT export dir.
    appends_run_id_to_export_dir: bool = False
    note: str = ""

    @property
    def has_sample(self) -> bool:
        return bool(self.example_files)

    def sample_inputs(self) -> dict[str, Any]:
        """Bundled sample inputs: absolute file paths plus any sample text."""
        out: dict[str, Any] = {
            k: str(SYNTHETIC_DIR / rel) for k, rel in self.example_files.items()
        }
        out.update(self.sample_text)
        return out

    def input_help(self) -> str:
        """One-line ``key=<file>`` usage string (CLI)."""
        parts = [
            f"{u.key}=<file>" if u.required else f"[{u.key}=<file>]"
            for u in self.uploads
        ]
        parts += [
            f'{t.key}="<text>"' if t.required else f'[{t.key}="<text>"]'
            for t in self.text_inputs
        ]
        return " ".join(parts)


_CSV_XLSX = ("csv", "xlsx")

_DESCRIPTORS: tuple[WorkflowDescriptor, ...] = (
    WorkflowDescriptor(
        workflow_type="bank_reconciliation",
        title="Bank reconciliation",
        description=(
            "Compare a bank statement against a ledger export, match "
            "transactions deterministically by amount and date, and produce a "
            "reviewable exception packet. All matching is done by code; the AI "
            "only explains the unmatched items."
        ),
        module=bank_reconciliation,
        category="review",
        cli_name="bank-reconciliation",
        aliases=("reconciliation", "rec"),
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
        sample_description=(
            "Synthetic bank statement + ledger export with known matched, "
            "unmatched, and timing-difference items."),
    ),
    WorkflowDescriptor(
        workflow_type="budget_variance",
        title="Budget-to-actual variance review",
        description=(
            "Join a budget file to an actuals file by fund/account/department/"
            "object, compute dollar and percentage variances, and flag the "
            "lines that exceed your thresholds. All math is deterministic; the "
            "AI only drafts plain-language commentary citing the flagged rows."
        ),
        module=budget_variance,
        category="review",
        cli_name="budget-variance",
        aliases=("variance",),
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
        sample_description=(
            "Synthetic budget and actuals files with lines that exceed the "
            "bundled thresholds."),
    ),
    WorkflowDescriptor(
        workflow_type="report_review",
        title="Financial report consistency review",
        description=(
            "Check a draft financial report or schedule for inconsistencies "
            "before a human finalizes it: subtotal mismatches, invalid account "
            "codes, duplicate lines, missing sections, large changes from a "
            "prior version, and inconsistent naming. The AI only explains the "
            "flagged issues."
        ),
        module=report_review,
        category="review",
        cli_name="report-review",
        aliases=("report", "consistency"),
        uploads=(
            UploadField("report_table", "Report table (CSV)", True),
            UploadField("chart_of_accounts", "Chart of accounts (CSV)", False),
            UploadField("prior_version", "Prior version (CSV)", False),
            UploadField("checklist_config", "Review checklist (JSON)", False,
                        ("json",)),
        ),
        example_files={
            "report_table": "report_review/report_table.csv",
            "chart_of_accounts": "report_review/chart_of_accounts.csv",
            "prior_version": "report_review/prior_version.csv",
            "checklist_config": "report_review/checklist_config.json",
        },
        sample_description=(
            "Synthetic draft report table with a prior version and chart of "
            "accounts, seeded with consistency issues."),
    ),
    WorkflowDescriptor(
        workflow_type="transaction_search",
        title="Transaction search (plain English)",
        description=(
            "Search across Tyler/Munis exports (GL detail, AP invoices, checks, "
            "purchase orders) using a plain-English query. The query is parsed "
            "into structured criteria, schema-validated, and then executed as "
            "deterministic filters; every match cites its source row. The AI "
            "never selects rows itself."
        ),
        module=transaction_search,
        category="search",
        cli_name="transaction-search",
        aliases=("search", "tx-search"),
        uploads=(
            UploadField("gl_detail", "GL detail (CSV/XLSX)", False, _CSV_XLSX),
            UploadField("ap_invoices", "AP invoice detail (CSV/XLSX)", False,
                        _CSV_XLSX),
            UploadField("checks", "Check register (CSV/XLSX)", False, _CSV_XLSX),
            UploadField("purchase_orders", "Purchase orders (CSV/XLSX)", False,
                        _CSV_XLSX, help="At least one data file is required."),
        ),
        example_files={
            "gl_detail": "tyler/gl_detail.csv",
            "ap_invoices": "tyler/ap_invoice_detail.csv",
            "checks": "tyler/check_register.csv",
            "purchase_orders": "tyler/purchase_orders.csv",
        },
        sample_text={"query": transaction_search.SAMPLE_INPUTS["query"]},
        text_inputs=(
            TextInput(
                key="query",
                label="Plain-English search query",
                required=True,
                help=("Describe what to find, e.g. vendor, amount range, and "
                      "date range. Parsed into deterministic filters."),
                example=transaction_search.SAMPLE_INPUTS["query"],
            ),
        ),
        sample_description=(
            "Synthetic Tyler/Munis-style GL, AP, check, and PO exports plus an "
            "example plain-English query."),
        appends_run_id_to_export_dir=True,
    ),
    WorkflowDescriptor(
        workflow_type="ap_duplicate_review",
        title="AP duplicate / suspicious payment review",
        description=(
            "Review a Tyler/Munis AP invoice export for duplicate, suspicious, "
            "and policy-violating payments (exact duplicates, near-date same-"
            "amount pairs, similar vendor names, missing POs, inactive/unknown "
            "vendors, split payments). All checks are deterministic; the AI "
            "only summarizes and drafts review notes citing the flagged rows."
        ),
        module=ap_duplicate_review,
        category="review",
        cli_name="ap-duplicate-review",
        aliases=("ap-duplicates", "duplicate-review"),
        uploads=(
            UploadField("ap_invoices", "AP invoice detail (CSV/XLSX)", True,
                        _CSV_XLSX),
            UploadField("vendor_list", "Vendor list (CSV/XLSX)", False, _CSV_XLSX),
            UploadField("check_register", "Check register (CSV/XLSX)", False,
                        _CSV_XLSX),
            UploadField("purchase_orders", "Purchase orders (CSV/XLSX)", False,
                        _CSV_XLSX),
            UploadField("config", "Review thresholds (JSON)", False, ("json",)),
        ),
        example_files={
            "ap_invoices": "tyler/ap_invoice_detail.csv",
            "vendor_list": "tyler/vendor_list.csv",
            "check_register": "tyler/check_register.csv",
            "purchase_orders": "tyler/purchase_orders.csv",
            "config": "ap_duplicate_review/review_config.json",
        },
        sample_description=(
            "Synthetic Tyler/Munis-style AP invoice export seeded with "
            "duplicate and suspicious payments."),
    ),
    WorkflowDescriptor(
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
        module=je_upload_prep,
        category="prep",
        cli_name="je-upload-prep",
        aliases=("je-prep", "journal-entry-prep"),
        uploads=(
            UploadField("je_draft", "Draft journal entry (CSV/XLSX)", True,
                        _CSV_XLSX),
            UploadField("chart_of_accounts", "Chart of accounts (CSV/XLSX)", True,
                        _CSV_XLSX),
            UploadField("gl_detail", "GL detail history (CSV/XLSX)", False,
                        _CSV_XLSX,
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
        sample_description=(
            "A valid synthetic journal-entry draft plus chart of accounts and "
            "GL history."),
        appends_run_id_to_export_dir=True,
    ),
    WorkflowDescriptor(
        workflow_type="po_invoice_review",
        title="PO / invoice mismatch review",
        description=(
            "Join AP invoices to purchase-order lines and flag every known "
            "mismatch pattern: invoice over PO total, wrong vendor, missing PO, "
            "closed-PO usage, unit-price/quantity mismatches, received-vs-"
            "invoiced gaps. All matching is deterministic; the AI only explains "
            "the flagged issues and suggests human follow-up."
        ),
        module=po_invoice_review,
        category="review",
        cli_name="po-invoice-review",
        aliases=("po-review", "po-match"),
        uploads=(
            UploadField("purchase_orders", "Purchase orders (CSV/XLSX)", True,
                        _CSV_XLSX),
            UploadField("ap_invoices", "AP invoice detail (CSV/XLSX)", True,
                        _CSV_XLSX),
            UploadField("vendor_list", "Vendor list (CSV/XLSX)", False, _CSV_XLSX),
            UploadField("check_register", "Check register (CSV/XLSX)", False,
                        _CSV_XLSX),
            UploadField("config", "Match tolerances (JSON)", False, ("json",)),
        ),
        example_files={
            "purchase_orders": "tyler/purchase_orders.csv",
            "ap_invoices": "tyler/ap_invoice_detail.csv",
            "vendor_list": "tyler/vendor_list.csv",
            "check_register": "tyler/check_register.csv",
            "config": "po_invoice_review/match_config.json",
        },
        sample_description=(
            "Synthetic purchase orders and AP invoices seeded with known "
            "mismatch patterns."),
    ),
    WorkflowDescriptor(
        workflow_type="freeform",
        title="Guided freeform task",
        description=(
            "A controlled fallback for tasks that are not yet a formal "
            "workflow. You describe the task with structured fields (not a "
            "chat box); the request is routed through the same logging and "
            "validation system. Output is always a DRAFT requiring human review."
        ),
        module=freeform,
        category="other",
        cli_name="freeform",
        aliases=("guided-freeform",),
        uploads=(
            UploadField("uploaded_files", "Supporting files (optional)", False,
                        ("csv", "xlsx", "txt", "md", "pdf", "json")),
        ),
        example_files={},
        # CLI --sample only (the console has no freeform sample): a synthetic
        # request that confirms no real sensitive data so the run completes.
        sample_text={
            "task_type": "grant_reimbursement_summary",
            "desired_output": "A short plain-language reimbursement summary memo (draft).",
            "relevant_context": "Synthetic sample request for the bundled demo only.",
            "uploaded_files": [],
            "sensitivity_confirmation": True,
            "human_review_confirmation": True,
        },
        text_inputs=(
            TextInput("task_type", "Task type", True,
                      "Short label for the requested task.", "draft_memo"),
            TextInput("desired_output", "Desired output", True,
                      "What you want produced (always a DRAFT for human review)."),
            TextInput("relevant_context", "Relevant context", False,
                      "Optional background the draft should reflect."),
            TextInput("sensitivity_confirmation",
                      "No real sensitive data (send 'true')", True,
                      "Must be 'true' to certify no real sensitive data is "
                      "included. The run is refused otherwise.", "true"),
            TextInput("human_review_confirmation",
                      "Output will be human-reviewed (send 'true')", True,
                      "Must be 'true'; all freeform output is a draft.", "true"),
        ),
    ),
)

DESCRIPTORS: dict[str, WorkflowDescriptor] = {d.workflow_type: d for d in _DESCRIPTORS}

# Every accepted name (workflow_type, CLI name, aliases) -> descriptor.
_BY_NAME: dict[str, WorkflowDescriptor] = {}
for _d in _DESCRIPTORS:
    for _name in (_d.workflow_type, _d.cli_name, *_d.aliases):
        _BY_NAME[_name] = _d


def list_descriptors() -> list[WorkflowDescriptor]:
    """All workflows in display order."""
    return list(_DESCRIPTORS)


def get_descriptor(workflow_type: str) -> WorkflowDescriptor:
    """Exact lookup by workflow_type. Raises KeyError for unknown types."""
    return DESCRIPTORS[workflow_type]


def resolve(name: str) -> Optional[WorkflowDescriptor]:
    """Lenient lookup by workflow_type, CLI name, or alias (CLI)."""
    return _BY_NAME.get(name) or _BY_NAME.get(name.replace("_", "-"))
