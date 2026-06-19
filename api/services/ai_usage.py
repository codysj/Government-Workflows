"""Builder for GET /api/ai-usage (GW-9).

Thin pass-through over src.core.ai_usage_log.ai_usage_log_rows, which returns
one flat dict per LLM interaction joined with run metadata. Rows are returned
newest-first (the ledger lists oldest-first) so the UI can render a table
without sorting. Pure read; nothing is written.
"""
from __future__ import annotations

from typing import Any

from src.core.ai_usage_log import ai_usage_log_rows

from api.schemas.models import AiUsageRow


def build_ai_usage_rows(ledger: Any) -> list[AiUsageRow]:
    """Return AiUsageRow models for every LLM interaction, newest first."""
    rows = ai_usage_log_rows(ledger)
    # ai_usage_log_rows joins llm_responses to runs without an explicit ORDER
    # BY, so the underlying order is not guaranteed chronological. Sort by the
    # ISO-8601 created_at string (lexical == chronological for UTC ISO stamps)
    # descending so the most recent interaction always leads. Rows missing a
    # created_at sort to the end.
    ordered = sorted(
        rows,
        key=lambda row: row.get("created_at") or "",
        reverse=True,
    )
    return [AiUsageRow(**row) for row in ordered]
