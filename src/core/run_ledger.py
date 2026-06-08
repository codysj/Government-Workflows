"""SQLite run ledger (Phase 3.4).

Persists runs, input files, findings, LLM responses, validation results, human
review actions, export artifacts, and audit events. Complex fields are
serialized as JSON. Supports ':memory:' for tests.

Threading model
---------------
This is a simple local audit/workflow store, not a production database layer.
A single long-lived ``sqlite3.Connection`` cannot be shared across threads
(``sqlite3`` raises "SQLite objects created in a thread can only be used in that
same thread"). Streamlit reruns the script on rotating ScriptRunner threads, so
a ledger cached with ``st.cache_resource`` would be touched from many threads.

To stay correct and simple we open a FRESH connection per operation for
file-backed ledgers (each connection is created, used, and closed in the calling
thread). In-memory (':memory:') ledgers keep ONE shared connection because an
in-memory database is private to its connection and cannot be reopened; that
path is test-only and the connection is created with ``check_same_thread=False``
so it is robust if a test touches it from another thread.
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterator, Optional

from src.core.schemas import (
    DeterministicFinding,
    ExportArtifact,
    HumanReviewAction,
    InputFile,
    LLMResponse,
    ValidationResult,
    WorkflowRun,
)


def _default_db_path() -> str:
    return os.environ.get("LEDGER_DB", "runs/ledger.db")


def _json(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _model_json(model: Any) -> str:
    # pydantic v2 -> JSON-safe string
    return model.model_dump_json()


class RunLedger:
    """Thin SQLite-backed ledger.

    File-backed ledgers use one short-lived connection per operation, so the
    same ``RunLedger`` instance is safe to share across Streamlit rerun threads.
    In-memory ledgers reuse a single connection (an in-memory DB cannot be
    reopened).
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        self._is_memory = self.db_path == ":memory:"
        if not self._is_memory:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # Shared connection ONLY for the non-reopenable in-memory DB.
        self._mem_conn: Optional[sqlite3.Connection] = (
            self._new_connection() if self._is_memory else None
        )
        self._create_tables()

    # ------------------------------------------------------------------ #
    # Connection management
    # ------------------------------------------------------------------ #
    def _new_connection(self) -> sqlite3.Connection:
        # check_same_thread=False is safe here: file-backed connections live for
        # a single operation in one thread; the in-memory connection is test-only.
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection for one operation.

        For file-backed ledgers this opens a fresh connection (created and used
        in the calling thread) and closes it afterwards. For in-memory ledgers it
        yields the single shared connection and leaves it open.
        """
        if self._is_memory:
            assert self._mem_conn is not None
            yield self._mem_conn
        else:
            conn = self._new_connection()
            try:
                yield conn
            finally:
                conn.close()

    # ------------------------------------------------------------------ #
    def _create_tables(self) -> None:
        with self._connect() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    workflow_type TEXT,
                    created_at TEXT,
                    created_by TEXT,
                    status TEXT,
                    human_review_status TEXT,
                    retention_category TEXT,
                    summary TEXT
                );
                CREATE TABLE IF NOT EXISTS input_files (
                    file_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    payload TEXT
                );
                CREATE TABLE IF NOT EXISTS findings (
                    finding_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    payload TEXT
                );
                CREATE TABLE IF NOT EXISTS llm_responses (
                    response_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    payload TEXT
                );
                CREATE TABLE IF NOT EXISTS validation_results (
                    validation_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    payload TEXT
                );
                CREATE TABLE IF NOT EXISTS human_review_actions (
                    action_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    payload TEXT
                );
                CREATE TABLE IF NOT EXISTS export_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    payload TEXT
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    timestamp TEXT,
                    event_type TEXT,
                    actor TEXT,
                    details TEXT
                );
                """
            )
            # Backward-compatible migration for ledgers created before the
            # retention_category column existed: add it if missing so existing
            # on-disk DBs keep working.
            cols = {r["name"] for r in c.execute("PRAGMA table_info(runs)")}
            if "retention_category" not in cols:
                c.execute("ALTER TABLE runs ADD COLUMN retention_category TEXT")
            c.commit()

    # ------------------------------------------------------------------ #
    # Runs
    # ------------------------------------------------------------------ #
    def create_run(self, run: WorkflowRun) -> str:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs "
                "(run_id, workflow_type, created_at, created_by, status, "
                "human_review_status, retention_category, summary) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    run.run_id,
                    run.workflow_type,
                    run.created_at.isoformat(),
                    run.created_by,
                    run.status.value,
                    run.human_review_status.value,
                    run.retention_category.value,
                    _json(run.summary),
                ),
            )
            conn.commit()
        for f in run.input_files:
            self.store_input_file(run.run_id, f)
        return run.run_id

    def update_run_status(
        self,
        run_id: str,
        status: str,
        human_review_status: Optional[str] = None,
        summary: Optional[dict] = None,
        retention_category: Optional[str] = None,
    ) -> None:
        sets = ["status = ?"]
        params: list[Any] = [status]
        if human_review_status is not None:
            sets.append("human_review_status = ?")
            params.append(human_review_status)
        if summary is not None:
            sets.append("summary = ?")
            params.append(_json(summary))
        if retention_category is not None:
            sets.append("retention_category = ?")
            params.append(retention_category)
        params.append(run_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE runs SET {', '.join(sets)} WHERE run_id = ?", params
            )
            conn.commit()

    def set_retention(self, run_id: str, retention_category: str) -> None:
        """Update only the records-retention category for a run."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET retention_category = ? WHERE run_id = ?",
                (retention_category, run_id),
            )
            conn.commit()

    def list_runs(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["summary"] = json.loads(d["summary"]) if d["summary"] else {}
            d["retention_category"] = (
                d.get("retention_category") or "draft_working")
            out.append(d)
        return out

    def get_run(self, run_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            run = dict(row)
            run["summary"] = json.loads(run["summary"]) if run["summary"] else {}
            run["retention_category"] = (
                run.get("retention_category") or "draft_working")
            run["input_files"] = [
                json.loads(r["payload"])
                for r in conn.execute(
                    "SELECT payload FROM input_files WHERE run_id = ?", (run_id,)
                ).fetchall()
            ]
            run["findings"] = [
                json.loads(r["payload"])
                for r in conn.execute(
                    "SELECT payload FROM findings WHERE run_id = ?", (run_id,)
                ).fetchall()
            ]
            run["llm_responses"] = [
                json.loads(r["payload"])
                for r in conn.execute(
                    "SELECT payload FROM llm_responses WHERE run_id = ?", (run_id,)
                ).fetchall()
            ]
            run["validation_results"] = [
                json.loads(r["payload"])
                for r in conn.execute(
                    "SELECT payload FROM validation_results WHERE run_id = ?",
                    (run_id,),
                ).fetchall()
            ]
            run["human_review_actions"] = [
                json.loads(r["payload"])
                for r in conn.execute(
                    "SELECT payload FROM human_review_actions WHERE run_id = ?",
                    (run_id,),
                ).fetchall()
            ]
            run["export_artifacts"] = [
                json.loads(r["payload"])
                for r in conn.execute(
                    "SELECT payload FROM export_artifacts WHERE run_id = ?",
                    (run_id,),
                ).fetchall()
            ]
        return run

    def _list_payloads(self, table: str, run_id: str) -> list[dict]:
        """Return the JSON-decoded ``payload`` column for a child table.

        ``table`` is an internal, fixed identifier (never user input).
        """
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT payload FROM {table} WHERE run_id = ?", (run_id,)
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def list_findings(self, run_id: str) -> list[dict]:
        return self._list_payloads("findings", run_id)

    def list_llm_responses(self, run_id: str) -> list[dict]:
        return self._list_payloads("llm_responses", run_id)

    def list_validation_results(self, run_id: str) -> list[dict]:
        return self._list_payloads("validation_results", run_id)

    def list_human_review_actions(self, run_id: str) -> list[dict]:
        return self._list_payloads("human_review_actions", run_id)

    def list_export_artifacts(self, run_id: str) -> list[dict]:
        return self._list_payloads("export_artifacts", run_id)

    def list_input_files(self, run_id: str) -> list[dict]:
        return self._list_payloads("input_files", run_id)

    # ------------------------------------------------------------------ #
    # Child records
    # ------------------------------------------------------------------ #
    def store_input_file(self, run_id: str, input_file: InputFile) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO input_files (file_id, run_id, payload) "
                "VALUES (?,?,?)",
                (input_file.file_id, run_id, _model_json(input_file)),
            )
            conn.commit()

    def store_findings(
        self, run_id: str, findings: list[DeterministicFinding]
    ) -> None:
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO findings (finding_id, run_id, payload) "
                "VALUES (?,?,?)",
                [(f.finding_id, run_id, _model_json(f)) for f in findings],
            )
            conn.commit()

    def store_llm_response(self, run_id: str, response: LLMResponse) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_responses "
                "(response_id, run_id, payload) VALUES (?,?,?)",
                (response.response_id, run_id, _model_json(response)),
            )
            conn.commit()

    def store_validation_result(
        self, run_id: str, result: ValidationResult
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO validation_results "
                "(validation_id, run_id, payload) VALUES (?,?,?)",
                (result.validation_id, run_id, _model_json(result)),
            )
            conn.commit()

    def store_human_review_action(
        self, run_id: str, action: HumanReviewAction
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO human_review_actions "
                "(action_id, run_id, payload) VALUES (?,?,?)",
                (action.action_id, run_id, _model_json(action)),
            )
            conn.commit()

    def store_export_artifact(
        self, run_id: str, artifact: ExportArtifact
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO export_artifacts "
                "(artifact_id, run_id, payload) VALUES (?,?,?)",
                (artifact.artifact_id, run_id, _model_json(artifact)),
            )
            conn.commit()

    # ------------------------------------------------------------------ #
    # Audit events (append-only). Owned here so callers never reach into the
    # raw connection across threads; AuditLog drives these.
    # ------------------------------------------------------------------ #
    def append_audit_event(
        self,
        *,
        event_id: str,
        run_id: str,
        timestamp: str,
        event_type: str,
        actor: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_events "
                "(event_id, run_id, timestamp, event_type, actor, details) "
                "VALUES (?,?,?,?,?,?)",
                (
                    event_id,
                    run_id,
                    timestamp,
                    event_type,
                    actor,
                    json.dumps(details or {}, default=str),
                ),
            )
            conn.commit()

    def list_audit_events(self, run_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_events WHERE run_id = ? "
                "ORDER BY timestamp ASC",
                (run_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["details"] = json.loads(d["details"]) if d["details"] else {}
            out.append(d)
        return out

    # ------------------------------------------------------------------ #
    # Cross-run AI interaction history (Tier 1: searchable AI usage log)
    # ------------------------------------------------------------------ #
    def list_llm_interactions(self) -> list[dict]:
        """Return one row per stored LLM response, joined with run metadata.

        Powers the AI Audit Log surface. Each row carries the run's workflow
        type, validation status, and the model / prompt-template metadata so AI
        usage is reviewable in one place. ``ai_draft_status`` is derived from
        recorded human-review actions (``approve_draft`` -> final,
        ``reject_ai_explanation`` -> rejected, else draft).
        """
        with self._connect() as conn:
            llm_rows = conn.execute(
                "SELECT l.run_id AS run_id, l.payload AS payload, "
                "r.workflow_type AS workflow_type, r.created_at AS created_at, "
                "r.status AS status, r.summary AS summary "
                "FROM llm_responses l LEFT JOIN runs r ON l.run_id = r.run_id"
            ).fetchall()
            action_rows = conn.execute(
                "SELECT run_id, payload FROM human_review_actions"
            ).fetchall()

        actions_by_run: dict[str, set[str]] = {}
        for ar in action_rows:
            payload = json.loads(ar["payload"])
            actions_by_run.setdefault(ar["run_id"], set()).add(
                payload.get("action", ""))

        out: list[dict] = []
        for row in llm_rows:
            resp = json.loads(row["payload"])
            summary = json.loads(row["summary"]) if row["summary"] else {}
            names = actions_by_run.get(row["run_id"], set())
            if "approve_draft" in names:
                draft_status = "final (human-approved)"
            elif "reject_ai_explanation" in names:
                draft_status = "rejected"
            else:
                draft_status = "draft"
            out.append({
                "run_id": row["run_id"],
                "workflow_type": row["workflow_type"],
                "created_at": resp.get("created_at") or row["created_at"],
                "model_provider": resp.get("model_provider"),
                "model_name": resp.get("model_name"),
                "prompt_template_version": resp.get("prompt_template_version"),
                "validation_status": summary.get("validation_status", "n/a"),
                "ai_draft_status": draft_status,
                "referenced_source_rows": resp.get("referenced_source_rows", []),
                "response_json": resp.get("response_json", {}),
            })
        out.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        return out

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        # File-backed ledgers hold no long-lived connection (per-operation
        # connections are already closed). Only the shared in-memory connection
        # needs closing.
        if self._mem_conn is not None:
            self._mem_conn.close()
            self._mem_conn = None
