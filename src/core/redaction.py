"""Redaction-assist prototype (Tier 1 roadmap #1: PII detection/scrubbing).

Public-records and finance workflows routinely surface documents that contain
personally identifiable information (PII) — Social Security numbers, emails,
phone numbers, bank/account-like long numbers, credit-card numbers — which a
clerk must scrub before disclosure. This module is a DETERMINISTIC, regex-based
prototype that flags and masks those values. It does NOT use ML/NLP and makes no
network calls: every decision is a transparent pattern match a reviewer can
audit.

Design notes:

  * Detection and redaction are pure functions over text — no Streamlit, no
    provider code, no persistence — so they stay testable and reusable from the
    UI, CLI, or a workflow.
  * Patterns are ordered most-specific-first. A 16-digit card number is a
    ``credit_card``, not a generic ``long_number``; overlapping matches are
    resolved by longest match then by pattern priority, so the more specific
    type always wins.
  * This is an ASSIST prototype: it errs toward flagging and a human reviewer
    must confirm every redaction. It is intentionally conservative and will not
    catch free-text names/addresses (out of scope for a regex prototype).
"""
from __future__ import annotations

import re
from typing import Optional, Pattern

import pandas as pd
from pydantic import BaseModel, Field

# Supported pattern types, ordered MOST-SPECIFIC-FIRST. ``credit_card`` is
# listed before ``long_number`` so a card number wins over the generic
# account-like long-number rule when both could match the same span.
PATTERN_TYPES: tuple[str, ...] = (
    "ssn",
    "email",
    "phone",
    "credit_card",
    "long_number",
)

# Lower index == higher priority (more specific). Used to break ties when two
# matches have the same length / overlap.
_PRIORITY: dict[str, int] = {name: i for i, name in enumerate(PATTERN_TYPES)}

# --------------------------------------------------------------------------- #
# Default patterns
# --------------------------------------------------------------------------- #
# SSN: 3-2-4 digits with dash or space separators (e.g. 123-45-6789).
_SSN = r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b"

# Email: a deliberately simple, conservative address pattern.
_EMAIL = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"

# US phone: optional +1 / country code, optional area-code parens, and
# space/dot/dash separators (e.g. (555) 123-4567, 555-123-4567, +1 555.123.4567).
# At least one separator OR parenthesised area code is required, so a bare run of
# 10 digits is treated as a generic account-like ``long_number`` instead.
_PHONE = (
    r"(?<!\d)(?:\+?1[-.\s]?)?"
    r"(?:\(\d{3}\)[-.\s]?\d{3}[-.\s]?\d{4}"          # (555) 123-4567
    r"|\d{3}[-.\s]\d{3}[-.\s]\d{4})"                # 555-123-4567 / 555.123.4567
    r"(?!\d)"
)

# Credit card: 13-19 digits, optionally grouped by single spaces or dashes.
# Applied BEFORE long_number so the more specific card type wins.
_CREDIT_CARD = r"(?<!\d)(?:\d[ -]?){13,19}(?<=\d)"

# Long number: a run of 9+ consecutive digits (account-like). Generic fallback.
_LONG_NUMBER = r"(?<!\d)\d{9,}(?!\d)"

DEFAULT_PATTERNS: dict[str, Pattern[str]] = {
    "ssn": re.compile(_SSN),
    "email": re.compile(_EMAIL),
    "phone": re.compile(_PHONE),
    "credit_card": re.compile(_CREDIT_CARD),
    "long_number": re.compile(_LONG_NUMBER),
}


# --------------------------------------------------------------------------- #
# Result models
# --------------------------------------------------------------------------- #
class RedactionFinding(BaseModel):
    """One detected PII span in the source text.

    ``preview`` is a masked rendering that reveals only the last few characters
    (so a reviewer can recognise the value without re-exposing it).
    """

    pattern_type: str
    start: int
    end: int
    preview: str


class RedactionResult(BaseModel):
    """Outcome of redacting a string: scrubbed text + findings + tallies."""

    redacted_text: str
    findings: list[RedactionFinding] = Field(default_factory=list)
    counts_by_type: dict[str, int] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _mask_preview(value: str, *, keep: int = 4) -> str:
    """Mask all but the last ``keep`` non-separator characters of ``value``.

    Digits/letters become ``*``; separators (space/dash/dot) are preserved so
    the shape stays recognisable. Always leaves at most ``keep`` real trailing
    characters visible (fewer for short values, to avoid revealing too much).
    """
    if not value:
        return value
    # Reveal fewer characters for short values so we never expose a majority.
    visible = min(keep, max(0, len(value) - 2))
    out = []
    n = len(value)
    for i, ch in enumerate(value):
        if i >= n - visible and ch.isalnum():
            out.append(ch)
        elif ch.isalnum():
            out.append("*")
        else:
            out.append(ch)
    return "".join(out)


def _compile_extra(extra_patterns: Optional[dict]) -> dict[str, Pattern[str]]:
    """Compile caller-supplied ``{type: pattern}`` (str or compiled) entries."""
    compiled: dict[str, Pattern[str]] = {}
    for name, pat in (extra_patterns or {}).items():
        compiled[name] = pat if isinstance(pat, re.Pattern) else re.compile(pat)
    return compiled


