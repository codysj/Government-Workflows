"""Standalone preflight endpoint (no run recorded, no LLM)."""
from __future__ import annotations


def test_preflight_sample_report_review_pass(client):
    r = client.post(
        "/api/workflows/report_review/preflight",
        data={"use_sample": "true"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pass"
    assert body["llm_allowed"] is True
    keys = {f["input_key"] for f in body["files"]}
    assert "report_table" in keys
    present = [f for f in body["files"] if f["input_key"] == "report_table"]
    assert present[0]["present"] is True
    assert present[0]["row_count"] > 0
    assert isinstance(body["supported_checks"], list)
    assert isinstance(body["next_steps"], list)


def test_preflight_broken_upload_fails(client):
    # A report table missing every required semantic column
    # (section / line_type / amount) must FAIL with a blocking finding.
    bad_csv = b"foo,bar\n1,2\n3,4\n"
    r = client.post(
        "/api/workflows/report_review/preflight",
        files={"report_table": ("broken.csv", bad_csv, "text/csv")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "fail"
    assert body["llm_allowed"] is False
    blocking = [f for f in body["findings"] if f["blocks_run"]]
    assert blocking, "expected at least one blocks_run finding"
    assert any(f["code"] == "missing_required_column" for f in blocking)


def test_preflight_suggested_mappings_populated(client):
    """GW-20: preflight on a sample with detectable columns returns non-empty
    suggested_mappings mirroring the core column-mapping shape."""
    r = client.post(
        "/api/workflows/report_review/preflight",
        data={"use_sample": "true"},
    )
    assert r.status_code == 200
    body = r.json()
    mappings = body["suggested_mappings"]
    assert isinstance(mappings, list) and mappings, "expected suggestions"
    m = mappings[0]
    assert {"input_key", "semantic_name", "mapped_column", "confidence",
            "source", "candidates"} == set(m)
    assert m["source"] in ("auto", "human", "unmapped")
    # At least one semantic column was confidently mapped on the sample.
    assert any(mm["source"] == "auto" and mm["mapped_column"]
               for mm in mappings)


def test_preflight_no_inputs_422(client):
    r = client.post("/api/workflows/report_review/preflight", data={})
    assert r.status_code == 422
    assert "detail" in r.json()


def test_preflight_unknown_workflow_404(client):
    r = client.post(
        "/api/workflows/nope/preflight", data={"use_sample": "true"})
    assert r.status_code == 404
