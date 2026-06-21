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
from typing import Any, Callable, Optional, Protocol, runtime_checkable


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


def _extract_json_object(text: str) -> Optional[dict]:
    """Extract the first balanced ``{...}`` JSON object from a string.

    Tolerates Markdown code fences and surrounding prose that real models often
    add. Returns the parsed dict, or ``None`` if no valid JSON object is found.
    """
    if not isinstance(text, str):
        return None
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return obj if isinstance(obj, dict) else None
    return None


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
# Real provider (opt-in; offline-testable via an injectable transport)
# --------------------------------------------------------------------------- #
# A transport is any callable that takes the request dict the provider built
# and returns the model's raw textual completion (the JSON the model emitted,
# as a plain ``str``). This is the seam that makes the provider fully unit-
# testable with NO network: tests inject a fake transport that returns a canned
# string; production uses the default httpx transport that POSTs to a vendor's
# chat/messages endpoint.
#
# Request dict shape (what a transport receives):
#   {
#     "base_url": str,          # e.g. "https://api.anthropic.com/v1/messages"
#     "model": str,             # e.g. "claude-3-5-sonnet-latest"
#     "api_key": str,           # read from env ONLY, never logged
#     "prompt": str,            # the fully-built deterministic-findings prompt
#     "timeout": float,
#   }
# Return: the model's completion text (str). The provider then parses it.
Transport = Callable[[dict], str]


def anthropic_messages_transport(request: dict) -> str:
    """Anthropic Messages API transport preset.

    Drop-in replacement for _default_httpx_transport when the target is the
    Anthropic Messages API (https://api.anthropic.com/v1/messages). Wire format
    differs from OpenAI-compatible: uses ``x-api-key`` / ``anthropic-version``
    headers and returns ``content[0].text`` instead of
    ``choices[0].message.content``.

    Opt-in via::

        from src.llm.provider import anthropic_messages_transport, RealLLMProvider
        p = RealLLMProvider(
            model_provider="anthropic",
            model_name="claude-opus-4-8",  # or override via LLM_MODEL env
            api_key_env="ANTHROPIC_API_KEY",
            base_url="https://api.anthropic.com/v1/messages",
            transport=anthropic_messages_transport,
        )

    This function performs the only network I/O for the Anthropic path and is
    NEVER exercised by the default (offline, mock) test suite.
    """
    import httpx  # local import: only touched on the opt-in real path

    base_url = request.get("base_url", "https://api.anthropic.com/v1/messages")
    headers = {
        "x-api-key": request["api_key"],
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": request["model"],
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": request["prompt"]}],
    }
    resp = httpx.post(
        base_url,
        headers=headers,
        json=body,
        timeout=request.get("timeout", 60.0),
    )
    resp.raise_for_status()
    data = resp.json()
    # Anthropic response: content[0].text
    return data["content"][0]["text"]


def _default_httpx_transport(request: dict) -> str:
    """Default transport: POST the prompt to a chat-completions-style endpoint.

    Uses ``httpx`` (already a dependency; no vendor SDK). The request body is
    shaped for an OpenAI-compatible ``/chat/completions`` endpoint, which many
    providers (OpenAI, Azure, OpenRouter, local servers) accept. To wire a
    different vendor (e.g. the Anthropic Messages API) supply that vendor's
    base URL + model + key and, if its wire format differs, inject a custom
    transport that returns the completion text.

    This function performs the only network I/O in the module and is therefore
    NEVER exercised by the default (offline, mock) test suite.
    """
    import httpx  # local import: only touched on the opt-in real path

    base_url = request["base_url"]
    headers = {
        "Authorization": f"Bearer {request['api_key']}",
        "Content-Type": "application/json",
    }
    body = {
        "model": request["model"],
        "messages": [{"role": "user", "content": request["prompt"]}],
    }
    resp = httpx.post(
        base_url,
        headers=headers,
        json=body,
        timeout=request.get("timeout", 60.0),
    )
    resp.raise_for_status()
    data = resp.json()
    # OpenAI-compatible response: choices[0].message.content
    return data["choices"][0]["message"]["content"]


# Keys the validation guardrails / workflows expect from a structured response.
_STRUCTURED_KEYS = (
    "summary",
    "categorized_exceptions",
    "referenced_source_rows",
    "suggested_review_steps",
    "draft_memo",
)


