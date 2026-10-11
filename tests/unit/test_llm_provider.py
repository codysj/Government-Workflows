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
    anthropic_messages_transport,
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


# ------------------------------------------------------------------ #
# GW-27: Anthropic Messages API transport preset tests
# ------------------------------------------------------------------ #

def test_anthropic_transport_builds_correct_request_shape(monkeypatch):
    """Preset builds x-api-key header and content[].text request; no network."""
    import json as _json

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test-key")
    captured: dict = {}

    # Simulate httpx.post returning an Anthropic-shaped response (no real network)
    import unittest.mock as _mock
    mock_resp = _mock.MagicMock()
    mock_resp.json.return_value = {
        "content": [{"type": "text", "text": _json.dumps({"summary": "Anthropic reply."})}]
    }
    mock_resp.raise_for_status = lambda: None

    with _mock.patch("httpx.post", return_value=mock_resp) as mock_post:
        rp = RealLLMProvider(
            model_provider="anthropic",
            model_name="claude-opus-4-8",
            api_key_env="ANTHROPIC_API_KEY",
            base_url="https://api.anthropic.com/v1/messages",
            transport=anthropic_messages_transport,
        )
        out = rp.generate_structured_response("test prompt")

    # Correct HTTP call shape
    _, kwargs = mock_post.call_args
    headers = kwargs["headers"]
    assert headers["x-api-key"] == "ant-test-key"
    assert headers["anthropic-version"] == "2023-06-01"
    body = kwargs["json"]
    assert body["model"] == "claude-opus-4-8"
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["content"] == "test prompt"
    # content[0].text was parsed into the standard dict shape
    assert out["summary"] == "Anthropic reply."


def test_anthropic_transport_parses_content_text():
    """content[].text extraction works with a fake httpx response; no network."""
    import json as _json
    import unittest.mock as _mock

    mock_resp = _mock.MagicMock()
    mock_resp.json.return_value = {
        "content": [
            {"type": "text", "text": _json.dumps({"summary": "Parsed ok.", "referenced_source_rows": ["bank:1"]})}
        ]
    }
    mock_resp.raise_for_status = lambda: None

    with _mock.patch("httpx.post", return_value=mock_resp):
        result = anthropic_messages_transport({
            "base_url": "https://api.anthropic.com/v1/messages",
            "model": "claude-opus-4-8",
            "api_key": "fake-key",
            "prompt": "hello",
            "timeout": 30.0,
        })

    data = _json.loads(result)
    assert data["summary"] == "Parsed ok."
    assert data["referenced_source_rows"] == ["bank:1"]


