"""Tests for the shared LLM provider wrapper (src/llm/provider.py, Phase 3.6).

Covers the spec requirements: the three required methods, mock-as-default,
config-driven selection, env-var-only API keys, offline determinism, and the
guardrail that the mock cites only real source-row ids (never invents).
"""
from __future__ import annotations

import json

import pytest

from src.llm.provider import (
    MockLLMProvider,
    RealLLMProvider,
    get_provider,
)


def _prompt_with_findings() -> str:
    findings = [
        {
            "finding_id": "f1",
            "finding_type": "unmatched_bank",
            "rule_used": "bank_item_without_ledger_match",
            "description": "Unmatched bank item.",
            "computed_values": {"amount": "100.00"},
            "source_row_ids": ["bank:3"],
        },
        {
            "finding_id": "f2",
            "finding_type": "timing_difference",
            "rule_used": "amount_match_within_date_tolerance",
            "description": "Timing difference.",
            "computed_values": {"amount": "200.00"},
            "source_row_ids": ["bank:5", "ledger:2"],
        },
    ]
    return (
        "GUARDRAILS...\n\nDETERMINISTIC FINDINGS:\n"
        + json.dumps(findings, indent=2)
        + "\n\nReturn JSON...\n"
    )


def test_required_methods_present():
    p = MockLLMProvider()
    assert hasattr(p, "generate_structured_response")
    assert hasattr(p, "generate_text_response")
    assert hasattr(p, "mock_response")


def test_default_factory_is_mock_and_offline(monkeypatch):
    # No env configured -> mock, no key needed.
    monkeypatch.delenv("LLM_MODE", raising=False)
    p = get_provider()
    assert isinstance(p, MockLLMProvider)
    assert p.model_provider == "mock"


def test_mock_is_deterministic():
    prompt = _prompt_with_findings()
    a = MockLLMProvider().generate_structured_response(prompt)
    b = MockLLMProvider().generate_structured_response(prompt)
    assert a == b


def test_mock_cites_only_real_source_rows():
    prompt = _prompt_with_findings()
    out = MockLLMProvider().generate_structured_response(prompt)
    # Every cited ref must be one that appeared in the prompt findings.
    allowed = {"bank:3", "bank:5", "ledger:2"}
    assert set(out["referenced_source_rows"]).issubset(allowed)
    # And it should have cited the exceptions present.
    assert "bank:3" in out["referenced_source_rows"]
    assert "summary" in out and out["summary"]


def test_mock_with_no_findings_invents_nothing():
    out = MockLLMProvider().generate_structured_response("no findings here")
    assert out["referenced_source_rows"] == []
    assert out["categorized_exceptions"] == []


def test_text_response_returns_summary_string():
    prompt = _prompt_with_findings()
    txt = MockLLMProvider().generate_text_response(prompt)
    assert isinstance(txt, str) and txt


def test_real_provider_requires_env_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    rp = RealLLMProvider("openai", "gpt-x", api_key_env="LLM_API_KEY")
    with pytest.raises(RuntimeError):
        rp.generate_structured_response("x")
    with pytest.raises(RuntimeError):
        rp.generate_text_response("x")


def test_real_provider_still_exposes_offline_mock():
    rp = RealLLMProvider("openai", "gpt-x")
    out = rp.mock_response(_prompt_with_findings())
    assert "summary" in out


def test_get_provider_real_mode_needs_key(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "real")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    p = get_provider()
    assert isinstance(p, RealLLMProvider)
    with pytest.raises(RuntimeError):
        p.generate_text_response("x")
