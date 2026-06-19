"""GET /api/settings (GW-8) - read-only mirror of app_settings.json."""
from __future__ import annotations

from fastapi import APIRouter

from app.app_settings import AppSettings

from api.schemas.models import SettingsInfo
from api.services.settings_view import build_settings_info

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=SettingsInfo)
def get_settings() -> SettingsInfo:
    """Return the deterministic core's settings (read-only; editable=false)."""
    return build_settings_info(AppSettings.load())