def test_real_provider_does_not_call_transport_without_key(monkeypatch):
    """No key -> raises BEFORE any transport/network call."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    def boom(request: dict) -> str:  # pragma: no cover - must never run
        raise AssertionError("transport must not be called without a key")

    rp = RealLLMProvider("openai", "gpt-test", transport=boom)
    with pytest.raises(RuntimeError):
        rp.generate_structured_response("p")


def test_env_anthropic_provider_uses_messages_transport(monkeypatch):
    """GW-38: LLM_PROVIDER=anthropic must not send OpenAI-format requests."""
    from src.llm import provider as prov

    monkeypatch.setenv("LLM_MODE", "real")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    p = prov.get_provider()
    assert p._transport is prov.anthropic_messages_transport
    assert p.base_url == "https://api.anthropic.com/v1/messages"


def test_env_openai_compatible_provider_keeps_default_transport(monkeypatch):
    from src.llm import provider as prov

    monkeypatch.setenv("LLM_MODE", "real")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1/chat/completions")
    p = prov.get_provider()
    assert p._transport is prov._default_httpx_transport
    assert p.base_url.startswith("https://openrouter.ai")


def test_extra_body_and_max_tokens_reach_the_transport(monkeypatch):
    from src.llm import provider as prov

    seen = {}

    def fake(request):
        seen.update(request)
        return '{"summary": "ok"}'

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_EXTRA_BODY", '{"reasoning": {"enabled": false}}')
    p = prov.RealLLMProvider("openai", "m", transport=fake, max_tokens=123)
    assert p.generate_structured_response("prompt")["summary"] == "ok"
    assert seen["max_tokens"] == 123
    assert seen["extra_body"] == {"reasoning": {"enabled": False}}


def test_pipeline_uses_real_provider_when_llm_mode_real(monkeypatch):
    from src.core import pipeline
    from src.core.run_ledger import RunLedger
    from src.workflows.registry import get_descriptor

    class Canned:
        model_provider = "canned"
        model_name = "canned-1"

        def generate_structured_response(self, prompt, schema=None):
            return {"summary": "Draft only; see ledger:0.", "referenced_source_rows": ["ledger:0"]}

    monkeypatch.setenv("LLM_MODE", "real")
    monkeypatch.setattr(pipeline, "get_provider", lambda: Canned())
    d = get_descriptor("bank_reconciliation")
    result = pipeline.run_workflow(d.workflow_type, d.sample_inputs(), ledger=RunLedger(":memory:"))
    assert result.llm_response.model_provider == "canned"


def test_env_file_loader_strips_inline_comments(tmp_path, monkeypatch):
    from src.core.env import load_env_file

    env = tmp_path / ".env"
    env.write_text('A_KEY=abc   # comment\nB_JSON={"x":1}\n# whole line\nC_Q="q v"\n',
                   encoding="utf-8")
    for k in ("A_KEY", "B_JSON", "C_Q"):
        monkeypatch.delenv(k, raising=False)
    load_env_file(env)
    import os
    assert os.environ["A_KEY"] == "abc"
    assert os.environ["B_JSON"] == '{"x":1}'
    assert os.environ["C_Q"] == "q v"


def test_provider_failure_keeps_deterministic_results():
    """A failing provider must not fail the run or drop the findings."""
    from src.core import pipeline
    from src.core.run_ledger import RunLedger
    from src.workflows.registry import list_descriptors

    class Broken:
        model_provider = "broken"
        model_name = "broken-1"

        def generate_structured_response(self, prompt, schema=None):
            raise TimeoutError("https://example.invalid/?key=secret timed out")

        def generate_text_response(self, prompt):
            raise TimeoutError("boom")

    for d in list_descriptors():
        if not d.sample_inputs():
            continue
        r = pipeline.run_workflow(d.workflow_type, d.sample_inputs(),
                                  ledger=RunLedger(":memory:"), provider=Broken())
        assert not r.blocked, d.workflow_type
        assert r.findings or d.workflow_type == "freeform", d.workflow_type
        if r.llm_response is not None:
            text = str(r.llm_response.response_json)
            assert "secret" not in text and "example.invalid" not in text


def test_extra_body_must_be_a_json_object(monkeypatch):
    import pytest
    from src.llm import provider as prov

    monkeypatch.setenv("LLM_API_KEY", "k")
    p = prov.RealLLMProvider("openai", "m", transport=lambda r: '{"summary": "x"}')
    monkeypatch.setenv("LLM_EXTRA_BODY", "[1, 2]")
    with pytest.raises(ValueError):
        p.generate_structured_response("prompt")
    monkeypatch.setenv("LLM_EXTRA_BODY", "{not json")
    with pytest.raises(ValueError):
        p.generate_structured_response("prompt")


def test_forced_mock_ignores_llm_mode_real(monkeypatch):
    """CLI --mock (default), the eval harness, and the scale benchmark pass
    provider=MOCK and must stay offline even when LLM_MODE=real."""
    from src.core import pipeline
    from src.core.run_ledger import RunLedger
    from src.workflows.registry import get_descriptor

    monkeypatch.setenv("LLM_MODE", "real")
    monkeypatch.setattr(pipeline, "get_provider",
                        lambda: (_ for _ in ()).throw(AssertionError("real provider used")))
    d = get_descriptor("bank_reconciliation")
    r = pipeline.run_workflow(d.workflow_type, d.sample_inputs(),
                              ledger=RunLedger(":memory:"), provider=pipeline.MOCK)
    assert r.validation.passed


def test_run_qa_provider_failure_returns_unavailable_answer(tmp_path):
    from src.core import pipeline, run_qa
    from src.core.audit_log import AuditLog
    from src.core.run_ledger import RunLedger
    from src.workflows.registry import get_descriptor

    class Broken:
        model_provider = model_name = "broken"

        def generate_structured_response(self, prompt, schema=None):
            raise TimeoutError("timed out")

    ledger = RunLedger(str(tmp_path / "l.db"))
    audit = AuditLog(ledger, audit_dir=str(tmp_path / "a"))
    d = get_descriptor("bank_reconciliation")
    run = pipeline.run_workflow(d.workflow_type, d.sample_inputs(), ledger=ledger, audit=audit)
    turn = run_qa.answer_question(ledger, audit, run.run_id, "Why is bank:4 unmatched?",
                                  provider=Broken(), export_dir=tmp_path / "e")
    assert turn["answerable"] is False
    assert "unavailable" in turn["answer"]