class RealLLMProvider:
    """Opt-in real provider behind the shared :class:`LLMProvider` interface.

    The API key is read ONLY from an environment variable (``api_key_env``,
    default ``LLM_API_KEY``). The request is built from the prompt and sent via
    an INJECTABLE ``transport`` callable to a configurable ``base_url`` +
    ``model``; the model's completion is parsed into the SAME dict shape the
    mock returns, so every downstream validation / source-citation guardrail
    keeps working unchanged.

    Transport seam (unit-testable with NO network):
        ``RealLLMProvider(..., transport=fake)`` where ``fake(request: dict) ->
        str`` returns the model's completion text. The default transport uses
        ``httpx`` to POST to an OpenAI-compatible chat-completions endpoint;
        wiring a specific vendor (e.g. Anthropic Messages API) is done by
        supplying that vendor's base URL / model / key and, if needed, a custom
        transport. See ``Transport`` above for the request dict shape.

    Guardrail posture: this provider only returns language output. It never
    computes or invents finance values; the workflows' deterministic validation
    still vets every response. If no key is set it raises a clear, actionable
    error and NEVER fabricates data.
    """

    def __init__(
        self,
        model_provider: str,
        model_name: str,
        api_key_env: str = "LLM_API_KEY",
        *,
        base_url: Optional[str] = None,
        transport: Optional[Transport] = None,
        timeout: float = 60.0,
    ):
        self.model_provider = model_provider
        self.model_name = model_name
        self.api_key_env = api_key_env
        # base_url is configurable via env so no vendor is hard-coded.
        self.base_url = base_url or os.environ.get(
            "LLM_BASE_URL", "https://api.openai.com/v1/chat/completions"
        )
        self._transport: Transport = transport or _default_httpx_transport
        self.timeout = timeout

    def _require_key(self) -> str:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(
                f"No API key found in environment variable ${self.api_key_env}. "
                f"The real LLM provider is opt-in: set ${self.api_key_env} (and "
                "optionally LLM_BASE_URL / LLM_MODEL) to enable it, or leave "
                "LLM_MODE unset to use the offline mock provider (the default, "
                "which needs no key and no internet)."
            )
        return key

    def _build_request(self, prompt: str) -> dict:
        return {
            "base_url": self.base_url,
            "model": self.model_name,
            "api_key": self._require_key(),
            "prompt": prompt,
            "timeout": self.timeout,
        }

    @staticmethod
    def _parse_completion(text: str) -> dict:
        """Parse the model's completion text into the shared dict shape.

        The model is instructed (by the prompt's output contract) to return
        JSON. We tolerate surrounding prose / Markdown code fences by extracting
        the first balanced ``{...}`` object. We NEVER fabricate finance values:
        missing keys are filled with empty defaults so downstream validation can
        flag them, rather than inventing data.
        """
        obj = _extract_json_object(text)
        if obj is None:
            raise ValueError(
                "Real provider returned a response that did not contain a JSON "
                "object matching the expected output contract."
            )
        # Normalize to the canonical key set without inventing content.
        out: dict[str, Any] = {
            "summary": obj.get("summary", ""),
            "categorized_exceptions": obj.get("categorized_exceptions", []) or [],
            "referenced_source_rows": obj.get("referenced_source_rows", []) or [],
            "suggested_review_steps": obj.get("suggested_review_steps", []) or [],
            "draft_memo": obj.get("draft_memo", ""),
        }
        # Preserve any extra keys the model emitted (validation may use them).
        for k, v in obj.items():
            if k not in out:
                out[k] = v
        return out

    def generate_structured_response(self, prompt: str, schema: Any = None) -> dict:
        request = self._build_request(prompt)
        completion = self._transport(request)
        return self._parse_completion(completion)

    def generate_text_response(self, prompt: str) -> str:
        request = self._build_request(prompt)
        completion = self._transport(request)
        # Prefer the parsed summary; fall back to the raw completion text.
        try:
            parsed = self._parse_completion(completion)
            summary = parsed.get("summary")
            if summary:
                return str(summary)
        except ValueError:
            pass
        return str(completion)

    def mock_response(self, prompt: str, schema: Any = None) -> dict:
        # Even a 'real' provider exposes the offline mock for offline tests.
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
    "Transport",
    "anthropic_messages_transport",
    "get_provider",
]
