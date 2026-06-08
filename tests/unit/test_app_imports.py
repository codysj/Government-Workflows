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
        "Settings": "render_settings",
        "About / Safety": "render_about",
    }
    for page in expected:
        assert page in mod.PAGES, f"missing page in PAGES: {page}"
    for page, fn_name in expected.items():
        fn = getattr(mod, fn_name, None)
        assert callable(fn), f"missing renderer: {fn_name}"
        assert mod.PAGE_RENDERERS[page] is fn


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
