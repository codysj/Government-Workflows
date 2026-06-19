"""Builder for POST /api/redaction/scan (GW-11 redaction assist).

Stateless wrapper over src.core.redaction.redact_text: it scans the supplied
text for PII, returns masked-preview findings plus the scrubbed text, and
STORES NOTHING. Only the core's masked previews are surfaced - raw sensitive
matches are never echoed back.
"""
from __future__ import annotations

from typing import Optional

from src.core.redaction import redact_text

from api.schemas.models import (
    RedactionFindingInfo,
    RedactionScanResult,
)


def build_redaction_scan(
    text: str,
    extra_patterns: Optional[dict[str, str]] = None,
) -> RedactionScanResult:
    """Scan ``text`` and return masked findings + redacted text.

    Uses redact_text so the scrubbed text and findings come from a single core
    call. The finding ``pattern_type`` is surfaced as ``category`` and only the
    masked ``preview`` is returned.
    """
    result = redact_text(text, extra_patterns=extra_patterns or None)
    findings = [
        RedactionFindingInfo(
            category=f.pattern_type,
            masked_preview=f.preview,
            start=f.start,
            end=f.end,
        )
        for f in result.findings
    ]
    return RedactionScanResult(
        findings=findings,
        redacted_text=result.redacted_text,
        finding_count=len(findings),
    )
