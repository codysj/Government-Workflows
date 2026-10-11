"""Every workflow's FAIL guidance is written for finance staff, not developers.

Hand each workflow the wrong file (or no file) and check that preflight says
what to do next in the user's terms: the upload's own label, the columns the
file actually has, and no internal field keys.
"""
from __future__ import annotations

import json

import pytest

from src.core.pipeline import run_preflight
from src.workflows.registry import SYNTHETIC_DIR, get_descriptor, list_descriptors

WRONG_FILE = str(SYNTHETIC_DIR / "tyler" / "vendor_list.csv")
TABULAR = [d.workflow_type for d in list_descriptors()
           if d.workflow_type not in ("transaction_search", "freeform")]


@pytest.mark.parametrize("workflow_type", TABULAR)
def test_wrong_file_guidance_is_plain_language(workflow_type):
    d = get_descriptor(workflow_type)
    required = [u for u in d.uploads if u.required]
    inputs = {u.key: WRONG_FILE for u in required}
    report = run_preflight(workflow_type, inputs)
    assert report.status.value == "fail"
    steps = " ".join(report.next_steps)
    assert any(f"'{u.label}'" in steps for u in required), steps
    assert "Its columns are: vendor_number, vendor_name" in steps
    assert "column-mapping step" in steps
    for u in required:  # never the bare internal key, e.g. 'ap_invoices'
        if u.key != u.label:
            assert f"'{u.key}'" not in steps, steps
    # One consolidated step per file, not one per missing column.
    assert len(report.next_steps) <= len(required)


def test_transaction_search_without_files():
    report = run_preflight("transaction_search", {"query": "payments over 5000"})
    assert report.status.value == "fail"
    assert report.next_steps[0].startswith("Upload at least one")


def test_freeform_without_sensitivity_confirmation():
    request = json.loads((SYNTHETIC_DIR / "freeform" / "messy" /
                          "fail_missing_sensitivity_request.json").read_text())
    report = run_preflight("freeform", request)
    assert report.status.value == "fail"
    assert "No real sensitive data" in report.next_steps[0]
