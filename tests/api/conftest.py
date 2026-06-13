"""Shared fixtures: a TestClient against create_app with tmp dirs.

The settings/app/client are session-scoped so the run-creating tests can
share state (review actions, list, restart-simulation tests reuse the runs
created earlier in the session).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.services.settings import ApiSettings


@pytest.fixture(scope="session")
def api_settings(tmp_path_factory) -> ApiSettings:
    root = tmp_path_factory.mktemp("api_env")
    return ApiSettings(
        ledger_db_path=root / "ledger.db",
        audit_dir=root / "audit",
        export_dir=root / "exports",
        upload_dir=root / "uploads",
        frontend_dist=root / "no_frontend_dist",  # absent -> no static mount
        default_actor="api_test_actor",
        llm_mode="mock",
    )


@pytest.fixture(scope="session")
def client(api_settings) -> TestClient:
    app = create_app(api_settings)
    with TestClient(app) as c:
        yield c
