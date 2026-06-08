"""Simple JSON-backed settings for the Streamlit MVP (Phase 6 Settings page).

No authentication (per spec). Settings persist to ``app_settings.json`` at the
repo root. This module has NO Streamlit dependency so it stays testable and the
UI/core separation holds.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = REPO_ROOT / "app_settings.json"
DEFAULT_EXPORT_DIR = REPO_ROOT / "runs" / "exports"


@dataclass
class AppSettings:
    city_name: str = "City of Example"
    default_actor: str = "finance_staff"
    llm_provider: str = "mock"
    mock_mode: bool = True
    date_tolerance_days: int = 3
    amount_tolerance: float = 0.01
    variance_threshold_pct: float = 10.0
    variance_dollar_threshold: float = 10000.0
    export_dir: str = str(DEFAULT_EXPORT_DIR)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AppSettings":
        p = Path(path) if path is not None else SETTINGS_PATH
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
            known = {f for f in cls.__dataclass_fields__}  # noqa: SIM118
            return cls(**{k: v for k, v in data.items() if k in known})
        return cls()

    def save(self, path: str | Path | None = None) -> Path:
        p = Path(path) if path is not None else SETTINGS_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return p

    def as_dict(self) -> dict:
        return asdict(self)
