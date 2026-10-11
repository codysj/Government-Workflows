"""The single workflow registry, the shared pipeline surface, and app settings."""
from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

from src.core import pipeline
from src.core.audit_log import AuditLog
from src.core.run_ledger import RunLedger
from src.core.settings import AppSettings
from src.workflows import registry

ALL_EIGHT = {
    "bank_reconciliation", "budget_variance", "report_review",
    "transaction_search", "ap_duplicate_review", "je_upload_prep",
    "po_invoice_review", "freeform",
}
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_registry_lists_all_eight_workflows():
    assert {d.workflow_type for d in registry.list_descriptors()} == ALL_EIGHT


def test_every_module_exposes_the_shared_run_contract():
    for d in registry.list_descriptors():
        params = inspect.signature(d.module.run).parameters
        for name in ("provider", "ledger", "audit", "run_id", "actor", "export_dir"):
            assert name in params, f"{d.workflow_type}.run missing {name}"
        assert hasattr(d.module, "CAPABILITY"), d.workflow_type


def test_resolve_accepts_cli_names_aliases_and_workflow_types():
    for d in registry.list_descriptors():
        assert registry.resolve(d.workflow_type) is d
        assert registry.resolve(d.cli_name) is d
        for alias in d.aliases:
            assert registry.resolve(alias) is d
    assert registry.resolve("bank_reconciliation").cli_name == "bank-reconciliation"
    assert registry.resolve("nope") is None


def test_sample_inputs_point_at_existing_files():
    for d in registry.list_descriptors():
        for key, val in d.sample_inputs().items():
            if key in d.example_files:
                assert Path(val).is_file(), f"{d.workflow_type}.{key}: {val}"
    assert registry.get_descriptor("transaction_search").sample_inputs()["query"]
    assert not registry.get_descriptor("freeform").has_sample  # console: no sample


def test_only_one_run_workflow_exists():
    """M1 invariant: the pipeline is the only runner in the codebase."""
    out = subprocess.run(
        ["git", "grep", "--untracked", "-l", "^def run_workflow(", "--", "*.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert out == ["src/core/pipeline.py"]


def test_human_review_actions_match_spec():
    assert {a for a, _ in pipeline.HUMAN_REVIEW_ACTIONS} == {
        "mark_reviewed", "mark_resolved", "needs_follow_up",
        "add_note", "reject_ai_explanation", "approve_draft",
    }


def test_app_settings_round_trip(tmp_path):
    p = tmp_path / "app_settings.json"
    AppSettings(city_name="Testville", variance_threshold_pct=12.5,
                default_retention_category="permanent").save(p)
    loaded = AppSettings.load(p)
    assert loaded.city_name == "Testville"
    assert loaded.variance_threshold_pct == 12.5
    assert loaded.default_retention_category == "permanent"
    assert loaded.mock_mode is True  # mock default


def test_app_settings_missing_file_gives_defaults(tmp_path):
    s = AppSettings.load(tmp_path / "absent.json")
    assert s.default_retention_category == "draft_working"
    assert Path(s.export_dir).parts[-2:] == ("runs", "exports")


def test_seeded_run_has_preflight_in_ledger(tmp_path):
    ledger = RunLedger(str(tmp_path / "ledger.db"))
    audit = AuditLog(ledger, audit_dir=str(tmp_path / "audit"))
    result = pipeline.run_workflow(
        "bank_reconciliation",
        registry.get_descriptor("bank_reconciliation").sample_inputs(),
        ledger=ledger, audit=audit, actor="tester",
        export_dir=tmp_path / "exports")
    assert result.preflight_status in ("pass", "partial")
    assert ledger.get_run(result.run_id)["preflight"]["status"] in ("pass", "partial")


def test_api_imports_without_streamlit():
    code = "import api.main, sys; assert 'streamlit' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, check=True)


def test_cli_accepts_every_old_spelling():
    import importlib.util

    spec = importlib.util.spec_from_file_location("cli_rw", REPO_ROOT / "cli" / "run_workflow.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    parser = cli.build_parser()
    for name in ("guided_freeform", "guided-freeform", "bank_reconciliation",
                 "ap_duplicate_review", "po-match", "je_prep"):
        assert parser.parse_args([name, "--sample"]).func is cli.cmd_run


def test_pipeline_rejects_unknown_run_kwargs():
    import pytest

    d = registry.get_descriptor("bank_reconciliation")
    with pytest.raises(TypeError):
        pipeline._call_workflow(d, {}, export_dir=None, run_export_dir=None,
                                run_id="x", colum_mappings=None)
