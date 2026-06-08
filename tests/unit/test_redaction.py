"""Tests for the redaction-assist prototype (src/core/redaction.py).

All sample values below are SYNTHETIC and clearly fake — no real PII.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.core import redaction
from src.core.redaction import (
    RedactionFinding,
    RedactionResult,
    redact_dataframe,
    redact_text,
    scan_text,
)


# --------------------------------------------------------------------------- #
# Per-type detection + redaction
# --------------------------------------------------------------------------- #
def test_detects_and_redacts_ssn():
    res = redact_text("My SSN is 123-45-6789 ok.")
    assert res.counts_by_type == {"ssn": 1}
    assert "[REDACTED:SSN]" in res.redacted_text
    assert "123-45-6789" not in res.redacted_text
    assert res.findings[0].pattern_type == "ssn"


def test_detects_and_redacts_email():
    res = redact_text("Contact jane.doe@example.gov for details.")
    assert res.counts_by_type == {"email": 1}
    assert "[REDACTED:EMAIL]" in res.redacted_text
    assert "jane.doe@example.gov" not in res.redacted_text


def test_detects_and_redacts_phone():
    for sample in ("(555) 123-4567", "555-123-4567", "+1 555.123.4567"):
        res = redact_text(f"Call {sample} today.")
        assert res.counts_by_type.get("phone") == 1, sample
        assert "[REDACTED:PHONE]" in res.redacted_text, sample


def test_detects_and_redacts_long_number():
    # 10 consecutive digits -> account-like long_number.
    res = redact_text("Account 1234567890 is closed.")
    assert res.counts_by_type == {"long_number": 1}
    assert "[REDACTED:LONG_NUMBER]" in res.redacted_text
    assert "1234567890" not in res.redacted_text


def test_detects_and_redacts_credit_card():
    res = redact_text("Card 4111 1111 1111 1111 charged.")
    assert res.counts_by_type == {"credit_card": 1}
    assert "[REDACTED:CREDIT_CARD]" in res.redacted_text
    assert "4111" not in res.redacted_text


# --------------------------------------------------------------------------- #
# Clean text
# --------------------------------------------------------------------------- #
def test_clean_text_has_no_findings_and_is_unchanged():
    text = "The quarterly budget report was approved by the council."
    res = redact_text(text)
    assert res.findings == []
    assert res.counts_by_type == {}
    assert res.redacted_text == text


def test_scan_clean_text_returns_empty():
    assert scan_text("No PII here, just words and the number 42.") == []


def test_empty_text():
    res = redact_text("")
    assert res.redacted_text == ""
    assert res.findings == []
    assert scan_text("") == []


# --------------------------------------------------------------------------- #
# Counts
# --------------------------------------------------------------------------- #
def test_counts_by_type_correct_for_mixed_text():
    text = (
        "SSN 123-45-6789 and 987-65-4321; "
        "email a@b.org; "
        "phones 555-123-4567 and (555) 765-4321."
    )
    res = redact_text(text)
    assert res.counts_by_type == {"ssn": 2, "email": 1, "phone": 2}
    # Total findings equals sum of counts.
    assert len(res.findings) == sum(res.counts_by_type.values())
    # Findings are ordered by position.
    starts = [f.start for f in res.findings]
    assert starts == sorted(starts)


# --------------------------------------------------------------------------- #
# Precedence: credit_card vs long_number
# --------------------------------------------------------------------------- #
def test_credit_card_wins_over_long_number():
    # 16 contiguous digits match both rules; the more specific card type wins.
    res = redact_text("Number 4111111111111111 here.")
    assert res.counts_by_type == {"credit_card": 1}
    assert "long_number" not in res.counts_by_type
    assert "[REDACTED:CREDIT_CARD]" in res.redacted_text


def test_short_long_number_is_not_a_credit_card():
    # 9-12 digits is below the 13-digit card floor -> long_number.
    res = redact_text("Ref 123456789 done.")
    assert res.counts_by_type == {"long_number": 1}


# --------------------------------------------------------------------------- #
# Masked preview
# --------------------------------------------------------------------------- #
def test_preview_is_masked_and_reveals_only_tail():
    res = redact_text("SSN 123-45-6789.")
    preview = res.findings[0].preview
    # The leading digits must be masked.
    assert preview.startswith("*")
    assert "123" not in preview
    # Separators are preserved so the shape is recognisable.
    assert "-" in preview


# --------------------------------------------------------------------------- #
# extra_patterns
# --------------------------------------------------------------------------- #
def test_extra_patterns_honored():
    extra = {"case_id": r"\bCASE-\d{4}\b"}
    res = redact_text("See CASE-2026 for context.", extra_patterns=extra)
    assert res.counts_by_type == {"case_id": 1}
    assert "[REDACTED:CASE_ID]" in res.redacted_text
    assert "CASE-2026" not in res.redacted_text


def test_extra_patterns_accepts_compiled_pattern():
    import re

    extra = {"badge": re.compile(r"\bBADGE\d+\b")}
    findings = scan_text("officer BADGE77 reported", extra_patterns=extra)
    assert [f.pattern_type for f in findings] == ["badge"]


def test_replacement_override():
    res = redact_text("Email x@y.com now.", replacement="[HIDDEN]")
    assert "[HIDDEN]" in res.redacted_text
    assert "[REDACTED" not in res.redacted_text
    assert "x@y.com" not in res.redacted_text


# --------------------------------------------------------------------------- #
# Non-matching text untouched around matches
# --------------------------------------------------------------------------- #
def test_surrounding_text_preserved():
    res = redact_text("before 123-45-6789 after")
    assert res.redacted_text == "before [REDACTED:SSN] after"


# --------------------------------------------------------------------------- #
# scan_text vs redact_text consistency
# --------------------------------------------------------------------------- #
def test_scan_text_returns_findings_without_changing_text():
    findings = scan_text("Email a@b.org and SSN 123-45-6789.")
    assert {f.pattern_type for f in findings} == {"email", "ssn"}
    assert all(isinstance(f, RedactionFinding) for f in findings)


# --------------------------------------------------------------------------- #
# DataFrame redaction
# --------------------------------------------------------------------------- #
def test_redact_dataframe_scrubs_string_columns():
    df = pd.DataFrame({
        "note": ["call 555-123-4567", "ssn 123-45-6789", "clean text"],
        "amount": [100, 200, 300],
    })
    out, findings = redact_dataframe(df)
    assert "[REDACTED:PHONE]" in out.loc[0, "note"]
    assert "[REDACTED:SSN]" in out.loc[1, "note"]
    assert out.loc[2, "note"] == "clean text"
    # Numeric column untouched.
    assert list(out["amount"]) == [100, 200, 300]
    # Aggregated findings across cells.
    assert {f.pattern_type for f in findings} == {"phone", "ssn"}
    # Original df unchanged (a copy was returned).
    assert df.loc[0, "note"] == "call 555-123-4567"


def test_redact_dataframe_columns_subset():
    df = pd.DataFrame({
        "public": ["email a@b.org"],
        "private": ["ssn 123-45-6789"],
    })
    out, findings = redact_dataframe(df, columns=["private"])
    # Only the named column was scrubbed.
    assert out.loc[0, "public"] == "email a@b.org"
    assert "[REDACTED:SSN]" in out.loc[0, "private"]
    assert [f.pattern_type for f in findings] == ["ssn"]


def test_result_is_pydantic_model():
    res = redact_text("clean")
    assert isinstance(res, RedactionResult)
    # Round-trips through pydantic serialization.
    dumped = res.model_dump()
    assert dumped["redacted_text"] == "clean"


def test_pattern_types_exposed():
    assert set(redaction.PATTERN_TYPES) == {
        "ssn", "email", "phone", "credit_card", "long_number"}
