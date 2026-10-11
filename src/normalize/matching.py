"""Generic deterministic amount+date matching helpers (used by bank rec).

The LLM never performs matching. This module is the authoritative matcher.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
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


def _by_magnitude(items: list[MatchCandidate]) -> tuple[list[Decimal], list[int]]:
    """Positions of items with an amount, sorted by |amount| (for range lookups)."""
    order = sorted((i for i, c in enumerate(items) if c.amount is not None),
                   key=lambda i: abs(items[i].amount))
    return [abs(items[i].amount) for i in order], order


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
    Each right record is consumed at most once; a left record takes the FIRST
    unused right record (in file order) that qualifies.

    Right records are indexed by |amount|, so each left record only looks at
    the right records inside its amount tolerance (O(n log n), not O(n^2)).
    """
    result = MatchResult()
    used_right: set[int] = set()
    matched_left: set[int] = set()
    tol = Decimal(amount_tolerance)
    keys, order = _by_magnitude(right)

    # Pass 1: prefer exact-date matches.
    # Pass 2: allow within-tolerance (timing) matches.
    for prefer_exact in (True, False):
        for li, a in enumerate(left):
            if li in matched_left or a.amount is None:
                continue
            mag = abs(a.amount)
            lo, hi = bisect_left(keys, mag - tol), bisect_right(keys, mag + tol)
            best: Optional[tuple[int, bool]] = None
            for j in order[lo:hi]:
                if j in used_right or (best is not None and j > best[0]):
                    continue
                within, exact = _pair_matches(a, right[j], amount_tolerance, date_tolerance_days)
                if within and (exact or not prefer_exact):
                    best = (j, exact)
            if best is None:
                continue
            j, exact = best
            used_right.add(j)
            matched_left.add(li)
            (result.matched_pairs if exact else result.timing_pairs).append((a, right[j]))

    result.unmatched_left = [a for li, a in enumerate(left) if li not in matched_left]
    result.unmatched_right = [b for j, b in enumerate(right) if j not in used_right]
    return result


def detect_duplicates(
    candidates: list[MatchCandidate],
    *,
    amount_tolerance: Decimal = Decimal("0"),
    date_tolerance_days: int = 0,
) -> list[tuple[MatchCandidate, MatchCandidate]]:
    """Return pairs within the same table that share amount + date (duplicates).

    Pairs are returned in (i, j) file order; only candidates inside each
    other's amount tolerance are compared (sorted sweep, not all pairs).
    """
    tol = Decimal(amount_tolerance)
    keys, order = _by_magnitude(candidates)
    pairs: list[tuple[int, int]] = []
    for pos, i in enumerate(order):
        for nxt in range(pos + 1, len(order)):
            if keys[nxt] - keys[pos] > tol:
                break
            j = order[nxt]
            a, b = candidates[min(i, j)], candidates[max(i, j)]
            if dates_within_tolerance(a.txn_date, b.txn_date, date_tolerance_days):
                pairs.append((min(i, j), max(i, j)))
    return [(candidates[i], candidates[j]) for i, j in sorted(pairs)]
