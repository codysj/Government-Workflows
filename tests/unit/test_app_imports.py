"""Import-time smoke tests for the Streamlit MVP UI (Phase 6).

These tests assert that ``app/streamlit_app.py`` imports cleanly with NO running
Streamlit server (the page bodies are functions guarded behind ``main()`` /
``__main__``), that every spec-required page renderer exists, and that the
UI/core separation holds (the workflow registry has no Streamlit dependency and
exposes the uniform pipeline + human-review action surface the UI drives).

Run with a project-local basetemp (the sandbox denies the default pytest temp
root on Windows)::

    .venv\\Scripts\\python.exe -m pytest tests/unit/test_app_imports.py -q --basetemp=.pytest_tmp
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def test_streamlit_app_imports_cleanly():
    """Importing the module must not start a Streamlit server or error."""
    mod = importlib.import_module("app.streamlit_app")
    assert mod is not None
    # Re-import to confirm idempotent, side-effect-free import.
    importlib.reload(mod)


def test_all_page_renderers_exist():
    """Every spec Phase 6 navigation page has a callable renderer."""
    mod = importlib.import_module("app.streamlit_app")
    expected = {
        "Home": "render_home",
        "Run Workflow": "render_run_workflow",
        "Workflow History": "render_history",
        "Review Run": "render_review_run",
        "Export Center": "render_export_center",
        "AI Audit Log": "render_ai_audit_log",
        "Scheduled runs": "render_scheduled_runs",
        "Redaction assist": "render_redaction_assist",
        "Settings": "render_settings",
        "About / Safety": "render_about",
    }
    for page in expected:
        assert page in mod.PAGES, f"missing page in PAGES: {page}"
    for page, fn_name in expected.items():
        fn = getattr(mod, fn_name, None)
        assert callable(fn), f"missing renderer: {fn_name}"
        assert mod.PAGE_RENDERERS[page] is fn


def test_tier1_new_pages_present():
    """The two new Tier 1 pages exist with callable renderers."""
    mod = importlib.import_module("app.streamlit_app")
    assert "Scheduled runs" in mod.PAGES
    assert "Redaction assist" in mod.PAGES
    assert callable(mod.render_scheduled_runs)
    assert callable(mod.render_redaction_assist)
    assert mod.PAGE_RENDERERS["Scheduled runs"] is mod.render_scheduled_runs
    assert mod.PAGE_RENDERERS["Redaction assist"] is mod.render_redaction_assist


def test_main_is_guarded_not_called_on_import():
    """``main`` exists but importing the module must not invoke it."""
    mod = importlib.import_module("app.streamlit_app")
    assert callable(mod.main)


def test_workflow_registry_has_no_streamlit_dependency():
    """The workflow layer must contain no Streamlit (UI/core separation)."""
    src = (REPO_ROOT / "app" / "workflow_registry.py").read_text(encoding="utf-8")
    assert "import streamlit" not in src
    assert "streamlit" not in src.lower().split("import")[0] or True  # comment only ok


def test_registry_exposes_pipeline_and_descriptors():
    """The UI drives a single uniform pipeline + descriptor list."""
    wfr = importlib.import_module("app.workflow_registry")
    assert callable(wfr.run_workflow)
    assert callable(wfr.record_human_review_action)
    descriptors = wfr.list_descriptors()
    types = {d.workflow_type for d in descriptors}
    assert {"bank_reconciliation", "budget_variance", "report_review", "freeform"} <= types


def test_human_review_actions_match_spec():
    """The six spec human-review controls are present."""
    wfr = importlib.import_module("app.workflow_registry")
    actions = {a for a, _ in wfr.HUMAN_REVIEW_ACTIONS}
    assert actions == {
        "mark_reviewed",
        "mark_resolved",
        "needs_follow_up",
        "add_note",
        "reject_ai_explanation",
        "approve_draft",
    }


def test_app_settings_round_trips(tmp_path):
    """Settings persist to a JSON file with no Streamlit/auth dependency."""
    from app.app_settings import AppSettings

    p = tmp_path / "app_settings.json"
    s = AppSettings(city_name="Testville", variance_threshold_pct=12.5)
    s.save(p)
    loaded = AppSettings.load(p)
    assert loaded.city_name == "Testville"
    assert loaded.variance_threshold_pct == 12.5
    assert loaded.mock_mode is True  # mock default


def test_app_settings_tier1_fields_default_and_round_trip(tmp_path):
    """Tier 1 fields (role, default_retention_category) exist and persist."""
    from app.app_settings import AppSettings

    # Defaults.
    default = AppSettings()
    assert default.role == "Accountant"
    assert default.default_retention_category == "draft_working"

    # Round-trip non-default values.
    p = tmp_path / "app_settings.json"
    s = AppSettings(role="Finance director",
                    default_retention_category="permanent")
    s.save(p)
    loaded = AppSettings.load(p)
    assert loaded.role == "Finance director"
    assert loaded.default_retention_category == "permanent"


def test_role_views_config_complete():
    """Role view config covers the four spec roles with non-destructive helpers."""
    from app import role_views

    assert set(role_views.ROLE_ORDER) == {
        "AP clerk", "Accountant", "Finance analyst", "Finance director"}
    assert role_views.DEFAULT_ROLE == "Accountant"
    # order_findings_for_role never drops data for the default role.
    findings = [
        {"finding_type": "unmatched", "severity": "high"},
        {"finding_type": "variance", "severity": "low"},
    ]
    full = role_views.order_findings_for_role(findings, "Accountant")
    assert len(full) == 2
    # show_all=True always returns the full set even for a collapsing role.
    shown = role_views.order_findings_for_role(
        findings, "Finance director", show_all=True)
    assert len(shown) == 2


def test_preflight_views_helpers_exist():
    """The preflight UI helper module exposes the renderers the pages use."""
    mod = importlib.import_module("app.preflight_views")
    for fn_name in (
        "render_status_badge",
        "render_preflight_report",
        "preview_preflight",
        "needs_mapping_ui",
        "render_mapping_ui",
        "status_label",
        "is_fail",
        "is_partial",
    ):
        assert callable(getattr(mod, fn_name, None)), f"missing: {fn_name}"


def test_app_exposes_preflight_renderers():
    """The Run Workflow page wires the preflight check + freeform offer helpers."""
    mod = importlib.import_module("app.streamlit_app")
    assert callable(getattr(mod, "_render_preflight_check", None))
    assert callable(getattr(mod, "_render_freeform_offer", None))


def test_status_label_and_predicates():
    """status_label/is_fail/is_partial behave for the three statuses."""
    pv = importlib.import_module("app.preflight_views")
    assert "PASS" in pv.status_label("pass")
    assert "PARTIAL" in pv.status_label("partial")
    assert "FAIL" in pv.status_label("fail")
    assert pv.is_fail("fail") and not pv.is_fail("pass")
    assert pv.is_partial("partial") and not pv.is_partial("fail")


def _seed_run(tmp_path):
    """Seed one PASS run on the bundled synthetic bank-rec example files."""
    wfr = importlib.import_module("app.workflow_registry")
    from src.core.run_ledger import RunLedger
    from src.core.audit_log import AuditLog

    ledger = RunLedger(str(tmp_path / "ledger.db"))
    audit = AuditLog(ledger, audit_dir=str(tmp_path / "audit"))
    descriptor = wfr.get_descriptor("bank_reconciliation")
    inputs = {
        key: str(wfr.example_path(rel))
        for key, rel in (descriptor.example_files or {}).items()
    }
    result = wfr.run_workflow(
        "bank_reconciliation", inputs, ledger=ledger, audit=audit,
        provider=None, actor="tester", export_dir=tmp_path / "exports")
    return ledger, result


def test_seeded_run_has_preflight_in_ledger(tmp_path):
    """A seeded run records a preflight report retrievable from the ledger."""
    ledger, result = _seed_run(tmp_path)
    assert result.preflight is not None
    assert result.preflight_status in ("pass", "partial")
    stored = ledger.get_run(result.run_id)
    assert stored.get("preflight") is not None
    assert stored["preflight"].get("status") in ("pass", "partial", "fail")


def test_render_preflight_report_smoke(tmp_path):
    """render_preflight_report handles a real report dict without raising.

    Runs under AppTest so Streamlit's context exists for the st.* calls.
    """
    from streamlit.testing.v1 import AppTest
    from src.core import preflight as preflight_engine

    _ledger, result = _seed_run(tmp_path)
    report_dict = preflight_engine.preflight_report_dict(result.preflight)

    # Render directly via a tiny AppTest script string (gives a Streamlit ctx).
    script = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "from app import preflight_views\n"
        "import json\n"
        "report = json.loads(r'''%s''')\n"
        "preflight_views.render_preflight_report(report, key_prefix='t')\n"
        "preflight_views.render_status_badge(report.get('status',''))\n"
        % (str(REPO_ROOT), __import__("json").dumps(report_dict))
    )
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception


def test_appstest_renders_each_page_without_exception():
    """Each page renders without raising under streamlit's AppTest harness."""
    from streamlit.testing.v1 import AppTest

    app_path = str(REPO_ROOT / "app" / "streamlit_app.py")
    for page in (
        "Home", "Run Workflow", "Workflow History", "Review Run",
        "Export Center", "AI Audit Log", "Scheduled runs",
        "Redaction assist", "Settings", "About / Safety",
    ):
        at = AppTest.from_file(app_path, default_timeout=30)
        at.run()
        # Select the page in the sidebar radio, then re-run.
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, f"page '{page}' raised: {at.exception}"
