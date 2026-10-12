"""MCP server: drive every tool through the real protocol (in-memory client).

Runs offline on the mock LLM. The real-model check is a separate, recorded run
(see docs/evaluation.md); nothing here touches the network. Tool arguments are
treated as untrusted (a remote model can be steered by file or web content), so
the confinement and download rules are tested from the attacker's side too.
"""
from __future__ import annotations

import io
import sys

import anyio
import pytest
from mcp.client import Client

import mcp_server
from api.services.settings import ApiSettings
from mcp_server import (
    UploadedFile,
    _check_download_url,
    _CheckedRedirects,
    _settings_from_env,
    build_server,
)
from src.core.pipeline import _input_files_for
from src.workflows.registry import SYNTHETIC_DIR


def _settings(tmp_path):
    return ApiSettings(
        ledger_db_path=tmp_path / "ledger.db", audit_dir=tmp_path / "audit",
        export_dir=tmp_path / "exports", upload_dir=tmp_path / "uploads",
        schedules_path=tmp_path / "schedules.json", frontend_dist=tmp_path / "none")


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVWF_DATA_DIR", str(SYNTHETIC_DIR))  # the bundled synthetic data
    return build_server(_settings(tmp_path))


def _call(server, tool, args=None):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(tool, args or {})
    return anyio.run(go)


def _tools(server):
    async def go():
        async with Client(server) as c:
            return {t.name: t for t in (await c.list_tools()).tools}
    return anyio.run(go)


def _error(result) -> str:
    assert result.is_error
    return result.content[0].text


def test_tools_are_discoverable(server):
    assert set(_tools(server)) == {"list_workflows", "list_files", "preflight", "run_workflow",
                                   "get_run", "ask_about_run"}


def test_list_workflows(server):
    r = _call(server, "list_workflows")
    names = {w["workflow_type"] for w in r.structured_content["result"]}
    assert "bank_reconciliation" in names and len(names) == 8


def test_preflight_explains_a_wrong_file_in_plain_language(server):
    r = _call(server, "preflight", {"workflow_type": "bank_reconciliation",
                                    "files": {"bank": "tyler/vendor_list.csv",
                                              "ledger": "tyler/vendor_list.csv"}})
    assert not r.is_error
    assert r.structured_content["status"] == "fail"
    assert "Its columns are" in r.structured_content["next_steps"][0]


def test_run_then_get_then_ask(server):
    run = _call(server, "run_workflow", {"workflow_type": "ap_duplicate_review",
                                         "use_sample": True}).structured_content
    assert run["status"] == "completed" and run["findings_total"] == 13
    assert run["workflow_title"] == "AP duplicate / suspicious payment review"
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
    assert "Unknown workflow 'nope'" in _error(
        _call(server, "preflight", {"workflow_type": "nope", "use_sample": True}))
    assert "File not found" in _error(_call(server, "run_workflow", {
        "workflow_type": "bank_reconciliation", "files": {"bank": "no_such_file.csv"}}))
    assert "Unknown file slot 'bnak'" in _error(_call(server, "run_workflow", {
        "workflow_type": "bank_reconciliation", "files": {"bnak": "x.csv"}}))


# --- Host integration metadata (Claude + ChatGPT) --------------------------


def test_host_metadata_annotations_ui_and_file_params(server):
    tools = _tools(server)
    for name in ("list_workflows", "list_files", "preflight", "get_run"):
        a = tools[name].annotations
        assert a.read_only_hint is True and a.open_world_hint is False, name
    for name in ("run_workflow", "ask_about_run"):  # write a run / audit entry; may call out
        a = tools[name].annotations
        assert a.read_only_hint is False and a.destructive_hint is False and a.open_world_hint is True
    for name in ("run_workflow", "get_run"):  # MCP Apps view
        assert tools[name].meta["ui"]["resourceUri"] == "ui://govworkflows/run-view.html"
    assert tools["run_workflow"].meta["openai/fileParams"] == ["uploaded_files"]
    assert "openai/fileParams" not in (tools["preflight"].meta or {})  # preflight stays offline


