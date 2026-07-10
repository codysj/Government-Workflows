"""Run-scoped Q&A assistant (src.core.run_qa).

Covers: a grounded answer cites real source rows; an ungrounded question is
refused; an invented reference is caught by validation; and each turn lands in
the ledger as a tagged LLMResponse (so it inherits audit / AI usage log /
retention / review packet).
"""
from __future__ import annotations

from pathlib import Path

from src.core.audit_log import AuditLog
from src.core.run_ledger import RunLedger
from src.core import run_qa
from src.workflows.bank_reconciliation import run as run_bank_rec


def _completed_run(tmp_path: Path) -> tuple[RunLedger, AuditLog, str, Path]:
    """A real bank-reconciliation run persisted to a fresh ledger."""
    sd = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "bank_reconciliation"
    ledger = RunLedger(":memory:")
    audit = AuditLog(ledger, audit_dir=tmp_path / "audit", write_jsonl=False)
    export_dir = tmp_path / "exports"
    # Persist a run row so get_run works (the workflow stores findings/llm/etc).
    from src.core.schemas import WorkflowRun, RunStatus
    rid = "qa_run_1"
    ledger.create_run(WorkflowRun(run_id=rid, workflow_type="bank_reconciliation",
                                  created_by="tester", status=RunStatus.RUNNING))
    run_bank_rec({"bank": str(sd / "bank.csv"), "ledger": str(sd / "ledger.csv")},
                 ledger=ledger, audit=audit, run_id=rid, export_dir=export_dir / rid)
    return ledger, audit, rid, export_dir


class _InventingProvider:
    """A stub provider that cites a row id that is NOT in the findings."""

    model_provider = "stub"
    model_name = "stub-invent"

    def generate_structured_response(self, prompt, schema=None):
        return {"summary": "It relates to row bogus:999.",
                "referenced_source_rows": ["bogus:999"], "answerable": True}


def test_grounded_answer_cites_real_source_rows(tmp_path):
    ledger, audit, rid, export_dir = _completed_run(tmp_path)
    turn = run_qa.answer_question(
        ledger, audit, rid,
        "Why might these unmatched bank items not reconcile?",
        export_dir=export_dir)
    assert turn is not None
    assert turn["answerable"] is True
    assert turn["referenced_source_rows"], "a grounded answer should cite rows"
    # Every cited ref is a real finding source row (validation passed).
    assert turn["validation"]["passed"] is True
    assert turn["validation"]["invented_reference_detected"] is False


def test_ungrounded_question_is_refused(tmp_path):
    ledger, audit, rid, export_dir = _completed_run(tmp_path)
    turn = run_qa.answer_question(
        ledger, audit, rid,
        "Recommend stocks unrelated zebra giraffe spaceship",
        export_dir=export_dir)
    assert turn["answerable"] is False
    assert turn["referenced_source_rows"] == []
    assert "can't answer" in turn["answer"].lower()


def test_invented_reference_is_caught_by_validation(tmp_path):
    ledger, audit, rid, export_dir = _completed_run(tmp_path)
    turn = run_qa.answer_question(
        ledger, audit, rid, "What about this item?",
        provider=_InventingProvider(), export_dir=export_dir)
    assert turn["validation"]["invented_reference_detected"] is True
    assert turn["validation"]["passed"] is False


def test_turn_is_logged_for_audit_usage_and_packet(tmp_path):
    ledger, audit, rid, export_dir = _completed_run(tmp_path)
    run_qa.answer_question(ledger, audit, rid, "Why are these unmatched?",
                           export_dir=export_dir)

    # AI usage log: the turn shows up as its own tagged interaction.
    interactions = ledger.list_llm_interactions()
    qa_rows = [i for i in interactions
               if str(i.get("prompt_template_version", "")).startswith("run_qa")]
    assert len(qa_rows) == 1

    # Audit trail: a Q&A request + response were recorded.
    events = [e["event_type"] for e in audit.list_events(rid)]
    assert "llm_request_sent" in events and "llm_response_received" in events

    # Review packet was refreshed and now contains the Q&A section.
    packet = (export_dir / rid / "review_packet.md").read_text(encoding="utf-8")
    assert "Run Q&A" in packet

    # The run's headline validation_results is NOT polluted by the turn
    # (the workflow draft stored exactly one).
    run = ledger.get_run(rid)
    assert len(run.get("validation_results") or []) == 1
    assert len(run_qa.qa_turns_for_run(run)) == 1
