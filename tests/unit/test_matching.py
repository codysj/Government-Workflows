"""The indexed matcher must agree exactly with the obvious O(n^2) definition."""
from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from src.normalize.finance_rules import dates_within_tolerance, signed_equal
from src.normalize.matching import MatchCandidate, detect_duplicates, match_records


def _brute_match(left, right, tol, days):
    matched, timing, used, done = [], [], set(), set()
    for prefer_exact in (True, False):
        for li, a in enumerate(left):
            if li in done:
                continue
            for j, b in enumerate(right):
                if j in used or not signed_equal(a.amount, b.amount, tol):
                    continue
                exact = dates_within_tolerance(a.txn_date, b.txn_date, 0)
                within = dates_within_tolerance(a.txn_date, b.txn_date, days)
                if within and (exact or not prefer_exact):
                    used.add(j)
                    done.add(li)
                    (matched if exact else timing).append((a, b))
                    break
    return matched, timing


def _brute_dups(cands, tol, days):
    return [(a, b) for i, a in enumerate(cands) for b in cands[i + 1:]
            if signed_equal(a.amount, b.amount, tol)
            and dates_within_tolerance(a.txn_date, b.txn_date, days)]


def _cands(rng, n):
    base = date(2026, 1, 1)
    out = []
    for i in range(n):
        amt = None if rng.random() < 0.05 else Decimal(rng.choice([-1, 1]) * rng.randint(1, 30)) / 4
        d = None if rng.random() < 0.05 else base + timedelta(days=rng.randint(0, 10))
        out.append(MatchCandidate(index=i, amount=amt, txn_date=d))
    return out


def test_indexed_matcher_equals_brute_force():
    rng = random.Random(7)
    for _ in range(300):
        left, right = _cands(rng, rng.randint(0, 25)), _cands(rng, rng.randint(0, 25))
        tol = Decimal(rng.choice(["0", "0.25", "1"]))
        days = rng.choice([0, 2, 5])
        got = match_records(left, right, amount_tolerance=tol, date_tolerance_days=days)
        exp_matched, exp_timing = _brute_match(left, right, tol, days)
        assert got.matched_pairs == exp_matched
        assert got.timing_pairs == exp_timing
        assert detect_duplicates(left, amount_tolerance=tol, date_tolerance_days=days) == \
            _brute_dups(left, tol, days)
