"""Tests for the exportable AI usage log (src/core/ai_usage_log.py)."""
from __future__ import annotations

import csv
import json

from src.core.ai_usage_log import (
    CSV_COLUMNS,
    CSV_FILE_NAME,
    JSON_FILE_NAME,
    ai_usage_log_rows,
    export_ai_usage_log,
)
from src.core.run_ledger import RunLedger
from src.core.schemas import (
    HumanReviewAction,
    InputFile,
    LLMResponse,
    WorkflowRun,
)


def _seed_two_runs(ledger):
    ledger.create_run(WorkflowRun(
        run_id="run-a", workflow_type="bank_reconciliation",
        created_by="tester",
        input_files=[InputFile(
            file_name="bank.csv", file_type="csv", file_hash="h",
            parser_used="csv_loader", row_count=1)]))
    ledger.update_run_status("run-a", "completed",
                             summary={"validation_status": "passed"})
    ledger.store_llm_response("run-a", LLMResponse(
        prompt_template_version="bank.v1", model_provider="mock",
        model_name="mock-llm", response_json={"summary": "ok"},
        referenced_source_rows=["bank:0", "bank:1"]))

    ledger.create_run(WorkflowRun(
        run_id="run-b", workflow_type="budget_variance", created_by="tester"))
    ledger.store_llm_response("run-b", LLMResponse(
        prompt_template_version="budget.v1", model_provider="mock",
        model_name="mock-llm", response_json={"summary": "ok"},
        referenced_source_rows=[]))
    ledger.store_human_review_action("run-b", HumanReviewAction(
        run_id="run-b", action="approve_draft", actor="director"))


# --------------------------------------------------------------------------- #
# Row builder
# --------------------------------------------------------------------------- #
def test_rows_have_expected_shape(tmp_path):
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    _seed_two_runs(ledger)
    rows = ai_usage_log_rows(ledger)
    assert len(rows) == 2
    by_run = {r["run_id"]: r for r in rows}
    a = by_run["run-a"]
    assert set(a.keys()) == set(CSV_COLUMNS)
    assert a["workflow_type"] == "bank_reconciliation"
    assert a["prompt_template_version"] == "bank.v1"
    assert a["validation_status"] == "passed"
    assert a["ai_draft_status"] == "draft"
    assert a["referenced_source_row_count"] == 2
    assert by_run["run-b"]["ai_draft_status"] == "final (human-approved)"
    assert by_run["run-b"]["referenced_source_row_count"] == 0


# --------------------------------------------------------------------------- #
# Export both
# --------------------------------------------------------------------------- #
def test_export_both_writes_csv_and_json(tmp_path):
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    _seed_two_runs(ledger)
    out = tmp_path / "logs"
    paths = export_ai_usage_log(ledger, out)
    names = {p.name for p in paths}
    assert names == {CSV_FILE_NAME, JSON_FILE_NAME}
    for p in paths:
        assert p.is_file()

    # CSV: header + 2 data rows with the expected columns.
    with (out / CSV_FILE_NAME).open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == list(CSV_COLUMNS)
        data = list(reader)
    assert len(data) == 2
    assert {d["run_id"] for d in data} == {"run-a", "run-b"}

    # JSON: full records (includes response_json + referenced_source_rows).
    records = json.loads((out / JSON_FILE_NAME).read_text(encoding="utf-8"))
    assert len(records) == 2
    a = next(r for r in records if r["run_id"] == "run-a")
    assert a["referenced_source_rows"] == ["bank:0", "bank:1"]
    assert a["response_json"] == {"summary": "ok"}


# --------------------------------------------------------------------------- #
# fmt selection
# --------------------------------------------------------------------------- #
def test_export_csv_only(tmp_path):
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    _seed_two_runs(ledger)
    out = tmp_path / "logs"
    paths = export_ai_usage_log(ledger, out, fmt="csv")
    assert [p.name for p in paths] == [CSV_FILE_NAME]
    assert (out / CSV_FILE_NAME).is_file()
    assert not (out / JSON_FILE_NAME).exists()


def test_export_json_only(tmp_path):
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    _seed_two_runs(ledger)
    out = tmp_path / "logs"
    paths = export_ai_usage_log(ledger, out, fmt="json")
    assert [p.name for p in paths] == [JSON_FILE_NAME]
    assert (out / JSON_FILE_NAME).is_file()
    assert not (out / CSV_FILE_NAME).exists()


def test_export_bad_fmt_raises(tmp_path):
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    import pytest
    with pytest.raises(ValueError):
        export_ai_usage_log(ledger, tmp_path / "logs", fmt="xml")


# --------------------------------------------------------------------------- #
# Empty ledger
# --------------------------------------------------------------------------- #
def test_export_empty_ledger(tmp_path):
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    out = tmp_path / "logs"
    export_ai_usage_log(ledger, out)

    # Header-only CSV.
    with (out / CSV_FILE_NAME).open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == list(CSV_COLUMNS)
        assert list(reader) == []

    # Empty JSON list.
    assert json.loads((out / JSON_FILE_NAME).read_text(encoding="utf-8")) == []
    assert ai_usage_log_rows(ledger) == []
