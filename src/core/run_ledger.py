"""SQLite run ledger (Phase 3.4).

Persists runs, input files, findings, LLM responses, validation results, human
review actions, and export artifacts. Complex fields are serialized as JSON.
Supports ':memory:' for tests.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

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
    """Thin SQLite-backed ledger. One connection per instance."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    # ------------------------------------------------------------------ #
    def _create_tables(self) -> None:
        c = self.conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                workflow_type TEXT,
                created_at TEXT,
                created_by TEXT,
                status TEXT,
                human_review_status TEXT,
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
        c.commit()

    # ------------------------------------------------------------------ #
    # Runs
    # ------------------------------------------------------------------ #
    def create_run(self, run: WorkflowRun) -> str:
        self.conn.execute(
            "INSERT OR REPLACE INTO runs "
            "(run_id, workflow_type, created_at, created_by, status, "
            "human_review_status, summary) VALUES (?,?,?,?,?,?,?)",
            (
                run.run_id,
                run.workflow_type,
                run.created_at.isoformat(),
                run.created_by,
                run.status.value,
                run.human_review_status.value,
                _json(run.summary),
            ),
        )
        self.conn.commit()
        for f in run.input_files:
            self.store_input_file(run.run_id, f)
        return run.run_id

    def update_run_status(
        self,
        run_id: str,
        status: str,
        human_review_status: Optional[str] = None,
        summary: Optional[dict] = None,
    ) -> None:
        sets = ["status = ?"]
        params: list[Any] = [status]
        if human_review_status is not None:
            sets.append("human_review_status = ?")
            params.append(human_review_status)
        if summary is not None:
            sets.append("summary = ?")
            params.append(_json(summary))
        params.append(run_id)
        self.conn.execute(
            f"UPDATE runs SET {', '.join(sets)} WHERE run_id = ?", params
        )
        self.conn.commit()

    def list_runs(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["summary"] = json.loads(d["summary"]) if d["summary"] else {}
            out.append(d)
        return out

    def get_run(self, run_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        run = dict(row)
        run["summary"] = json.loads(run["summary"]) if run["summary"] else {}
        run["input_files"] = [
            json.loads(r["payload"])
            for r in self.conn.execute(
                "SELECT payload FROM input_files WHERE run_id = ?", (run_id,)
            ).fetchall()
        ]
        run["findings"] = [
            json.loads(r["payload"])
            for r in self.conn.execute(
                "SELECT payload FROM findings WHERE run_id = ?", (run_id,)
            ).fetchall()
        ]
        run["llm_responses"] = [
            json.loads(r["payload"])
            for r in self.conn.execute(
                "SELECT payload FROM llm_responses WHERE run_id = ?", (run_id,)
            ).fetchall()
        ]
        run["validation_results"] = [
            json.loads(r["payload"])
            for r in self.conn.execute(
                "SELECT payload FROM validation_results WHERE run_id = ?", (run_id,)
            ).fetchall()
        ]
        run["human_review_actions"] = [
            json.loads(r["payload"])
            for r in self.conn.execute(
                "SELECT payload FROM human_review_actions WHERE run_id = ?", (run_id,)
            ).fetchall()
        ]
        run["export_artifacts"] = [
            json.loads(r["payload"])
            for r in self.conn.execute(
                "SELECT payload FROM export_artifacts WHERE run_id = ?", (run_id,)
            ).fetchall()
        ]
        return run

    # ------------------------------------------------------------------ #
    # Child records
    # ------------------------------------------------------------------ #
    def store_input_file(self, run_id: str, input_file: InputFile) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO input_files (file_id, run_id, payload) "
            "VALUES (?,?,?)",
            (input_file.file_id, run_id, _model_json(input_file)),
        )
        self.conn.commit()

    def store_findings(
        self, run_id: str, findings: list[DeterministicFinding]
    ) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO findings (finding_id, run_id, payload) "
            "VALUES (?,?,?)",
            [(f.finding_id, run_id, _model_json(f)) for f in findings],
        )
        self.conn.commit()

    def store_llm_response(self, run_id: str, response: LLMResponse) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO llm_responses (response_id, run_id, payload) "
            "VALUES (?,?,?)",
            (response.response_id, run_id, _model_json(response)),
        )
        self.conn.commit()

    def store_validation_result(
        self, run_id: str, result: ValidationResult
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO validation_results "
            "(validation_id, run_id, payload) VALUES (?,?,?)",
            (result.validation_id, run_id, _model_json(result)),
        )
        self.conn.commit()

    def store_human_review_action(
        self, run_id: str, action: HumanReviewAction
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO human_review_actions "
            "(action_id, run_id, payload) VALUES (?,?,?)",
            (action.action_id, run_id, _model_json(action)),
        )
        self.conn.commit()

    def store_export_artifact(
        self, run_id: str, artifact: ExportArtifact
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO export_artifacts "
            "(artifact_id, run_id, payload) VALUES (?,?,?)",
            (artifact.artifact_id, run_id, _model_json(artifact)),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        self.conn.close()
