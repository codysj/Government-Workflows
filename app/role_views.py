"""Role-specific view configuration for the Streamlit MVP (Tier 1 #4).

This is a DISPLAY-EMPHASIS layer only — there is NO authentication and NO
access control. Picking a role never hides data destructively: it only reorders
/ optionally filters the findings table and shows a short "focus" caption. The
Review Run page always offers a "show all" fallback so every finding stays
reachable regardless of role.

Each role config describes:
  * ``caption`` — a one-line "Focus for <role>" hint shown above the findings.
  * ``emphasis_types`` — finding ``finding_type`` substrings to surface first.
  * ``hide_low_severity`` — whether the default (non-"show all") view collapses
    low-severity rows for that role (still recoverable via "show all").

The module has NO Streamlit / provider dependency so it stays trivially
importable and testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RoleView:
    """Per-role display emphasis (non-destructive)."""

    name: str
    caption: str
    emphasis_types: tuple[str, ...] = ()
    hide_low_severity: bool = False


# Order is the dropdown order. Accountant is the default (sees everything).
ROLE_VIEWS: dict[str, RoleView] = {
    "AP clerk": RoleView(
        name="AP clerk",
        caption=(
            "Focus for AP clerk: unmatched and duplicate items that block "
            "payment processing. Variance commentary is de-emphasized."
        ),
        emphasis_types=("unmatched", "duplicate", "missing"),
    ),
    "Accountant": RoleView(
        name="Accountant",
        caption=(
            "Focus for Accountant: the full set of findings, unfiltered. You "
            "see every deterministic result and all AI draft sections."
        ),
    ),
    "Finance analyst": RoleView(
        name="Finance analyst",
        caption=(
            "Focus for Finance analyst: budget-to-actual variances and large "
            "changes. Routine matched items are de-emphasized."
        ),
        emphasis_types=("variance", "change", "threshold"),
    ),
    "Finance director": RoleView(
        name="Finance director",
        caption=(
            "Focus for Finance director: approval status and the high-level "
            "summary. Low-severity, line-level rows are collapsed by default "
            "(use 'Show all findings' to expand)."
        ),
        emphasis_types=("summary", "approval"),
        hide_low_severity=True,
    ),
}

# Canonical dropdown order + default.
ROLE_ORDER: tuple[str, ...] = (
    "AP clerk",
    "Accountant",
    "Finance analyst",
    "Finance director",
)
DEFAULT_ROLE = "Accountant"


def get_role_view(role: str) -> RoleView:
    """Return the :class:`RoleView` for ``role`` (falls back to the default)."""
    return ROLE_VIEWS.get(role, ROLE_VIEWS[DEFAULT_ROLE])


def _emphasis_score(finding_type: str, emphasis_types: tuple[str, ...]) -> int:
    """0 if the finding type matches an emphasis substring, else 1 (sorts first)."""
    ft = (finding_type or "").lower()
    return 0 if any(e in ft for e in emphasis_types) else 1


def order_findings_for_role(
    findings: list[dict], role: str, *, show_all: bool = False
) -> list[dict]:
    """Return findings reordered (and optionally filtered) for ``role``.

    Non-destructive: emphasized finding types sort to the top, the rest keep
    their original relative order. When ``show_all`` is False and the role
    collapses low-severity rows, low-severity findings are dropped from the
    returned list (the caller still has the full list and a 'show all' toggle).
    """
    view = get_role_view(role)
    items = list(findings)
    if not show_all and view.hide_low_severity:
        items = [
            f for f in items
            if str(f.get("severity", "")).lower() not in ("low", "info", "")
        ]
    # Stable sort: emphasized types first, original order preserved within groups.
    if view.emphasis_types:
        items = sorted(
            items,
            key=lambda f: _emphasis_score(
                f.get("finding_type", ""), view.emphasis_types),
        )
    return items


__all__ = [
    "RoleView",
    "ROLE_VIEWS",
    "ROLE_ORDER",
    "DEFAULT_ROLE",
    "get_role_view",
    "order_findings_for_role",
]
