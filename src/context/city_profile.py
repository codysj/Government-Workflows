"""City profile context (Phase 0 ``src/context``).

A tiny configuration object describing the deploying city. Used to auto-inject
non-sensitive city references (city name, default actor) into freeform context
and to seed Settings defaults in the UI. Loadable from a dict, a JSON file, or
the environment.

Defaults match the spec / Settings page expectations:
    city_name    = "Sample City"
    default_actor = "finance_staff"
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

DEFAULT_CITY_NAME = "Sample City"
DEFAULT_ACTOR = "finance_staff"


@dataclass
class CityProfile:
    """Non-sensitive profile of the deploying city.

    ``extra`` carries any additional, non-sensitive profile fields supplied by a
    config file without forcing a schema change here.
    """

    city_name: str = DEFAULT_CITY_NAME
    default_actor: str = DEFAULT_ACTOR
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Loaders
    # ------------------------------------------------------------------ #
    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "CityProfile":
        if not data:
            return cls()
        known = {"city_name", "default_actor"}
        extra = {k: v for k, v in data.items() if k not in known}
        return cls(
            city_name=str(data.get("city_name", DEFAULT_CITY_NAME)) or DEFAULT_CITY_NAME,
            default_actor=str(data.get("default_actor", DEFAULT_ACTOR)) or DEFAULT_ACTOR,
            extra=extra,
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "CityProfile":
        p = Path(path)
        if not p.is_file():
            return cls()
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))

    @classmethod
    def from_env(cls, env: Optional[dict[str, str]] = None) -> "CityProfile":
        """Read ``CITY_NAME`` / ``DEFAULT_ACTOR`` from the environment."""
        env = env if env is not None else os.environ
        return cls(
            city_name=env.get("CITY_NAME", DEFAULT_CITY_NAME) or DEFAULT_CITY_NAME,
            default_actor=env.get("DEFAULT_ACTOR", DEFAULT_ACTOR) or DEFAULT_ACTOR,
        )

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        d = {"city_name": self.city_name, "default_actor": self.default_actor}
        d.update(self.extra)
        return d


def load_city_profile(
    source: Any = None, *, env: Optional[dict[str, str]] = None
) -> CityProfile:
    """Load a CityProfile from a dict, a JSON path, a CityProfile, or env.

    Resolution order when ``source`` is None: a ``city_profile.json`` is NOT
    auto-discovered (keep it explicit); env vars are read as a fallback so a
    deployment can set ``CITY_NAME`` / ``DEFAULT_ACTOR`` without a file.
    """
    if isinstance(source, CityProfile):
        return source
    if isinstance(source, dict):
        return CityProfile.from_dict(source)
    if isinstance(source, (str, Path)):
        return CityProfile.from_json(source)
    return CityProfile.from_env(env)


__all__ = [
    "CityProfile",
    "load_city_profile",
    "DEFAULT_CITY_NAME",
    "DEFAULT_ACTOR",
]
