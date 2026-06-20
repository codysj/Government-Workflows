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


def test_get_provider_real_mode_returns_real_provider(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "real")
    assert isinstance(get_provider(), RealLLMProvider)
    # use_mock=False bypasses env entirely.
    monkeypatch.delenv("LLM_MODE", raising=False)
    assert isinstance(get_provider(use_mock=False), RealLLMProvider)


def test_real_provider_with_key_and_fake_transport_parses_dict(monkeypatch):
    """WITH a key + an injected fake transport: correctly-parsed dict, NO network."""
    monkeypatch.setenv("LLM_API_KEY", "test-key-123")

    captured: dict = {}

    def fake_transport(request: dict) -> str:
        # Prove the request was built from env key + configured model, and that
        # the prompt flowed through. No network is touched.
        captured.update(request)
        return json.dumps(
            {
                "summary": "Real-path summary.",
                "categorized_exceptions": [
                    {
                        "category": "unmatched_bank",
                        "description": "Unmatched bank item.",
                        "referenced_source_rows": ["bank:3"],
                    }
                ],
                "referenced_source_rows": ["bank:3"],
                "suggested_review_steps": ["Confirm against source rows."],
                "draft_memo": "DRAFT memo for human review.",
            }
        )

    rp = RealLLMProvider(
        "openai", "gpt-test", api_key_env="LLM_API_KEY", transport=fake_transport
    )
    prompt = _prompt_with_findings()
    out = rp.generate_structured_response(prompt)

    # Same dict shape the mock returns -> downstream validation works unchanged.
    for key in (
        "summary",
        "categorized_exceptions",
        "referenced_source_rows",
        "suggested_review_steps",
        "draft_memo",
    ):
        assert key in out
    assert out["summary"] == "Real-path summary."
    assert out["referenced_source_rows"] == ["bank:3"]

    # Request was built from env key + configured model + the prompt.
    assert captured["api_key"] == "test-key-123"
    assert captured["model"] == "gpt-test"
    assert captured["prompt"] == prompt


def test_real_provider_text_response_with_fake_transport(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    rp = RealLLMProvider(
        "openai",
        "gpt-test",
        transport=lambda req: json.dumps({"summary": "Just a summary."}),
    )
    assert rp.generate_text_response("prompt") == "Just a summary."


def test_real_provider_tolerates_code_fenced_json(monkeypatch):
    """Models often wrap JSON in prose / Markdown fences; the parser tolerates it."""
    monkeypatch.setenv("LLM_API_KEY", "k")
    fenced = (
        "Here is the analysis you requested:\n```json\n"
        + json.dumps({"summary": "Fenced.", "referenced_source_rows": ["bank:3"]})
        + "\n```\nLet me know if you need more."
    )
    rp = RealLLMProvider("openai", "gpt-test", transport=lambda req: fenced)
    out = rp.generate_structured_response("p")
    assert out["summary"] == "Fenced."
    assert out["referenced_source_rows"] == ["bank:3"]
    # Missing keys are filled with empty defaults (no fabricated data).
    assert out["categorized_exceptions"] == []
    assert out["draft_memo"] == ""


def test_real_provider_invents_nothing_on_non_json(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    rp = RealLLMProvider("openai", "gpt-test", transport=lambda req: "no json here")
    with pytest.raises(ValueError):
        rp.generate_structured_response("p")
    # text path falls back to the raw completion rather than fabricating fields.
    assert rp.generate_text_response("p") == "no json here"


def test_real_provider_does_not_call_transport_without_key(monkeypatch):
    """No key -> raises BEFORE any transport/network call."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    def boom(request: dict) -> str:  # pragma: no cover - must never run
        raise AssertionError("transport must not be called without a key")

    rp = RealLLMProvider("openai", "gpt-test", transport=boom)
    with pytest.raises(RuntimeError):
        rp.generate_structured_response("p")
