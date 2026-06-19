"""POST /api/redaction/scan (GW-11) - stateless PII scan."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.schemas.models import RedactionScanRequest, RedactionScanResult
from api.services.redaction import build_redaction_scan

router = APIRouter(tags=["redaction"])


@router.post("/redaction/scan", response_model=RedactionScanResult)
def scan_redaction(body: RedactionScanRequest) -> RedactionScanResult:
    """Scan text for PII and return masked findings + redacted text.

    Stateless: nothing is stored. Returns 422 when ``text`` is blank.
    """
    if not body.text or not body.text.strip():
        raise HTTPException(
            status_code=422,
            detail="Provide non-empty text to scan for redaction.",
        )
    return build_redaction_scan(body.text, body.extra_patterns)
