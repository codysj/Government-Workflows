"""LLM provider wrapper (Phase 3.6).

ONE provider interface for the whole project. Workflow modules must never embed
provider-specific code; they receive a provider object (or ``None``) and call
its uniform methods. Per the spec (sections 0.3, 1.2, 3.6):

  * Required methods:
        generate_structured_response(prompt, schema) -> dict
        generate_text_response(prompt) -> str
        mock_response(prompt, schema) -> dict
  * API keys come ONLY from environment variables.
  * Provider choice is config-driven (env ``LLM_PROVIDER`` / ``get_provider``).
  * Mock mode is the DEFAULT path and works with NO API key and NO internet.
  * Prompt templates are versioned (see ``src.llm.prompts``).

The model may ONLY explain/summarize/draft/classify/flag. It MUST NEVER
calculate, decide matches, or invent account numbers/funds/vendors/amounts/
dates/policy. The mock here is fully deterministic and derives its output ONLY
from the deterministic findings embedded in the prompt — it never invents data
and cites the real source-row ids, which keeps it within the validation
guardrails offline.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional, Protocol, runtime_checkable


# --------------------------------------------------------------------------- #
# Provider protocol (the single interface workflows depend on)
# --------------------------------------------------------------------------- #
@runtime_checkable
class LLMProvider(Protocol):
    """Uniform provider interface. Implementations must expose these names."""

    model_provider: str
    model_name: str

    def generate_structured_response(self, prompt: str, schema: Any = None) -> dict:
        ...

    def generate_text_response(self, prompt: str) -> str:
        ...

    def mock_response(self, prompt: str, schema: Any = None) -> dict:
        ...


# --------------------------------------------------------------------------- #
# Deterministic, offline mock provider (DEFAULT path)
# --------------------------------------------------------------------------- #
def _extract_findings_from_prompt(prompt: str) -> list[dict]:
    """Pull the JSON findings array out of a built prompt.

    Prompts built by the workflows / ``src.llm.prompts`` embed a
    ``DETERMINISTIC FINDINGS:`` block followed by a JSON array. We parse it so
    the mock can cite the REAL source-row ids (never invented). Returns [] when
    no such block is present.
    """
    marker = "DETERMINISTIC FINDINGS:"
    idx = prompt.find(marker)
    if idx == -1:
        return []
    tail = prompt[idx + len(marker):]
    start = tail.find("[")
    if start == -1:
        return []
    depth = 0
    for i, ch in enumerate(tail[start:], start=start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(tail[start : i + 1])
                except json.JSONDecodeError:
                    return []
    return []


class MockLLMProvider:
    """Deterministic mock. No API key, no internet.

    Output is derived ONLY from the deterministic findings embedded in the
    prompt and cites their real source-row ids. It never matches, calculates,
    or invents account numbers / funds / vendors / amounts / dates. Every run on
    the same prompt yields the same JSON (reproducible).
    """

    model_provider = "mock"
    model_name = "mock-llm"

    def generate_structured_response(self, prompt: str, schema: Any = None) -> dict:
        return self._build(prompt)

    def mock_response(self, prompt: str, schema: Any = None) -> dict:
        return self._build(prompt)

    def generate_text_response(self, prompt: str) -> str:
        out = self._build(prompt)
        return str(out.get("summary", ""))

    # ------------------------------------------------------------------ #
    def _build(self, prompt: str) -> dict:
        findings = _extract_findings_from_prompt(prompt)
        ref_ids: list[str] = []
        categorized: list[dict] = []
        for f in findings:
            refs = list(f.get("source_row_ids", []) or [])
            ref_ids.extend(refs)
            categorized.append(
                {
                    "category": f.get("finding_type") or f.get("rule_used") or "other",
                    "description": f.get("description", ""),
                    "referenced_source_rows": refs,
                }
            )
        return {
            "summary": (
                f"Deterministic analysis produced {len(findings)} finding(s) for "
                "human review. Each categorized item cites the source rows that "
                "triggered it. No figures were computed or invented by the "
                "assistant."
            ),
            "categorized_exceptions": categorized,
            "referenced_source_rows": sorted(set(ref_ids)),
            "suggested_review_steps": [
                "Confirm each flagged item against the underlying source rows.",
                "Have finance staff verify any item marked as requiring review.",
            ],
            "draft_memo": (
                "DRAFT — for human review only. The automated workflow flagged "
                "items requiring finance staff confirmation before finalization. "
                "All figures were computed deterministically; the assistant only "
                "drafted explanatory language."
            ),
        }


# --------------------------------------------------------------------------- #
# Real provider (interface-complete; not exercised in the offline MVP)
# --------------------------------------------------------------------------- #
class RealLLMProvider:
    """Config-driven real provider stub.

    Reads its API key ONLY from an environment variable. The MVP ships and runs
    fully on the mock path (no key, no internet); this class exists so the
    wrapper interface is complete and a real provider can be wired in later
    WITHOUT changing any workflow module. It NEVER falls back to fabricating
    data: if no key/SDK is configured it raises, so callers stay on the mock.
    """

    def __init__(
        self,
        model_provider: str,
        model_name: str,
        api_key_env: str = "LLM_API_KEY",
    ):
        self.model_provider = model_provider
        self.model_name = model_name
        self.api_key_env = api_key_env

    def _require_key(self) -> str:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(
                f"No API key in ${self.api_key_env}. The MVP runs on the mock "
                "provider by default (no key/internet required)."
            )
        return key

    def generate_structured_response(self, prompt: str, schema: Any = None) -> dict:
        self._require_key()
        raise NotImplementedError(
            "Real provider call is not implemented in the offline MVP; use the "
            "mock provider (default)."
        )

    def generate_text_response(self, prompt: str) -> str:
        self._require_key()
        raise NotImplementedError(
            "Real provider call is not implemented in the offline MVP; use the "
            "mock provider (default)."
        )

    def mock_response(self, prompt: str, schema: Any = None) -> dict:
        # Even a 'real' provider exposes the mock for offline tests.
        return MockLLMProvider().mock_response(prompt, schema)


# --------------------------------------------------------------------------- #
# Factory (config-driven; mock is the default)
# --------------------------------------------------------------------------- #
def get_provider(
    use_mock: Optional[bool] = None,
    *,
    provider_name: Optional[str] = None,
    model_name: Optional[str] = None,
) -> LLMProvider:
    """Return a provider per config. Mock is the DEFAULT.

    Resolution order:
      * ``use_mock`` explicitly True  -> MockLLMProvider.
      * ``use_mock`` explicitly False -> RealLLMProvider (needs an env key).
      * ``use_mock`` None -> read env ``LLM_MODE`` ('mock' default) and
        ``LLM_PROVIDER`` / ``LLM_MODEL``.

    The default with no environment configured is always the offline mock, so
    the system runs with no API key and no internet.
    """
    if use_mock is None:
        mode = os.environ.get("LLM_MODE", "mock").strip().lower()
        use_mock = mode != "real"

    if use_mock:
        return MockLLMProvider()

    return RealLLMProvider(
        model_provider=provider_name or os.environ.get("LLM_PROVIDER", "openai"),
        model_name=model_name or os.environ.get("LLM_MODEL", "unset"),
        api_key_env=os.environ.get("LLM_API_KEY_ENV", "LLM_API_KEY"),
    )


__all__ = [
    "LLMProvider",
    "MockLLMProvider",
    "RealLLMProvider",
    "get_provider",
]