def test_run_view_resource_is_served_as_an_mcp_app(server):
    async def go():
        async with Client(server) as c:
            return await c.read_resource("ui://govworkflows/run-view.html")
    content = anyio.run(go).contents[0]
    assert content.mime_type == "text/html;profile=mcp-app"
    assert "ui/initialize" in content.text and "ui/notifications/tool-result" in content.text
    assert "fetch(" not in content.text and "innerHTML" not in content.text  # display only


def test_uploaded_file_schema_matches_chatgpt_file_params():
    schema = UploadedFile.model_json_schema()
    assert {"download_url", "file_id", "mime_type", "file_name"} <= set(schema["properties"])
    assert set(schema["required"]) == {"download_url", "file_id"}


def test_govwf_home_moves_all_run_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVWF_HOME", str(tmp_path / "home"))
    s = _settings_from_env()
    for path in (s.ledger_db_path, s.audit_dir, s.export_dir, s.upload_dir, s.schedules_path):
        assert path.is_relative_to(tmp_path / "home")


# --- Data folder: the only place files are read from ------------------------


@pytest.fixture
def data_server(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "may").mkdir(parents=True)
    (data / "may" / "ap.csv").write_bytes((SYNTHETIC_DIR / "tyler" / "ap_invoice_detail.csv").read_bytes())
    (data / "notes.txt").write_text("not data")
    (tmp_path / "secret.csv").write_text("account,amount\nsecret,1\n")
    (tmp_path / ".env").write_text("LLM_API_KEY=sk-test\n")
    monkeypatch.setenv("GOVWF_DATA_DIR", str(data))
    return build_server(_settings(tmp_path)), tmp_path


def _preflight_ap(server, path):
    return _call(server, "preflight", {"workflow_type": "ap_duplicate_review",
                                       "files": {"ap_invoices": path}})


def test_data_folder_lists_and_reads_its_files(data_server):
    server, _ = data_server
    listed = _call(server, "list_files").structured_content
    assert [f["name"] for f in listed["files"]] == ["may/ap.csv"]
    ok = _preflight_ap(server, "may/ap.csv")
    assert not ok.is_error and ok.structured_content["status"] in ("pass", "partial")


@pytest.mark.parametrize("path, message", [
    ("../secret.csv", "Only files inside the data folder"),
    ("may/../../secret.csv", "Only files inside the data folder"),
    ("{tmp}/secret.csv", "Only files inside the data folder"),
    ("../.env", "not a CSV, Excel, or JSON file"),
    ("notes.txt", "not a CSV, Excel, or JSON file"),
    ("\\\\attacker.example@SSL\\share\\a.csv", "Network and device paths"),
    ("//attacker.example/share/a.csv", "Network and device paths"),
    pytest.param("Z:/elsewhere/a.csv", "Only files inside the data folder",
                 marks=pytest.mark.skipif(sys.platform != "win32", reason="drive letters are Windows-only")),
])
def test_paths_outside_the_data_folder_are_refused(data_server, path, message):
    server, tmp = data_server
    assert message in _error(_preflight_ap(server, path.format(tmp=tmp)))


def test_without_a_data_folder_nothing_is_read_by_name(tmp_path, monkeypatch):
    monkeypatch.delenv("GOVWF_DATA_DIR", raising=False)
    server = build_server(_settings(tmp_path))
    msg = _error(_preflight_ap(server, str(SYNTHETIC_DIR / "tyler" / "ap_invoice_detail.csv")))
    assert "No data folder is set" in msg
    assert not _call(server, "run_workflow", {"workflow_type": "ap_duplicate_review",
                                              "use_sample": True}).is_error


def test_list_files_does_not_follow_links_out_of_the_folder(data_server):
    server, tmp = data_server
    outside = tmp / "outside"
    outside.mkdir()
    (outside / "payroll.csv").write_text("a,b\n1,2\n")
    link = tmp / "data" / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:  # Windows symlinks need extra rights; a junction does not
        _winapi = pytest.importorskip("_winapi")
        _winapi.CreateJunction(str(outside), str(link))
    names = [f["name"] for f in _call(server, "list_files").structured_content["files"]]
    assert not any("payroll" in n for n in names)


