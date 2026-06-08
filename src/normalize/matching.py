"""Generic deterministic amount+date matching helpers (used by bank rec).

The LLM never performs matching. This module is the authoritative matcher.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from src.normalize.finance_rules import dates_within_tolerance, signed_equal


@dataclass
class MatchCandidate:
    """One record participating in matching, keyed by amount + date."""

    index: int                # original row_index in its source table
    amount: Optional[Decimal]
    txn_date: Optional[date]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class MatchResult:
    matched_pairs: list[tuple[MatchCandidate, MatchCandidate]] = field(default_factory=list)
    unmatched_left: list[MatchCandidate] = field(default_factory=list)
    unmatched_right: list[MatchCandidate] = field(default_factory=list)
    timing_pairs: list[tuple[MatchCandidate, MatchCandidate]] = field(default_factory=list)


def _pair_matches(
    a: MatchCandidate,
    b: MatchCandidate,
    amount_tolerance: Decimal,
    date_tolerance_days: int,
) -> tuple[bool, bool]:
    """Return (amount_ok, exact_date) for two candidates."""
    amount_ok = signed_equal(a.amount, b.amount, amount_tolerance)
    if not amount_ok:
        return (False, False)
    exact = dates_within_tolerance(a.txn_date, b.txn_date, 0)
    within = dates_within_tolerance(a.txn_date, b.txn_date, date_tolerance_days)
    return (within, exact)


def match_records(
    left: list[MatchCandidate],
    right: list[MatchCandidate],
    *,
    amount_tolerance: Decimal = Decimal("0"),
    date_tolerance_days: int = 0,
) -> MatchResult:
    """Greedy one-to-one match on amount (tolerance) and date (tolerance).

    Exact-date matches are preferred over timing (within-tolerance) matches.
    A timing pair is a match whose dates differ but fall within tolerance.
    Each right record is consumed at most once.
    """
    result = MatchResult()
    used_right: set[int] = set()

    # Pass 1: prefer exact-date matches.
    # Pass 2: allow within-tolerance (timing) matches.
    for prefer_exact in (True, False):
        for a in left:
            if any(a is m[0] for m in result.matched_pairs) or any(
                a is t[0] for t in result.timing_pairs
            ):
                continue
            for j, b in enumerate(right):
                if j in used_right:
                    continue
                within, exact = _pair_matches(
                    a, b, amount_tolerance, date_tolerance_days
                )
                if not within:
                    continue
                if prefer_exact and not exact:
                    continue
                used_right.add(j)
                if exact:
                    result.matched_pairs.append((a, b))
                else:
                    result.timing_pairs.append((a, b))
                break

    matched_left_ids = {id(a) for a, _ in result.matched_pairs}
    matched_left_ids |= {id(a) for a, _ in result.timing_pairs}
    matched_right_idx = used_right
    result.unmatched_left = [a for a in left if id(a) not in matched_left_ids]
    result.unmatched_right = [
        b for j, b in enumerate(right) if j not in matched_right_idx
    ]
    return result


def detect_duplicates(
    candidates: list[MatchCandidate],
    *,
    amount_tolerance: Decimal = Decimal("0"),
    date_tolerance_days: int = 0,
) -> list[tuple[MatchCandidate, MatchCandidate]]:
    """Return pairs within the same table that share amount + date (duplicates)."""
    dups: list[tuple[MatchCandidate, MatchCandidate]] = []
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            a, b = candidates[i], candidates[j]
            if signed_equal(a.amount, b.amount, amount_tolerance) and (
                dates_within_tolerance(a.txn_date, b.txn_date, date_tolerance_days)
            ):
                dups.append((a, b))
    return dups
