"""Context loader (Phase 0 ``src/context``; Phase 5 auto-injection).

Loads the AVAILABLE, NON-SENSITIVE reference material a workflow may cite into a
single dict of "available context references":

  * plain-text / markdown context files found under a context directory,
  * an optional chart of accounts (codes + names),
  * a city profile (city name, default actor).

This is what Phase 5 "Guided Freeform Mode" auto-injects (step 4: "Auto-inject
available city/context references"). ``src.workflows.freeform`` duck-types this
module and tries, in order, ``load_context`` / ``load_available_context`` /
``load`` / ``get_context``; all four are provided here so auto-injection always
resolves to a real function.

Only NON-SENSITIVE reference data should ever flow through here (city profile,
chart of accounts, public context notes) — never task payloads or real city
financial data (spec sections 0.3 / 1.1 / Phase 5 prohibited behavior).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from src.context.chart_of_accounts import ChartOfAccounts, load_chart_of_accounts
from src.context.city_profile import CityProfile, load_city_profile

# Text/markdown context file suffixes we surface as references.
_TEXT_SUFFIXES = (".txt", ".md", ".markdown", ".rst")
# Cap how much of each context file we inject (keep prompts bounded).
_MAX_TEXT_CHARS = 20_000


def _load_text_files(context_dir: str | Path | None) -> dict[str, str]:
    """Return ``{relative_name: text}`` for context files under ``context_dir``."""
    if context_dir is None:
        return {}
    root = Path(context_dir)
    if not root.is_dir():
        return {}
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        out[p.relative_to(root).as_posix()] = text[:_MAX_TEXT_CHARS]
    return out


def load_context(
    context_dir: str | Path | None = None,
    coa_path: str | Path | None = None,
    *,
    city_profile: Any = None,
) -> dict[str, Any]:
    """Load available context references into a single dict.

    Parameters
    ----------
    context_dir:
        Optional directory of plain-text / markdown context files.
    coa_path:
        Optional path to a chart-of-accounts CSV.
    city_profile:
        Optional CityProfile, dict, JSON path, or None (env/defaults used).

    Returns
    -------
    dict
        Keys (always present):
          * ``city_profile`` — the city profile as a dict.
          * ``context_files`` — ``{name: text}`` (possibly empty).
          * ``chart_of_accounts`` — ``{codes: [...], names: {...}}`` (empty when
            no chart of accounts was supplied).
          * ``available`` — sorted list of the reference categories that carry
            real data, for quick "what's available" display / auditing.
    """
    profile: CityProfile = load_city_profile(city_profile)
    coa: ChartOfAccounts = load_chart_of_accounts(coa_path) if coa_path else ChartOfAccounts()
    text_files = _load_text_files(context_dir)

    refs: dict[str, Any] = {
        "city_profile": profile.to_dict(),
        "context_files": text_files,
        "chart_of_accounts": {
            "codes": sorted(coa.codes),
            "names": coa.names,
            "count": len(coa),
        },
    }
    available = ["city_profile"]
    if text_files:
        available.append("context_files")
    if len(coa):
        available.append("chart_of_accounts")
    refs["available"] = sorted(available)
    return refs


# --------------------------------------------------------------------------- #
# Duck-typed aliases (freeform tries these names in order)
# --------------------------------------------------------------------------- #
def get_context(
    context_dir: str | Path | None = None,
    coa_path: str | Path | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Thin alias for :func:`load_context` (freeform duck-typed lookup)."""
    return load_context(context_dir, coa_path, **kwargs)


def load_available_context(
    context_dir: str | Path | None = None,
    coa_path: str | Path | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Alias for :func:`load_context`."""
    return load_context(context_dir, coa_path, **kwargs)


def load(
    context_dir: str | Path | None = None,
    coa_path: str | Path | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Alias for :func:`load_context`."""
    return load_context(context_dir, coa_path, **kwargs)


__all__ = [
    "load_context",
    "get_context",
    "load_available_context",
    "load",
]
