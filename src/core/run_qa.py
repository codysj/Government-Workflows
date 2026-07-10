"""Run-scoped Q&A assistant ("ask about this run").

A bounded assistant attached to ONE completed run. It answers natural-language
questions grounded ONLY in that run's deterministic findings, their cited source
rows, the available non-sensitive reference data (city profile + chart of
accounts), and run metadata. It is not a general chatbot: every answer passes
through the SAME validator drafted commentary uses (`src.core.validation`), so
invented source-row references and account codes are rejected and stray numeric
claims are flagged before a user sees them as trustworthy.

It never computes finance values, decides matches, reaches the internet, or
mutates anything. Each turn is persisted as an `LLMResponse` (tagged
`run_qa.v1`) so it inherits the run's audit trail, AI usage log, retention
category, and review packet for free.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from src.core.schemas import DeterministicFinding, LLMResponse
from src.core.validation import validate_llm_output
from src.context.context_loader import load_context

RUN_QA_TEMPLATE_VERSION = "run_qa.v1"
# Header the offline mock keys off to recognise a Q&A prompt (see provider.py).
QA_PROMPT_MARKER = "RUN Q&A ASSISTANT"
_MAX_FINDINGS_IN_PROMPT = 50  # ponytail: bound prompt size; raise if runs grow huge

_CANT_ANSWER = (
    "I can't answer that from this run's data. Try asking about the flagged "
    "items, their source rows, or the run's results."
)


def _short_ref(s: dict) -> str:
    return f"{s.get('table_name')}:{s.get('row_index')}"


def _findings_block(findings: list[dict]) -> list[dict]:
    """Compact, JSON-able view of findings for the prompt. Carries source-row ids
    (so the model/mock cite REAL refs) plus the cell values for reasoning."""
    block = []
    for f in findings[:_MAX_FINDINGS_IN_PROMPT]:
        srcs = f.get("source_rows") or []
        block.append({
            "finding_id": f.get("finding_id"),
            "finding_type": f.get("finding_type"),
            "rule_used": f.get("rule_used"),
            "severity": f.get("severity"),
            "description": f.get("description"),
            "computed_values": f.get("computed_values") or {},
            "source_row_ids": [_short_ref(s) for s in srcs],
            "source_rows": [
                {"ref": _short_ref(s), "values": s.get("source_values") or {}}
                for s in srcs
            ],
        })
    return block


def build_grounding(ledger: Any, run_id: str) -> Optional[dict]:
    """Everything an answer may be grounded in, recovered from the ledger +
    available reference data. Returns None if the run is unknown."""
    run = ledger.get_run(run_id)
    if run is None:
        return None
    findings = ledger.list_findings(run_id)
    reference = load_context()  # city profile + (default) chart of accounts
    return {
        "metadata": {
            "run_id": run_id,
            "workflow_type": run.get("workflow_type"),
            "status": run.get("status"),
            "created_at": run.get("created_at"),
            "summary": run.get("summary") or {},
        },
        "reference": reference,
        "findings": findings,
    }


def build_prompt(question: str, grounding: dict) -> str:
    """Assemble the grounded Q&A prompt. The findings block is in the same shape
    the shared mock/validator expect (`DETERMINISTIC FINDINGS:` + JSON array)."""
    ref = grounding["reference"]
    ref_view = {
        "city_profile": ref.get("city_profile", {}),
        "chart_of_accounts": ref.get("chart_of_accounts", {}),
        "available": ref.get("available", []),
    }
    return (
        f"{QA_PROMPT_MARKER} — answer ONLY from this run's data.\n"
        "STRICT RULES:\n"
        "- Use ONLY the findings, source rows, reference data, and run metadata "
        "below. \n"
        "- Do NOT compute or assert any number, decide a match, or introduce an "
        "account/fund/vendor/amount/date/policy not present below.\n"
        "- Cite the source-row ids you rely on in 'referenced_source_rows'.\n"
        "- If the question cannot be answered from this data, set "
        '"answerable": false and say so plainly. Do NOT answer from general '
        "knowledge.\n"
        "- This is an advisory DRAFT for human review; never give final approval.\n\n"
        f"RUN METADATA:\n{json.dumps(grounding['metadata'], default=str, indent=2)}\n\n"
        f"REFERENCE DATA (available, non-sensitive):\n"
        f"{json.dumps(ref_view, default=str, indent=2)}\n\n"
        f"DETERMINISTIC FINDINGS:\n"
        f"{json.dumps(_findings_block(grounding['findings']), default=str, indent=2)}\n\n"
        f"QUESTION: {question}\n\n"
        'Return JSON: {"summary": str, "referenced_source_rows": [str], '
        '"answerable": bool}.\n'
    )


def _findings_objs(finding_dicts: list[dict]) -> list[DeterministicFinding]:
    """Rebuild DeterministicFinding objects for the validator. Falls back to a
    generic finding_type if a stored value is outside the enum."""
    objs: list[DeterministicFinding] = []
    for d in finding_dicts:
        try:
            objs.append(DeterministicFinding.model_validate(d))
        except Exception:
            safe = dict(d)
            safe["finding_type"] = "other"
            try:
                objs.append(DeterministicFinding.model_validate(safe))
            except Exception:
                continue  # unparseable finding: skip for validation grounding
    return objs


def answer_question(
    ledger: Any,
    audit: Any,
    run_id: str,
    question: str,
    *,
    provider: Any = None,
    export_dir: str | Path | None = None,
    actor: str = "finance_staff",
) -> Optional[dict]:
    """Answer one question about a run; persist + validate + audit the turn.

    Returns the turn dict (or None if the run is unknown). The turn is stored as
    an LLMResponse tagged ``run_qa.v1`` so it inherits the run's audit/usage/
    packet/retention; turn-level validation lives inside the turn, never in the
    run's validation_results (the run's headline validation stays unpolluted).
    """
    question = (question or "").strip()
    grounding = build_grounding(ledger, run_id)
    if grounding is None:
        return None

    from src.llm.provider import get_provider  # local: avoid import cycle
    provider = provider or get_provider()

    prompt = build_prompt(question, grounding)
    if audit is not None and hasattr(audit, "llm_request_sent"):
        audit.llm_request_sent(
            run_id, actor, kind="run_qa",
            template=RUN_QA_TEMPLATE_VERSION, question=question[:200])

    raw = provider.generate_structured_response(prompt, schema=None)
    if not isinstance(raw, dict):
        raw = {"summary": str(raw), "referenced_source_rows": [], "answerable": False}

    findings_objs = _findings_objs(grounding["findings"])
    coa_codes = (grounding["reference"].get("chart_of_accounts") or {}).get("codes") or None
    validation = validate_llm_output(
        raw,
        findings_objs,
        valid_account_codes=coa_codes,
        require_references=False,
        missing_references_is_error=False,
        check_numeric_claims=True,
    )

    turn = {
        "question": question,
        "answer": str(raw.get("summary", "")),
        "answerable": bool(raw.get("answerable", True)),
        "referenced_source_rows": list(raw.get("referenced_source_rows", []) or []),
        "validation": {
            "passed": validation.passed,
            "errors": validation.errors,
            "warnings": validation.warnings,
            "invented_reference_detected": validation.invented_reference_detected,
            "numeric_claims_checked": validation.numeric_claims_checked,
        },
    }

    response = LLMResponse(
        run_id=run_id,
        prompt_template_version=RUN_QA_TEMPLATE_VERSION,
        model_provider=getattr(provider, "model_provider", "mock"),
        model_name=getattr(provider, "model_name", "mock-llm"),
        response_json=turn,
        referenced_source_rows=turn["referenced_source_rows"],
    )
    if ledger is not None and hasattr(ledger, "store_llm_response"):
        ledger.store_llm_response(run_id, response)
    if audit is not None and hasattr(audit, "llm_response_received"):
        audit.llm_response_received(
            run_id, actor, kind="run_qa",
            model_name=response.model_name,
            validation_passed=validation.passed)

    # Refresh the review packet so the turn is part of the exported run record.
    if export_dir is not None:
        try:
            from src.core.review_packet import generate_review_packet
            generate_review_packet(ledger, audit, run_id, export_dir, actor=actor)
        except Exception:
            pass  # ponytail: packet refresh is best-effort; never fail a turn on it

    return turn


def qa_turns_for_run(run: dict) -> list[dict]:
    """Pull the persisted Q&A turns (newest last) out of a run's llm_responses.

    A run's ``llm_responses`` mixes the workflow draft with Q&A turns; turns are
    the ones tagged ``run_qa.v1``. Shared by the API and the review packet so
    both split the same way.
    """
    turns = []
    for r in run.get("llm_responses") or []:
        if str(r.get("prompt_template_version", "")).startswith("run_qa"):
            payload = r.get("response_json") or {}
            turns.append(payload)
    return turns


def is_qa_response(llm_response: dict) -> bool:
    """True if a stored llm_response row is a Q&A turn (not the workflow draft)."""
    return str(llm_response.get("prompt_template_version", "")).startswith("run_qa")


__all__ = [
    "RUN_QA_TEMPLATE_VERSION",
    "QA_PROMPT_MARKER",
    "build_grounding",
    "build_prompt",
    "answer_question",
    "qa_turns_for_run",
    "is_qa_response",
]
