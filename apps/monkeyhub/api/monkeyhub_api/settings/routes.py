"""The settings routes: this Hub's launch choices, the provider keys, and the local account's preferences.

No project is opened by any of them. The Hub serves the three apart, each where
its routes stand in the Hub's route order.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import ValidationError
from starlette.requests import Request

from .. import projects
from ..chat import providers
from ..models import (
    CredentialCheck, CredentialId, CredentialStatus, CredentialWrite, HubError, HubFailure, ProviderLinkId,
)
from . import credentials
from .models import ApplicationSettingsDto, UserSettingsDto
from .store import SettingsError, read_application_settings, read_user_settings, save_user_settings


def app_settings_router(settings, applications, chats) -> APIRouter:
    """GET/PUT /api/settings/apps: the launch choices saved in this Hub's own runtime root."""
    routes = APIRouter()

    @routes.get("/api/settings/apps", response_model=ApplicationSettingsDto, response_model_by_alias=True)
    def application_settings() -> ApplicationSettingsDto:
        return read_application_settings(settings.runtime_root)

    @routes.put("/api/settings/apps", response_model=ApplicationSettingsDto, response_model_by_alias=True)
    def update_application_settings(body: ApplicationSettingsDto) -> ApplicationSettingsDto:
        if body.library_dir is not None:
            # The skill library (#252) is a complete project, checked as one.
            _, library = projects._project(body.library_dir)
            body = body.model_copy(update={"library_dir": library})
        with chats.project_configuration(body.project_dir):
            return applications.configure(body)

    return routes


_ERROR_RESPONSES = {409: {"model": HubError}, 503: {"model": HubError}}


# #334: provider keys, written from Settings into the account's credential
# store. A response says whether a key is in use and where it comes from;
# none ever carries the key.
def credential_status(credential_id: str) -> CredentialStatus:
    available = credentials.secret_store().available
    saved = credentials.saved(credential_id) is not None
    variable = credentials.from_environment(credential_id)
    if variable is not None:
        return CredentialStatus(id=credential_id, configured=True, source="environment", variable=variable,
                                saved=saved, storeAvailable=available)
    if saved:
        return CredentialStatus(id=credential_id, configured=True, source="saved", saved=True, storeAvailable=available)
    if credential_id == "coding-plan" and providers.claude_plan_configured():
        return CredentialStatus(id=credential_id, configured=True, source="claude-config", storeAvailable=available)
    return CredentialStatus(id=credential_id, configured=False, storeAvailable=available)


credentials_router = APIRouter()


@credentials_router.get("/api/credentials", response_model=list[CredentialStatus])
def list_credentials():
    return [credential_status(credential_id) for credential_id in credentials.SLOTS]


@credentials_router.put("/api/credentials/{credential_id}", response_model=CredentialStatus, responses=_ERROR_RESPONSES)
def save_credential(credential_id: CredentialId, body: CredentialWrite):
    if not credentials.secret_store().available:
        raise HubFailure(409, "CREDENTIAL_STORE_UNAVAILABLE",
                         "This system has no account credential store; provide the key through the environment instead.")
    try:
        credentials.save(credential_id, body.key.get_secret_value())
    except credentials.CredentialError as exc:
        raise HubFailure(422, "CREDENTIAL_INVALID", str(exc)) from None
    except OSError:
        raise HubFailure(503, "CREDENTIAL_STORE_FAILED", "The account credential store did not take the key. Try again.") from None
    return credential_status(credential_id)


@credentials_router.delete("/api/credentials/{credential_id}", response_model=CredentialStatus, responses=_ERROR_RESPONSES)
def clear_credential(credential_id: CredentialId):
    try:
        credentials.clear(credential_id)
    except OSError:
        raise HubFailure(503, "CREDENTIAL_STORE_FAILED", "The account credential store did not remove the key. Try again.") from None
    return credential_status(credential_id)


@credentials_router.post("/api/credentials/gemini/check", response_model=CredentialCheck)
def check_gemini():
    return CredentialCheck(id="gemini", **credentials.check_gemini(credentials.resolve("gemini")))


@credentials_router.post("/api/links/{link_id}/open", status_code=202, responses=_ERROR_RESPONSES)
def open_provider_link(link_id: ProviderLinkId):
    """Where to get a key, opened in the system browser: the desktop window keeps to the Hub's own pages."""
    if not credentials.open_link(link_id):
        raise HubFailure(503, "LINK_UNAVAILABLE", "No browser could be opened from here.")
    return {"opened": True}


def require_local_settings(request: Request) -> None:
    if request.app.state.settings.mode != "local":
        raise HubFailure(404, "NOT_FOUND", "User settings are available in local mode only.")


router = APIRouter(tags=["settings"], dependencies=[Depends(require_local_settings)])


@router.get("/settings/user", response_model=UserSettingsDto, response_model_by_alias=True, response_model_exclude_none=True)
def get_user_settings() -> UserSettingsDto:
    try:
        return read_user_settings()
    except (ValidationError, UnicodeError) as exc:
        raise HubFailure(422, "USER_SETTINGS_INVALID", "Saved user settings are invalid. Save valid preferences to replace them.") from exc
    except (SettingsError, OSError) as exc:
        raise HubFailure(503, "USER_SETTINGS_UNAVAILABLE", "The local user settings file could not be read.") from exc


@router.put("/settings/user", response_model=UserSettingsDto, response_model_by_alias=True, response_model_exclude_none=True)
def put_user_settings(body: UserSettingsDto) -> UserSettingsDto:
    try:
        return save_user_settings(body)
    except (SettingsError, OSError) as exc:
        raise HubFailure(503, "USER_SETTINGS_UNAVAILABLE", "The local user settings file could not be saved.") from exc
