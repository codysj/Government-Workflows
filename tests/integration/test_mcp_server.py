"""MCP server: drive every tool through the real protocol (in-memory client).

Runs offline on the mock LLM. The real-model check is a separate, recorded run
(see docs/evaluation.md); nothing here touches the network.
"""
from __future__ import annotations

import anyio
import pytest
from mcp.client import Client

from api.services.settings import ApiSettings
from mcp_server import build_server
from src.workflows.registry import SYNTHETIC_DIR


@pytest.fixture
def server(tmp_path):
    return build_server(ApiSettings(
        ledger_db_path=tmp_path / "ledger.db", audit_dir=tmp_path / "audit",
        export_dir=tmp_path / "exports", upload_dir=tmp_path / "uploads",
        schedules_path=tmp_path / "schedules.json", frontend_dist=tmp_path / "none"))


def _call(server, tool, args=None):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(tool, args or {})
    return anyio.run(go)


def test_tools_are_discoverable(server):
    async def go():
        async with Client(server) as c:
            return {t.name for t in (await c.list_tools()).tools}
    assert anyio.run(go) == {"list_workflows", "preflight", "run_workflow",
                             "get_run", "ask_about_run"}


def test_list_workflows(server):
    r = _call(server, "list_workflows")
    names = {w["workflow_type"] for w in r.structured_content["result"]}
    assert "bank_reconciliation" in names and len(names) == 8


def test_preflight_explains_a_wrong_file_in_plain_language(server):
    wrong = str(SYNTHETIC_DIR / "tyler" / "vendor_list.csv")
    r = _call(server, "preflight", {"workflow_type": "bank_reconciliation",
                                    "files": {"bank": wrong, "ledger": wrong}})
    assert not r.is_error
    assert r.structured_content["status"] == "fail"
    assert "Its columns are" in r.structured_content["next_steps"][0]


def test_run_then_get_then_ask(server):
    run = _call(server, "run_workflow", {"workflow_type": "ap_duplicate_review",
                                         "use_sample": True}).structured_content
    assert run["status"] == "completed" and run["findings_total"] == 13
    severities = [f["severity"] for f in run["top_findings"]]
    assert severities == sorted(severities, key=["critical", "high", "medium", "low", "info"].index)
    assert run["ai_validation"]["passed"] is True

    detail = _call(server, "get_run", {"run_id": run["run_id"], "max_findings": 5}).structured_content
    assert detail["findings_total"] == 13 and len(detail["findings"]) == 5

    turn = _call(server, "ask_about_run", {"run_id": run["run_id"],
                                           "question": "Which invoices look duplicated?"})
    assert not turn.is_error
    assert "validation" in turn.structured_content


def test_errors_are_plain_language(server):
    r = _call(server, "preflight", {"workflow_type": "nope", "use_sample": True})
    assert r.is_error and "Unknown workflow 'nope'" in r.content[0].text
    r = _call(server, "run_workflow", {"workflow_type": "bank_reconciliation",
                                       "files": {"bank": "C:/no/such/file.csv"}})
    assert r.is_error and "File not found" in r.content[0].text
    r = _call(server, "run_workflow", {"workflow_type": "bank_reconciliation",
                                       "files": {"bnak": "x.csv"}})
    assert r.is_error and "Unknown file slot 'bnak'" in r.content[0].text
    r = _call(server, "get_run", {"run_id": "missing"})
    assert r.is_error and "No run with id" in r.content[0].text
