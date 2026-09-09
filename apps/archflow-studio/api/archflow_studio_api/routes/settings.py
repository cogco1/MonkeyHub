"""The local account's preferences; no project is opened by either route."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import ValidationError
from starlette.requests import Request

from ..settings import LOCAL_MODE, SettingsError, read_user_settings, save_user_settings
from ..transport.errors import StudioError
from ..transport.settings import UserSettingsDto


def require_local_settings(request: Request) -> None:
    if request.app.state.settings.mode != LOCAL_MODE:
        raise StudioError(404, "NOT_FOUND", "User settings are available in local mode only.")


router = APIRouter(tags=["settings"], dependencies=[Depends(require_local_settings)])


@router.get("/settings/user", response_model=UserSettingsDto, response_model_by_alias=True, response_model_exclude_none=True)
def get_user_settings() -> UserSettingsDto:
    try:
        return read_user_settings()
    except (ValidationError, UnicodeError) as exc:
        raise StudioError(422, "USER_SETTINGS_INVALID", "Saved user settings are invalid. Save valid preferences to replace them.") from exc
    except (SettingsError, OSError) as exc:
        raise StudioError(503, "USER_SETTINGS_UNAVAILABLE", "The local user settings file could not be read.") from exc


@router.put("/settings/user", response_model=UserSettingsDto, response_model_by_alias=True, response_model_exclude_none=True)
def put_user_settings(body: UserSettingsDto) -> UserSettingsDto:
    try:
        return save_user_settings(body)
    except (SettingsError, OSError) as exc:
        raise StudioError(503, "USER_SETTINGS_UNAVAILABLE", "The local user settings file could not be saved.") from exc
