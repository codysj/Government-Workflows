"""Health + workflow catalog endpoints."""
from __future__ import annotations

WORKFLOW_TYPES = {
    "bank_reconciliation", "budget_variance", "report_review",
    "transaction_search", "ap_duplicate_review", "je_upload_prep",
    "po_invoice_review", "freeform",
}


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["app"] == "municipal-finance-ai"
    assert body["llm_mode"] == "mock"
    assert isinstance(body["version"], str) and body["version"]


def test_workflows_list_shape(client):
    r = client.get("/api/workflows")
    assert r.status_code == 200
    workflows = r.json()["workflows"]
    assert len(workflows) == 8
    assert {w["workflow_type"] for w in workflows} == WORKFLOW_TYPES
    for w in workflows:
        for key in ("workflow_type", "title", "description", "note",
                    "category", "uploads", "text_inputs", "has_sample",
                    "sample_description"):
            assert key in w, f"missing {key} in {w['workflow_type']}"
        assert w["category"] in ("review", "search", "prep", "other")
        for u in w["uploads"]:
            assert set(u) == {"key", "label", "required", "file_types", "help"}
        for t in w["text_inputs"]:
            assert set(t) == {"key", "label", "required", "help", "example"}


def test_transaction_search_text_input_example(client):
    r = client.get("/api/workflows/transaction_search")
    assert r.status_code == 200
    body = r.json()
    assert body["category"] == "search"
    assert body["has_sample"] is True
    queries = [t for t in body["text_inputs"] if t["key"] == "query"]
    assert len(queries) == 1
    assert queries[0]["required"] is True
    assert "Cascade Paving" in queries[0]["example"]


def test_freeform_has_no_sample(client):
    r = client.get("/api/workflows/freeform")
    assert r.status_code == 200
    body = r.json()
    assert body["has_sample"] is False
    assert body["category"] == "other"
    keys = {t["key"] for t in body["text_inputs"]}
    assert {"task_type", "desired_output",
            "sensitivity_confirmation"} <= keys


def test_workflow_detail_404(client):
    r = client.get("/api/workflows/not_a_workflow")
    assert r.status_code == 404
    assert "detail" in r.json()
