"""Versioned prompt templates (Phase 3.6).

PROMPT_TEMPLATES maps a version string (e.g. 'bank_recon.v1') to a builder
callable. Each builder turns deterministic findings into a prompt that
instructs the model to ONLY explain/summarize/draft and to cite source_row ids.
"""
from __future__ import annotations

import json
from typing import Any, Callable

_GUARDRAILS = (
    "You are a finance-review assistant for a small municipal finance team.\n"
    "STRICT RULES:\n"
    "- You may ONLY explain, summarize, draft, classify, and flag.\n"
    "- You MUST NOT calculate, decide matches, or invent account numbers, "
    "funds, vendors, amounts, or dates.\n"
    "- Only use numbers and source-row ids that already appear in the data below.\n"
    "- Every claim must cite source_row ids in 'referenced_source_rows'.\n"
    "- Do NOT produce final-approval language; all output is a DRAFT for human "
    "review.\n"
)

_OUTPUT_CONTRACT = (
    "Return JSON with keys: summary (str), categorized_exceptions (list of "
    "{category, description, referenced_source_rows}), referenced_source_rows "
    "(list of str), suggested_review_steps (list of str), draft_memo (str).\n"
)


def _findings_block(det: Any) -> str:
    """Serialize a DeterministicOutput-like object into a prompt-safe block."""
    findings = getattr(det, "findings", det)
    rows = []
    for f in findings:
        fid = getattr(f, "finding_id", None)
        ftype = getattr(f, "finding_type", None)
        ftype = getattr(ftype, "value", ftype)
        desc = getattr(f, "description", "")
        cv = getattr(f, "computed_values", {})
        src_ids = [
            f"{getattr(s, 'table_name', '')}:{getattr(s, 'row_index', '')}"
            for s in getattr(f, "source_rows", [])
        ]
        rows.append(
            {
                "finding_id": fid,
                "finding_type": ftype,
                "description": desc,
                "computed_values": cv,
                "source_row_ids": src_ids,
            }
        )
    return json.dumps(rows, default=str, indent=2)


def build_bank_recon_prompt(det: Any, context: Any = None) -> str:
    summary = getattr(det, "summary", {})
    return (
        _GUARDRAILS
        + "\nWORKFLOW: Bank reconciliation.\n"
        + "TASK: Summarize unmatched items, draft plain-language likely causes, "
        "group exceptions into categories, suggest human review steps, and draft "
        "reconciliation memo language. Do NOT perform any matching.\n\n"
        + f"DETERMINISTIC SUMMARY:\n{json.dumps(summary, default=str, indent=2)}\n\n"
        + f"DETERMINISTIC FINDINGS:\n{_findings_block(det)}\n\n"
        + _OUTPUT_CONTRACT
    )


PROMPT_TEMPLATES: dict[str, Callable[..., str]] = {
    "bank_recon.v1": build_bank_recon_prompt,
}


def get_prompt_builder(version: str) -> Callable[..., str]:
    if version not in PROMPT_TEMPLATES:
        raise KeyError(f"Unknown prompt template version: {version}")
    return PROMPT_TEMPLATES[version]