def _ordered_patterns(
    extra: dict[str, Pattern[str]],
) -> list[tuple[str, Pattern[str]]]:
    """Return (type, pattern) pairs in most-specific-first priority order.

    Defaults keep their fixed priority; extra patterns are appended after the
    defaults (lower priority than any built-in of the same conceptual rank)
    unless they override a default type, in which case they keep that slot.
    """
    merged: dict[str, Pattern[str]] = {**DEFAULT_PATTERNS, **extra}
    extra_only = [name for name in extra if name not in _PRIORITY]

    def rank(name: str) -> int:
        if name in _PRIORITY:
            return _PRIORITY[name]
        return len(_PRIORITY) + extra_only.index(name)

    return [(name, merged[name]) for name in sorted(merged, key=rank)]


def _select_non_overlapping(
    matches: list[tuple[int, int, str]],
) -> list[tuple[int, int, str]]:
    """Resolve overlaps, keeping longest match then highest priority (lowest rank).

    ``matches`` is a list of ``(start, end, pattern_type)``. Returns the kept
    matches sorted by start position, with no two spans overlapping.
    """
    # Sort so the "best" candidates come first: longer spans win; ties broken by
    # pattern priority, then by earlier start for determinism.
    def sort_key(m: tuple[int, int, str]) -> tuple[int, int, int]:
        start, end, ptype = m
        return (-(end - start), _PRIORITY.get(ptype, len(_PRIORITY)), start)

    kept: list[tuple[int, int, str]] = []
    for start, end, ptype in sorted(matches, key=sort_key):
        if all(end <= ks or start >= ke for ks, ke, _ in kept):
            kept.append((start, end, ptype))
    kept.sort(key=lambda m: m[0])
    return kept


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def scan_text(
    text: str,
    *,
    extra_patterns: Optional[dict] = None,
) -> list[RedactionFinding]:
    """Detect PII spans in ``text`` (no scrubbing).

    Returns findings sorted by start position with overlaps resolved
    longest/most-specific-first.
    """
    if not text:
        return []
    patterns = _ordered_patterns(_compile_extra(extra_patterns))
    raw: list[tuple[int, int, str]] = []
    for ptype, pattern in patterns:
        for m in pattern.finditer(text):
            if m.end() > m.start():  # ignore zero-width matches
                raw.append((m.start(), m.end(), ptype))
    findings = []
    for start, end, ptype in _select_non_overlapping(raw):
        findings.append(RedactionFinding(
            pattern_type=ptype,
            start=start,
            end=end,
            preview=_mask_preview(text[start:end]),
        ))
    return findings


def redact_text(
    text: str,
    *,
    extra_patterns: Optional[dict] = None,
    replacement: Optional[str] = None,
) -> RedactionResult:
    """Detect and scrub PII in ``text``.

    Each match is replaced with a tag like ``[REDACTED:SSN]`` (or a fixed
    ``replacement`` string if supplied). Overlapping matches are resolved
    longest/most-specific-first, so a card number wins over a generic
    long-number. Non-matching text is left untouched.
    """
    findings = scan_text(text, extra_patterns=extra_patterns)
    if not findings:
        return RedactionResult(
            redacted_text=text or "", findings=[], counts_by_type={})

    # Rebuild the string left-to-right, swapping each span for its tag.
    pieces: list[str] = []
    cursor = 0
    counts: dict[str, int] = {}
    for f in findings:
        pieces.append(text[cursor:f.start])
        tag = (replacement if replacement is not None
               else f"[REDACTED:{f.pattern_type.upper()}]")
        pieces.append(tag)
        cursor = f.end
        counts[f.pattern_type] = counts.get(f.pattern_type, 0) + 1
    pieces.append(text[cursor:])

    return RedactionResult(
        redacted_text="".join(pieces),
        findings=findings,
        counts_by_type=counts,
    )


def redact_dataframe(
    df: pd.DataFrame,
    columns: Optional[list[str]] = None,
    *,
    extra_patterns: Optional[dict] = None,
    replacement: Optional[str] = None,
) -> tuple[pd.DataFrame, list[RedactionFinding]]:
    """Apply :func:`redact_text` cell-wise to string columns of ``df``.

    Returns ``(df_redacted, findings)`` where ``df_redacted`` is a copy with PII
    scrubbed in place and ``findings`` aggregates every span found (across all
    scanned cells). ``columns`` restricts the scan; by default every
    object/string-dtype column is scanned. Non-string cells are left untouched.
    """
    out = df.copy()
    if columns is not None:
        target = list(columns)
    else:
        # Scan object- and string-dtype columns. Newer pandas may type a text
        # column as a dedicated ``str``/``string`` dtype rather than ``object``,
        # so check both rather than comparing to ``object`` alone.
        target = [
            c for c in out.columns
            if out[c].dtype == object
            or pd.api.types.is_string_dtype(out[c].dtype)
        ]
    all_findings: list[RedactionFinding] = []
    for col in target:
        if col not in out.columns:
            continue
        new_values = []
        for val in out[col]:
            if isinstance(val, str):
                res = redact_text(
                    val, extra_patterns=extra_patterns, replacement=replacement)
                new_values.append(res.redacted_text)
                all_findings.extend(res.findings)
            else:
                new_values.append(val)
        out[col] = new_values
    return out, all_findings


__all__ = [
    "PATTERN_TYPES",
    "DEFAULT_PATTERNS",
    "RedactionFinding",
    "RedactionResult",
    "scan_text",
    "redact_text",
    "redact_dataframe",
]
