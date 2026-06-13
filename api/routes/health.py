"""GET /api/health"""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.schemas.models import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    return HealthResponse(
        status="ok",
        app="municipal-finance-ai",
        version=settings.app_version,
        llm_mode=settings.llm_mode,
    )