def test_text_fields_are_never_read_as_files(tmp_path):
    secret = tmp_path / "payroll.csv"
    secret.write_text("a,b\n1,2\n")
    files = _input_files_for({"query": str(secret), "bank": str(secret)}, {"bank"})
    assert [f.input_key for f in files] == ["bank"]


def test_freeform_confirmations_must_really_be_true(server):
    text = {"task_type": "summarize", "desired_output": "memo", "relevant_context": "x",
            "sensitivity_confirmation": "false", "human_review_confirmation": "no"}
    assert "sensitivity_confirmation=true" in _error(
        _call(server, "run_workflow", {"workflow_type": "freeform", "text": text}))


# --- Chat attachments (ChatGPT file params) --------------------------------


@pytest.fixture
def fake_downloads(monkeypatch):
    """Serve attachment URLs from memory instead of the network."""
    payloads: dict[str, bytes] = {}
    opened: list[str] = []

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

    class _Opener:
        def open(self, url, timeout=None):
            opened.append(url)
            return _Response(payloads[url])

    monkeypatch.setattr(mcp_server.urllib.request, "build_opener", lambda *h: _Opener())
    return payloads, opened


AP_URL = "https://files.oaiusercontent.com/a"


def _attach(name, url=AP_URL, **extra):
    return {"download_url": url, "file_id": f"file_{name}", "file_name": name, **extra}


def test_attached_file_is_downloaded_slotted_and_run(tmp_path, monkeypatch, fake_downloads):
    payloads, opened = fake_downloads
    payloads[AP_URL] = (SYNTHETIC_DIR / "tyler" / "ap_invoice_detail.csv").read_bytes()
    monkeypatch.delenv("GOVWF_DATA_DIR", raising=False)  # attachments work without one
    server = build_server(_settings(tmp_path))
    run = _call(server, "run_workflow", {"workflow_type": "ap_duplicate_review",
                                         "uploaded_files": [_attach("AP export May.csv")]})
    assert not run.is_error, run.content[0].text
    assert run.structured_content["findings_total"] > 0  # the one required slot
    assert opened == [AP_URL]
    assert list((tmp_path / "uploads").rglob("AP export May.csv"))


@pytest.mark.parametrize("attachments, message", [
    ([_attach("export1.csv"), _attach("export2.csv")], "Which file slot"),
    ([_attach("bank.csv"), _attach("bank statement.csv")], "Which file slot"),
    ([_attach("a.csv", slot="bank"), _attach("b.csv", slot="bank")], "Two files were given"),
    ([_attach("paychecks.csv")], "Which file slot"),  # "checks" is not a whole word here
    ([_attach("notes.pdf")], "not a CSV, Excel, or JSON file"),
    ([_attach("a.csv", url="https://example.com/a.csv", slot="bank")], "Refusing to download"),
])
def test_attachment_slotting_and_host_rules(server, fake_downloads, attachments, message):
    payloads, _ = fake_downloads
    payloads[AP_URL] = b"a,b\n1,2\n"
    assert message in _error(_call(server, "run_workflow", {
        "workflow_type": "bank_reconciliation", "uploaded_files": attachments}))


def test_attachment_names_are_made_safe(tmp_path, fake_downloads):
    payloads, _ = fake_downloads
    payloads[AP_URL] = b"a,b\n1,2\n"
    for name in ("x.csv:evil.csv", "CON.csv", "..\\..\\up.csv"):
        path = mcp_server._download(UploadedFile(**_attach(name)), tmp_path, ("oaiusercontent.com",))
        assert ":" not in path.split("\\")[-1] and path.startswith(str(tmp_path))
        assert not path.lower().endswith("\\con.csv")


