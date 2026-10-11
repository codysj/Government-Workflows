"""Guardrails against REAL model output, offline.

``tests/fixtures/real_llm/*.json`` are responses recorded from a real model on
the bundled synthetic data (``python -m src.eval.real_llm``). Each is replayed
through the full pipeline:

* the untouched response must PASS validation (no false positives on real
  phrasing: "$12,450.00", "17.50%", "INV-10234", "March 2026", "7-row", lists);
* real responses where the model hallucinated (``rejected/``) must FAIL;
* every adversarial edit must FAIL (an invented amount, percent, date,
  identifier, row reference, or approval language is never "passed").
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src.core.pipeline import run_workflow
from src.core.run_ledger import RunLedger
from src.workflows.registry import get_descriptor

_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "real_llm"
FIXTURES = sorted(_DIR.glob("*.json"))
# Real responses where the model DID hallucinate (e.g. an invoice number that
# exists nowhere in the data). Kept as permanent must-reject cases.
REJECTED = sorted((_DIR / "rejected").glob("*.json"))


class ReplayProvider:
    model_provider = "replay"
    model_name = "replay"

    def __init__(self, response: dict):
        self.response = response

    def generate_structured_response(self, prompt, schema=None):
        return copy.deepcopy(self.response)

    def generate_text_response(self, prompt):
        return str(self.response.get("summary", ""))

    def mock_response(self, prompt, schema=None):
        return copy.deepcopy(self.response)


def _run(workflow_type: str, response: dict):
    d = get_descriptor(workflow_type)
    result = run_workflow(d.workflow_type, d.sample_inputs(),
                          ledger=RunLedger(":memory:"), provider=ReplayProvider(response))
    return result.validation


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _append_summary(text: str):
    def mutate(r: dict) -> dict:
        r["summary"] = f"{r.get('summary', '')} {text}"
        return r
    return mutate


def _cite(ref: str):
    def mutate(r: dict) -> dict:
        r["referenced_source_rows"] = list(r.get("referenced_source_rows") or []) + [ref]
        return r
    return mutate


ADVERSARIAL = {
    "invented_amount": _append_summary("A further $48,213.77 is unaccounted for."),
    "invented_percent": _append_summary("That is an 83.7% increase."),
    "invented_count": _append_summary("There were 37 exceptions in total."),
    "invented_date": _append_summary("The last payment cleared on 1999-01-04."),
    "invented_month": _append_summary("This pattern began in February 1998."),
    "invented_identifier": _append_summary("Vendor V-99871 also billed invoice INV-777001."),
    "invented_prose_ref": _append_summary("See row ap_invoices:98765 for details."),
    "invented_cited_ref": _cite("ledger:424242"),
    "approval_language": _append_summary("I certify this is complete."),
}


def test_fixtures_present():
    assert len(FIXTURES) >= 8 and REJECTED


@pytest.mark.parametrize("path", REJECTED, ids=lambda p: p.stem)
def test_real_hallucination_is_rejected(path):
    fx = _load(path)
    v = _run(fx["workflow_type"], fx["response_json"])
    assert not v.passed


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_real_response_passes(path):
    fx = _load(path)
    v = _run(fx["workflow_type"], fx["response_json"])
    assert v.passed, v.errors


@pytest.mark.parametrize("kind", sorted(ADVERSARIAL))
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_adversarial_edit_fails(path, kind):
    fx = _load(path)
    v = _run(fx["workflow_type"], ADVERSARIAL[kind](copy.deepcopy(fx["response_json"])))
    assert not v.passed, f"{kind} slipped through"
