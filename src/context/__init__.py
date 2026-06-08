"""Context package (Phase 0 ``src/context``).

Loaders for NON-SENSITIVE reference material that workflows may cite:

  * :mod:`src.context.chart_of_accounts` — valid account codes + names.
  * :mod:`src.context.city_profile` — city name / default actor.
  * :mod:`src.context.context_loader` — available context references dict
    (Phase 5 freeform auto-injection).
"""
from src.context.chart_of_accounts import (  # noqa: F401
    ChartOfAccounts,
    chart_of_accounts_from_codes,
    load_chart_of_accounts,
)
from src.context.city_profile import (  # noqa: F401
    CityProfile,
    load_city_profile,
)
from src.context.context_loader import (  # noqa: F401
    get_context,
    load,
    load_available_context,
    load_context,
)

__all__ = [
    "ChartOfAccounts",
    "load_chart_of_accounts",
    "chart_of_accounts_from_codes",
    "CityProfile",
    "load_city_profile",
    "load_context",
    "get_context",
    "load_available_context",
    "load",
]
