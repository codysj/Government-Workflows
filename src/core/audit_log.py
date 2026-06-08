"""Append-only audit log (Phase 3.5).

Appends AuditEvent rows to BOTH the SQLite ledger (audit_events table) and a
JSONL file under runs/audit/<run_id>.jsonl. Existing events are never mutated
or deleted. Helpers exist for each spec 3.5 event type.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from src.core.run_ledger import RunLedger
from src.core.schemas import AuditEvent, EventType


class AuditLog:
    def __init__(
        self,
        ledger: RunLedger,
        audit_dir: str | Path = "runs/audit",
        write_jsonl: bool = True,
    ):
        self.ledger = ledger
        self.audit_dir = Path(audit_dir)
        self.write_jsonl = write_jsonl
        if self.write_jsonl:
            self.audit_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    def append(
        self,
        run_id: str,
        event_type: EventType,
        actor: str,
        details: Optional[dict[str, Any]] = None,
    ) -> AuditEvent:
        event = AuditEvent(
            run_id=run_id,
            event_type=event_type,
            actor=actor,
            details=details or {},
        )
        # SQLite (append-only INSERT, never UPDATE/DELETE). The ledger owns the
        # connection lifecycle so this stays thread-safe under Streamlit reruns.
        self.ledger.append_audit_event(
            event_id=event.event_id,
            run_id=event.run_id,
            timestamp=event.timestamp.isoformat(),
            event_type=event.event_type.value,
            actor=event.actor,
            details=event.details,
        )
        # JSONL (append-only file).
        if self.write_jsonl:
            path = self.audit_dir / f"{run_id}.jsonl"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(event.model_dump_json() + "\n")
        return event

    def list_events(self, run_id: str) -> list[dict]:
        return self.ledger.list_audit_events(run_id)

    # ------------------------------------------------------------------ #
    # Typed helpers for each spec 3.5 event.
    # ------------------------------------------------------------------ #
    def run_created(self, run_id: str, actor: str, **details: Any) -> AuditEvent:
        return self.append(run_id, EventType.RUN_CREATED, actor, details)

    def file_uploaded(self, run_id: str, actor: str, **details: Any) -> AuditEvent:
        return self.append(run_id, EventType.FILE_UPLOADED, actor, details)

    def file_parsed(self, run_id: str, actor: str, **details: Any) -> AuditEvent:
        return self.append(run_id, EventType.FILE_PARSED, actor, details)

    def deterministic_analysis_completed(
        self, run_id: str, actor: str, **details: Any
    ) -> AuditEvent:
        return self.append(
            run_id, EventType.DETERMINISTIC_ANALYSIS_COMPLETED, actor, details
        )

    def llm_request_sent(self, run_id: str, actor: str, **details: Any) -> AuditEvent:
        return self.append(run_id, EventType.LLM_REQUEST_SENT, actor, details)

    def llm_response_received(
        self, run_id: str, actor: str, **details: Any
    ) -> AuditEvent:
        return self.append(run_id, EventType.LLM_RESPONSE_RECEIVED, actor, details)

    def validation_completed(
        self, run_id: str, actor: str, **details: Any
    ) -> AuditEvent:
        return self.append(run_id, EventType.VALIDATION_COMPLETED, actor, details)

    def human_review_action(
        self, run_id: str, actor: str, **details: Any
    ) -> AuditEvent:
        return self.append(run_id, EventType.HUMAN_REVIEW_ACTION, actor, details)

    def export_generated(self, run_id: str, actor: str, **details: Any) -> AuditEvent:
        return self.append(run_id, EventType.EXPORT_GENERATED, actor, details)

    def run_completed(self, run_id: str, actor: str, **details: Any) -> AuditEvent:
        return self.append(run_id, EventType.RUN_COMPLETED, actor, details)

    def run_failed(self, run_id: str, actor: str, **details: Any) -> AuditEvent:
        return self.append(run_id, EventType.RUN_FAILED, actor, details)
