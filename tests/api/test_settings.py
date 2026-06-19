"""GET /api/settings (GW-8) - read-only settings mirror."""
from __future__ import annotations


def test_settings_returns_expected_keys(client):
    r = client.get("/api/settings")
    assert r.status_code == 200, r.text
    body = r.json()
    expected = {
        "city_name", "default_actor", "llm_provider", "mock_mode",
        "date_tolerance_days", "amount_tolerance", "variance_threshold_pct",
        "variance_dollar_threshold", "export_dir", "role",
        "default_retention_category", "editable",
    }
    assert expected == set(body)
    # Read-only this batch.
    assert body["editable"] is False
    # Tolerances are strings (frontend never does arithmetic on amounts).
    assert isinstance(body["amount_tolerance"], str)
    assert isinstance(body["variance_threshold_pct"], str)
    assert isinstance(body["variance_dollar_threshold"], str)
    assert isinstance(body["date_tolerance_days"], int)
    assert isinstance(body["mock_mode"], bool)


def test_settings_has_no_put(client):
    """The settings endpoint is read-only: no write verb exists."""
    r = client.put("/api/settings", json={"city_name": "Hacktown"})
    assert r.status_code in (404, 405)
