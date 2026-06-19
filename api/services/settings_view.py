"""Builder for GET /api/settings (GW-8).

Reads app.app_settings.AppSettings (the same JSON the deterministic core uses)
and maps it to the read-only SettingsInfo response. Decimal-like numeric
tolerances are stringified so the frontend never does arithmetic on amounts.
This module READS settings only; it never writes app_settings.json.
"""
from __future__ import annotations

from app.app_settings import AppSettings

from api.schemas.models import SettingsInfo


def _num_str(value: object) -> str:
    """Render a numeric tolerance as a plain string for display."""
    return str(value)


def build_settings_info(settings: AppSettings) -> SettingsInfo:
    """Map an AppSettings dataclass to the read-only SettingsInfo model."""
    return SettingsInfo(
        city_name=settings.city_name,
        default_actor=settings.default_actor,
        llm_provider=settings.llm_provider,
        mock_mode=settings.mock_mode,
        date_tolerance_days=settings.date_tolerance_days,
        amount_tolerance=_num_str(settings.amount_tolerance),
        variance_threshold_pct=_num_str(settings.variance_threshold_pct),
        variance_dollar_threshold=_num_str(settings.variance_dollar_threshold),
        export_dir=settings.export_dir,
        role=settings.role,
        default_retention_category=settings.default_retention_category,
        editable=False,
    )
