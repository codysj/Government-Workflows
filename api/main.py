"""FastAPI app factory for the Municipal Finance AI Workflow Tool.

Run locally:  .venv/Scripts/python.exe -m uvicorn api.main:app --port 8000

The API is a thin HTTP seam over the existing core: all workflow execution
goes through app.workflow_registry.run_workflow and all persistence goes
through the shared RunLedger / AuditLog, so run history is shared with the
Streamlit app. Local-first; no auth (per spec).
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.core.audit_log import AuditLog
from src.core.run_ledger import RunLedger
from src.core.scheduler import ScheduleStore

from api.routes import (
    ai_usage,
    health,
    redaction,
    runs,
    schedules,
    settings as settings_route,
    workflows,
)
from api.services.settings import ApiSettings

CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def create_app(settings: Optional[ApiSettings] = None) -> FastAPI:
    """Build the FastAPI app. Tests pass an ApiSettings with tmp paths."""
    settings = settings or ApiSettings.from_app_settings()
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(
        title="Municipal Finance AI API",
        version=settings.app_version,
        description=(
            "Local-first API over the deterministic workflow core. The LLM "
            "is advisory-only (mock by default); all financial logic is "
            "deterministic Python."
        ),
    )
    app.state.settings = settings
    # One shared ledger/audit pair. File-backed RunLedger opens a fresh
    # SQLite connection per operation, so it is safe across request threads.
    app.state.ledger = RunLedger(str(settings.ledger_db_path))
    app.state.audit = AuditLog(app.state.ledger, audit_dir=settings.audit_dir)
    # Read-only schedule listing reads from the same JSON store the Streamlit
    # app uses (runs/schedules.json); tests override schedules_path to a tmp.
    app.state.schedules_path = settings.schedules_path
    app.state.schedule_store = ScheduleStore(settings.schedules_path)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix="/api")
    app.include_router(workflows.router, prefix="/api")
    app.include_router(runs.router, prefix="/api")
    app.include_router(settings_route.router, prefix="/api")
    app.include_router(ai_usage.router, prefix="/api")
    app.include_router(redaction.router, prefix="/api")
    app.include_router(schedules.router, prefix="/api")

    # Serve the built frontend (if present) from the same server. Mounted
    # last so /api/* routes always win; absence is fine (dev mode uses Vite).
    if settings.frontend_dist.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=str(settings.frontend_dist), html=True),
            name="frontend",
        )

    return app


app = create_app()
