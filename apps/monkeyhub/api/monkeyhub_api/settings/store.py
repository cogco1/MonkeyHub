"""The two settings files the Hub reads and replaces, and where they are.

Their locations and JSON are frozen. Every installed version shares
``%APPDATA%\\MonkeyArch\\settings.json``, so a version rolled back to still reads
what a newer one saved; ``<runtime root>/config/applications.json`` belongs to
the one Hub runtime root that names it.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

from .models import ApplicationSettingsDto, UserSettingsDto


class SettingsError(ValueError):
    """A settings location cannot be used: no absolute APPDATA, or a relative runtime root."""


def user_settings_path() -> Path:
    """The local account's preferences, never a project or request-selected path."""

    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata or not Path(appdata).is_absolute():
        raise SettingsError("APPDATA must name the local account's absolute settings directory.")
    return Path(appdata) / "MonkeyArch" / "settings.json"


def read_user_settings() -> UserSettingsDto:
    """Read saved values, or no overrides when the file does not yet exist."""

    try:
        raw = user_settings_path().read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return UserSettingsDto()
    return UserSettingsDto.model_validate_json(raw)


def save_user_settings(settings: UserSettingsDto) -> UserSettingsDto:
    """Replace one local preferences file atomically; no project state is touched."""

    _save_local_settings(user_settings_path(), settings)
    return settings


def read_application_settings(runtime_root: Path) -> ApplicationSettingsDto:
    """Read Hub launch configuration from its explicitly selected nonproject root."""

    try:
        raw = (runtime_root / "config" / "applications.json").read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return ApplicationSettingsDto()
    return ApplicationSettingsDto.model_validate_json(raw)


def save_application_settings(runtime_root: Path, settings: ApplicationSettingsDto) -> ApplicationSettingsDto:
    """App configuration has no preference, credential or project-write fields."""

    if not runtime_root.is_absolute():
        raise SettingsError("The application runtime root must be absolute.")
    _save_local_settings(runtime_root / "config" / "applications.json", settings)
    return settings


def _save_local_settings(destination: Path, settings: UserSettingsDto | ApplicationSettingsDto) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix="settings-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(settings.model_dump_json(by_alias=True, exclude_none=True, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
