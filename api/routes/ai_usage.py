"""GET /api/ai-usage (GW-9) - cross-run AI usage log."""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.schemas.models import AiUsageList
from api.services.ai_usage import build_ai_usage_rows

router = APIRouter(tags=["ai-usage"])


@router.get("/ai-usage", response_model=AiUsageList)
def get_ai_usage(request: Request) -> AiUsageList:
    """List every LLM interaction across all runs, newest first."""
    return AiUsageList(rows=build_ai_usage_rows(request.app.state.ledger))
