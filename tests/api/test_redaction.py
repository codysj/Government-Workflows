"""POST /api/redaction/scan (GW-11) - stateless PII scan."""
from __future__ import annotations


def test_redaction_flags_ssn_and_email(client):
    text = "Contact jane.doe@example.com or SSN 123-45-6789 today."
    r = client.post("/api/redaction/scan", json={"text": text})
    assert r.status_code == 200, r.text
    body = r.json()
    categories = {f["category"] for f in body["findings"]}
    assert "ssn" in categories
    assert "email" in categories
    assert body["finding_count"] == len(body["findings"])
    # Redacted text masks the raw values.
    assert "123-45-6789" not in body["redacted_text"]
    assert "jane.doe@example.com" not in body["redacted_text"]
    # Findings only expose masked previews, never the raw match.
    for f in body["findings"]:
        assert {"category", "masked_preview", "start", "end"} == set(f)
        assert "123-45-6789" not in f["masked_preview"]


def test_redaction_no_findings_passthrough(client):
    r = client.post("/api/redaction/scan", json={"text": "nothing to see here"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["findings"] == []
    assert body["finding_count"] == 0
    assert body["redacted_text"] == "nothing to see here"


def test_redaction_empty_text_422(client):
    assert client.post("/api/redaction/scan", json={"text": ""}).status_code == 422
    assert client.post(
        "/api/redaction/scan", json={"text": "   "}).status_code == 422


def test_redaction_stores_nothing(client):
    """The scan is stateless: no run, no audit, no artifact is created."""
    before = client.get("/api/runs", params={"limit": 500}).json()["runs"]
    client.post(
        "/api/redaction/scan",
        json={"text": "SSN 123-45-6789 and bob@example.com"},
    )
    after = client.get("/api/runs", params={"limit": 500}).json()["runs"]
    assert len(after) == len(before)
    # AI usage is likewise untouched by a redaction scan.
    usage_before = client.get("/api/ai-usage").json()["rows"]
    client.post("/api/redaction/scan", json={"text": "SSN 999-88-7777"})
    usage_after = client.get("/api/ai-usage").json()["rows"]
    assert len(usage_after) == len(usage_before)
