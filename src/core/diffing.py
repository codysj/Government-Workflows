"""Prompt/response diffing (Tier 1 roadmap #1).

Deterministic, stdlib-only comparison of two stored LLM interactions (or two
whole runs) so a reviewer can see exactly how a prompt template change, a model
swap, or a re-run altered the AI DRAFT output and which source rows it cited.

Everything here is pure ``difflib`` over data already persisted in the run
ledger — it never calls an LLM, never calculates findings, and never mutates
state. It only *describes* differences. No Streamlit, no provider code, so it
respects the UI/core separation and stays trivially testable.

Public API:
  - ``unified_text_diff(a, b, ...)`` -> str   (free-text / summary diffs)
  - ``diff_json(a, b)`` -> list[dict]          (structured dict/list diff)
  - ``diff_llm_responses(resp_a, resp_b)`` -> dict
  - ``diff_runs(run_a, run_b)`` -> dict        (ledger get_run() convenience)
"""
from __future__ import annotations

import difflib
from typing import Any


# --------------------------------------------------------------------------- #
# Free-text diff
# --------------------------------------------------------------------------- #
def unified_text_diff(
    a: str,
    b: str,
    *,
    label_a: str = "a",
    label_b: str = "b",
    context: int = 3,
) -> str:
    """Return a unified diff of two strings (empty string when identical).

    Compares ``a`` and ``b`` line-by-line via :func:`difflib.unified_diff` and
    joins the result back into a single string. ``None`` is treated as empty
    text so callers can diff possibly-missing fields safely.
    """
    a_text = "" if a is None else str(a)
    b_text = "" if b is None else str(b)
    if a_text == b_text:
        return ""
    diff = difflib.unified_diff(
        a_text.splitlines(),
        b_text.splitlines(),
        fromfile=label_a,
        tofile=label_b,
        lineterm="",
        n=context,
    )
    return "\n".join(diff)


# --------------------------------------------------------------------------- #
# Structured (dict/list) diff
# --------------------------------------------------------------------------- #
def _join(prefix: str, key: str) -> str:
    """Build a dotted path, omitting the leading dot at the root."""
    return f"{prefix}.{key}" if prefix else key


def _walk(a: Any, b: Any, path: str, out: list[dict]) -> None:
    """Recurse through nested dicts/lists, appending change records to ``out``.

    Dicts recurse by key; lists recurse by index up to the shared length and
    report trailing extras as added/removed. Any other (scalar) mismatch is a
    single ``changed`` record. Recursion is shallow in the sense that it only
    descends while *both* sides are the same container kind.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        for key in a:
            child = _join(path, str(key))
            if key not in b:
                out.append({"path": child, "change": "removed",
                            "old": a[key], "new": None})
            else:
                _walk(a[key], b[key], child, out)
        for key in b:
            if key not in a:
                out.append({"path": _join(path, str(key)), "change": "added",
                            "old": None, "new": b[key]})
        return

    if isinstance(a, list) and isinstance(b, list):
        shared = min(len(a), len(b))
        for i in range(shared):
            _walk(a[i], b[i], f"{path}[{i}]", out)
        for i in range(shared, len(a)):
            out.append({"path": f"{path}[{i}]", "change": "removed",
                        "old": a[i], "new": None})
        for i in range(shared, len(b)):
            out.append({"path": f"{path}[{i}]", "change": "added",
                        "old": None, "new": b[i]})
        return

    if a != b:
        out.append({"path": path, "change": "changed", "old": a, "new": b})


def diff_json(a: dict, b: dict) -> list[dict]:
    """Structured diff of two dicts -> list of change records.

    Each record is ``{"path", "change", "old", "new"}`` where ``change`` is one
    of ``'added' | 'removed' | 'changed'`` and ``path`` is a dotted/indexed
    locator such as ``'a.b[0]'``. Recurses into nested dicts and lists. Returns
    an empty list when the inputs are equal.
    """
    out: list[dict] = []
    _walk(a or {}, b or {}, "", out)
    return out


# --------------------------------------------------------------------------- #
# LLM-response diff
# --------------------------------------------------------------------------- #
def diff_llm_responses(resp_a: dict, resp_b: dict) -> dict:
    """Compare two stored ``LLMResponse`` payload dicts.

    Each payload is expected to carry ``prompt_template_version``,
    ``model_name``, ``response_json`` (with an optional ``summary``), and
    ``referenced_source_rows``. Missing keys are tolerated. Returns:

      - ``template_changed`` (bool)
      - ``model_changed`` (bool)
      - ``summary_diff`` (unified diff of ``response_json.summary``)
      - ``referenced_rows_added`` / ``referenced_rows_removed`` (lists)
      - ``json_changes`` (``diff_json`` over the two ``response_json`` dicts)
    """
    resp_a = resp_a or {}
    resp_b = resp_b or {}
    rj_a = resp_a.get("response_json") or {}
    rj_b = resp_b.get("response_json") or {}

    rows_a = list(resp_a.get("referenced_source_rows") or [])
    rows_b = list(resp_b.get("referenced_source_rows") or [])
    set_a, set_b = set(rows_a), set(rows_b)

    return {
        "template_changed": (
            resp_a.get("prompt_template_version")
            != resp_b.get("prompt_template_version")
        ),
        "model_changed": resp_a.get("model_name") != resp_b.get("model_name"),
        "summary_diff": unified_text_diff(
            rj_a.get("summary") or "",
            rj_b.get("summary") or "",
            label_a="summary_a",
            label_b="summary_b",
        ),
        "referenced_rows_added": [r for r in rows_b if r not in set_a],
        "referenced_rows_removed": [r for r in rows_a if r not in set_b],
        "json_changes": diff_json(rj_a, rj_b),
    }


# --------------------------------------------------------------------------- #
# Run-level convenience
# --------------------------------------------------------------------------- #
def _latest_llm_response(run: dict) -> dict:
    """Return the most recent ``llm_responses`` entry of a run, or ``{}``."""
    responses = (run or {}).get("llm_responses") or []
    return responses[-1] if responses else {}


def diff_runs(run_a: dict, run_b: dict) -> dict:
    """Diff two ledger ``get_run()`` dicts via their latest LLM responses.

    Convenience wrapper around :func:`diff_llm_responses`. Handles runs that
    have no recorded LLM response gracefully (empty payloads diff to "no
    change"). Also surfaces which sides actually had a response so callers can
    distinguish "identical" from "nothing to compare".
    """
    resp_a = _latest_llm_response(run_a)
    resp_b = _latest_llm_response(run_b)
    result = diff_llm_responses(resp_a, resp_b)
    result["has_response_a"] = bool(resp_a)
    result["has_response_b"] = bool(resp_b)
    return result


__all__ = [
    "unified_text_diff",
    "diff_json",
    "diff_llm_responses",
    "diff_runs",
]
