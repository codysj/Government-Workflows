"""Workflow catalog: map WorkflowDescriptor -> contract WorkflowInfo.

Adds the API-only metadata the descriptors do not carry: a coarse category
for grouping in the UI, the non-file text inputs each workflow accepts, and
sample-data availability.
"""
from __future__ import annotations

from typing import Optional

from app import workflow_registry as wfr
from api.schemas.models import TextInputInfo, UploadFieldInfo, WorkflowInfo

# Coarse grouping for progressive disclosure in the frontend.
CATEGORY_BY_TYPE: dict[str, str] = {
    "bank_reconciliation": "review",
    "budget_variance": "review",
    "report_review": "review",
    "ap_duplicate_review": "review",
    "po_invoice_review": "review",
    "transaction_search": "search",
    "je_upload_prep": "prep",
    "freeform": "other",
}

# Non-file inputs each workflow accepts (multipart text fields with the same
# key). Boolean confirmations are sent as the strings "true"/"false".
TEXT_INPUTS_BY_TYPE: dict[str, list[TextInputInfo]] = {
    "transaction_search": [
        TextInputInfo(
            key="query",
            label="Plain-English search query",
            required=True,
            help=("Describe what to find, e.g. vendor, amount range, and "
                  "date range. Parsed into deterministic filters."),
            example=wfr.TRANSACTION_SEARCH_EXAMPLE_QUERY,
        ),
    ],
    "freeform": [
        TextInputInfo(
            key="task_type",
            label="Task type",
            required=True,
            help="Short label for the requested task.",
            example="draft_memo",
        ),
        TextInputInfo(
            key="desired_output",
            label="Desired output",
            required=True,
            help="What you want produced (always a DRAFT for human review).",
            example=None,
        ),
        TextInputInfo(
            key="relevant_context",
            label="Relevant context",
            required=False,
            help="Optional background the draft should reflect.",
            example=None,
        ),
        TextInputInfo(
            key="sensitivity_confirmation",
            label="No real sensitive data (send 'true')",
            required=True,
            help=("Must be 'true' to certify no real sensitive data is "
                  "included. The run is refused otherwise."),
            example="true",
        ),
        TextInputInfo(
            key="human_review_confirmation",
            label="Output will be human-reviewed (send 'true')",
            required=True,
            help="Must be 'true'; all freeform output is a draft.",
            example="true",
        ),
    ],
}

SAMPLE_DESCRIPTION_BY_TYPE: dict[str, str] = {
    "bank_reconciliation": (
        "Synthetic bank statement + ledger export with known matched, "
        "unmatched, and timing-difference items."),
    "budget_variance": (
        "Synthetic budget and actuals files with lines that exceed the "
        "bundled thresholds."),
    "report_review": (
        "Synthetic draft report table with a prior version and chart of "
        "accounts, seeded with consistency issues."),
    "transaction_search": (
        "Synthetic Tyler/Munis-style GL, AP, check, and PO exports plus an "
        "example plain-English query."),
    "ap_duplicate_review": (
        "Synthetic Tyler/Munis-style AP invoice export seeded with duplicate "
        "and suspicious payments."),
    "je_upload_prep": (
        "A valid synthetic journal-entry draft plus chart of accounts and "
        "GL history."),
    "po_invoice_review": (
        "Synthetic purchase orders and AP invoices seeded with known "
        "mismatch patterns."),
}


def workflow_info(descriptor: wfr.WorkflowDescriptor) -> WorkflowInfo:
    wt = descriptor.workflow_type
    return WorkflowInfo(
        workflow_type=wt,
        title=descriptor.title,
        description=descriptor.description,
        note=descriptor.note or None,
        category=CATEGORY_BY_TYPE.get(wt, "other"),
        uploads=[
            UploadFieldInfo(
                key=u.key,
                label=u.label,
                required=u.required,
                file_types=list(u.file_types),
                help=u.help,
            )
            for u in descriptor.uploads
        ],
        text_inputs=TEXT_INPUTS_BY_TYPE.get(wt, []),
        has_sample=bool(descriptor.example_files),
        sample_description=SAMPLE_DESCRIPTION_BY_TYPE.get(wt),
    )


def list_workflow_infos() -> list[WorkflowInfo]:
    return [workflow_info(d) for d in wfr.list_descriptors()]


def get_workflow_info(workflow_type: str) -> Optional[WorkflowInfo]:
    try:
        return workflow_info(wfr.get_descriptor(workflow_type))
    except KeyError:
        return None
