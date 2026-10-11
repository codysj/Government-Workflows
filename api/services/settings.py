"""API settings: filesystem paths + defaults for the API process.

Defaults: ledger ``runs/ledger.db``, audit ``runs/audit``, uploads
``runs/uploads``, schedules ``runs/schedules.json``, and exports from
``app_settings.json`` (default ``runs/exports``). Tests override every path
with tmp directories. Settings are read here, never written.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.core.settings import AppSettings
from src.llm.provider import llm_mode

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

try:  # version for /api/health; fall back to the pyproject value
    from importlib.metadata import version as _pkg_version

    APP_VERSION = _pkg_version("municipal-finance-ai")
except Exception:  # pragma: no cover - package not installed
    APP_VERSION = "0.1.0"


def _default_app_settings() -> AppSettings:
    return AppSettings.load()


@dataclass
class ApiSettings:
    """Paths + defaults the API process uses. All paths are absolute."""

    ledger_db_path: Path = REPO_ROOT / "runs" / "ledger.db"
    audit_dir: Path = REPO_ROOT / "runs" / "audit"
    export_dir: Path = REPO_ROOT / "runs" / "exports"
    upload_dir: Path = REPO_ROOT / "runs" / "uploads"
    schedules_path: Path = REPO_ROOT / "runs" / "schedules.json"
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"
    default_actor: str = "finance_staff"
    default_retention_category: str = "draft_working"
    llm_mode: str = "mock"  # "mock" | "real" (mock is the default provider)
    # Settings-page tolerances applied to every run; a run's own config (form
    # field or uploaded config file) overrides them.
    run_defaults: dict = field(default_factory=dict)
    app_version: str = field(default=APP_VERSION)

    @classmethod
    def from_app_settings(cls) -> "ApiSettings":
        """Build defaults from app_settings.json (read-only)."""
        s = _default_app_settings()
        return cls(
            export_dir=Path(s.export_dir),
            default_actor=s.default_actor,
            default_retention_category=s.default_retention_category,
            llm_mode=llm_mode(),
            run_defaults={
                "date_tolerance_days": s.date_tolerance_days,
                "amount_tolerance": s.amount_tolerance,
                "variance_threshold_pct": s.variance_threshold_pct,
                "variance_dollar_threshold": s.variance_dollar_threshold,
            },
        )

    def __post_init__(self) -> None:
        self.ledger_db_path = Path(self.ledger_db_path)
        self.audit_dir = Path(self.audit_dir)
        self.export_dir = Path(self.export_dir)
        self.upload_dir = Path(self.upload_dir)
        self.schedules_path = Path(self.schedules_path)
        self.frontend_dist = Path(self.frontend_dist)
