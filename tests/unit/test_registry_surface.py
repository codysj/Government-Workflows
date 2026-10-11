"""Workflow registry / pipeline surface and app-settings persistence."""
from __future__ import annotations

import importlib
from pathlib import Path


def test_registry_exposes_pipeline_and_descriptors():
    """The UI drives a single uniform pipeline + descriptor list."""
    wfr = importlib.import_module("app.workflow_registry")
    assert callable(wfr.run_workflow)
    assert callable(wfr.record_human_review_action)
    descriptors = wfr.list_descriptors()
    types = {d.workflow_type for d in descriptors}
    assert {"bank_reconciliation", "budget_variance", "report_review", "freeform"} <= types


def test_registry_exposes_all_eight_workflows():
    """The four Tyler-era workflows are wired beside the original four."""
    wfr = importlib.import_module("app.workflow_registry")
    types = {d.workflow_type for d in wfr.list_descriptors()}
    assert types == {
        "bank_reconciliation", "budget_variance", "report_review",
        "transaction_search", "ap_duplicate_review", "je_upload_prep",
        "po_invoice_review", "freeform",
    }
    # CLI registry resolves the same eight (canonical names + aliases).
    from src.workflows import registry as cli_registry
    assert {s.workflow_type for s in cli_registry.list_specs()} == types
    for name in ("transaction-search", "ap-duplicate-review",
                 "je-upload-prep", "po-invoice-review"):
        assert cli_registry.get_spec(name) is not None
    # Sample inputs resolve to existing files (query passes through as text).
    for name in ("transaction_search", "ap_duplicate_review",
                 "je_upload_prep", "po_invoice_review"):
        sample = cli_registry.get_spec(name).sample_inputs()
        for key, val in sample.items():
            if key == "query":
                assert isinstance(val, str) and val.strip()
            else:
                assert Path(val).is_file(), f"{name}.{key}: {val}"


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