def test_oversized_download_is_refused_and_cleaned_up(tmp_path, monkeypatch, fake_downloads):
    payloads, _ = fake_downloads
    payloads[AP_URL] = b"x" * 200_000
    monkeypatch.setattr(mcp_server, "_MAX_DOWNLOAD_BYTES", 100_000)
    with pytest.raises(Exception, match="larger than 50 MB"):
        mcp_server._download(UploadedFile(**_attach("big.csv")), tmp_path, ("oaiusercontent.com",))
    assert list(tmp_path.iterdir()) == []


def test_redirects_are_rechecked_against_the_allowlist():
    handler = _CheckedRedirects(("oaiusercontent.com",))
    with pytest.raises(Exception, match="Refusing to download"):
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/x.csv")


@pytest.mark.parametrize("url, allowed", [
    ("https://files.oaiusercontent.com/x", True),
    ("https://oaiusercontent.com/x", True),
    ("http://files.oaiusercontent.com/x", False),
    ("https://oaiusercontent.com.evil.example/x", False),
    ("https://evil.example/oaiusercontent.com", False),
    ("https://a.openai.com@evil.example/x", False),
    ("file:///C:/Windows/win.ini", False),
])
def test_download_host_allowlist(url, allowed):
    hosts = ("oaiusercontent.com", "openai.com")
    if allowed:
        _check_download_url(url, hosts)
    else:
        with pytest.raises(Exception, match="Refusing to download"):
            _check_download_url(url, hosts)


def test_freeform_run_and_its_detail_work(server):
    """get_run used to crash on every freeform run (a confirmation flag was
    parsed as an amount)."""
    run = _call(server, "run_workflow", {"workflow_type": "freeform", "use_sample": True})
    assert not run.is_error, run.content[0].text
    assert not _call(server, "get_run", {"run_id": run.structured_content["run_id"]}).is_error


# --- The Claude extension's optional AI settings ---------------------------


@pytest.mark.parametrize("env, expected, status", [
    ({}, None, "offline (no API key set)"),
    ({"GOVWF_AI_API_KEY": "${user_config.ai_api_key}"}, None, "offline"),  # unset placeholder
    ({"LLM_API_KEY": "repo-dev-key"}, None, "offline"),  # a .env key alone never switches it on
    ({"GOVWF_AI_API_KEY": "k"}, {"LLM_MODE": "real", "LLM_PROVIDER": "anthropic",
                                  "LLM_MODEL": "claude-haiku-5-5"}, "anthropic:claude-haiku-5-5"),
    ({"GOVWF_AI_API_KEY": "k", "GOVWF_AI_PROVIDER": "openai"}, None, "set a model name"),
    ({"GOVWF_AI_API_KEY": "k", "GOVWF_AI_PROVIDER": "openai", "GOVWF_AI_MODEL": "m",
      "GOVWF_AI_BASE_URL": "https://example.test/v1/chat/completions"},
     {"LLM_MODE": "real", "LLM_PROVIDER": "openai", "LLM_MODEL": "m",
      "LLM_BASE_URL": "https://example.test/v1/chat/completions"}, "openai:m"),
    ({"GOVWF_AI_API_KEY": "k", "GOVWF_AI_PROVIDER": "acme"}, None, "unknown provider"),
])
def test_extension_ai_settings(env, expected, status):
    environ = dict(env)
    assert status in mcp_server.apply_extension_ai_settings(environ)
    if expected is None:
        assert "LLM_MODE" not in environ
    else:
        assert {k: environ[k] for k in expected} == expected
        assert environ["LLM_API_KEY"] == "k"


def test_manifest_declares_the_ai_settings_as_optional_and_the_key_as_secret():
    import json
    from pathlib import Path

    manifest = json.loads((Path(mcp_server.__file__).parent / "manifest.json").read_text(encoding="utf-8"))
    config = manifest["user_config"]
    assert config["ai_api_key"]["sensitive"] is True
    assert all(config[k]["required"] is False for k in ("ai_api_key", "ai_provider", "ai_model", "ai_base_url"))
    env = manifest["server"]["mcp_config"]["env"]
    assert env["GOVWF_AI_API_KEY"] == "${user_config.ai_api_key}"
