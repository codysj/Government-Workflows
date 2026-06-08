"""Shared finance helpers: tolerance comparison and signed-amount handling."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional


def amounts_match(
    a: Optional[Decimal],
    b: Optional[Decimal],
    amount_tolerance: Decimal = Decimal("0"),
) -> bool:
    """True when |a - b| <= amount_tolerance. None never matches."""
    if a is None or b is None:
        return False
    return abs(Decimal(a) - Decimal(b)) <= Decimal(amount_tolerance)


def dates_within_tolerance(
    a: Optional[date], b: Optional[date], date_tolerance_days: int = 0
) -> bool:
    """True when the two dates are within date_tolerance_days of each other."""
    if a is None or b is None:
        return False
    return abs((a - b).days) <= int(date_tolerance_days)


def normalize_sign(amount: Optional[Decimal]) -> Optional[Decimal]:
    """Return the absolute magnitude of an amount (sign-insensitive matching)."""
    if amount is None:
        return None
    return abs(Decimal(amount))


def signed_equal(
    a: Optional[Decimal], b: Optional[Decimal], amount_tolerance: Decimal = Decimal("0")
) -> bool:
    """Tolerance compare on absolute magnitudes (ignores debit/credit sign)."""
    return amounts_match(normalize_sign(a), normalize_sign(b), amount_tolerance)
