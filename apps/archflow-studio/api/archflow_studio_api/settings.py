"""Configuration for one running ArchFlow Studio API process.

The project root is never defaulted in code: it arrives from the environment or
from ``--project-dir``. An API that guessed a project root could bind, and then
write to, a project nobody chose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import ip_address
import os
import math
from pathlib import Path
import tempfile
from typing import Mapping
from urllib.parse import urlsplit

from .transport.settings import ApplicationSettingsDto, UserSettingsDto

PROJECT_DIR_ENV = "ARCHFLOW_STUDIO_PROJECT_DIR"
CAD_EXPORT_ENV = "ARCHFLOW_STUDIO_CAD_EXPORT"
# The variable the launcher forwarded before ``cad_export`` existed. It is read
# only when ``ARCHFLOW_STUDIO_CAD_EXPORT`` is unset, and it can do two things:
# ``1`` enables the ordinary (OCCT) export, anything else it was set to keeps
# export off. It never selects Rhino; that backend is named explicitly or not at all.
RHINO_EXPORT_ENV = "ARCHFLOW_STUDIO_RHINO_EXPORT"
WORKERS_ENV = "ARCHFLOW_STUDIO_WORKERS"
POWERSHELL_ENV = "ARCHFLOW_STUDIO_POWERSHELL"
REFERENCE_RUN_ENV = "ARCHFLOW_STUDIO_REFERENCE_RUN"
INTENT_PROVIDER_ENV = "ARCHFLOW_STUDIO_INTENT_PROVIDER"
INTENT_MODEL_ENV = "ARCHFLOW_STUDIO_INTENT_MODEL"
INTENT_TIMEOUT_ENV = "ARCHFLOW_STUDIO_INTENT_TIMEOUT_S"
CONTEXT_BUDGET_ENV = "ARCHFLOW_STUDIO_CONTEXT_BUDGET_TOKENS"
CODEX_ENV = "ARCHFLOW_STUDIO_CODEX"
MODE_ENV = "ARCHFLOW_STUDIO_MODE"
BIND_ENV = "ARCHFLOW_STUDIO_BIND"
TOKEN_ENV = "ARCHFLOW_STUDIO_TOKEN"
ORIGINS_ENV = "ARCHFLOW_STUDIO_ORIGINS"
MONITOR_DIR_ENV = "MONKEYMONITOR_DATA_DIR"
RENDER_PROVIDER_ENV = "ARCHFLOW_STUDIO_RENDER_PROVIDER"
RENDER_MODEL_ENV = "ARCHFLOW_STUDIO_RENDER_MODEL"
RENDER_API_KEY_ENV = "ARCHFLOW_STUDIO_RENDER_API_KEY"
RENDER_TIMEOUT_ENV = "ARCHFLOW_STUDIO_RENDER_TIMEOUT_S"

# The two modes of the protocol boundary. ``local`` is the pair the launcher
# starts on this machine: one loopback listener, one user, no token. ``remote``
# is the same API reached over a network, and it is a different security
# question, not a different protocol: it must name a token and the origins its
# browser clients are served from.
LOCAL_MODE = "local"
REMOTE_MODE = "remote"
MODES = (LOCAL_MODE, REMOTE_MODE)
SERVICE_ROLE_ENV = "ARCHFLOW_STUDIO_SERVICE_ROLE"
ACTORS_FILE_ENV = "ARCHFLOW_STUDIO_ACTORS_FILE"
SYNC_URL_ENV = "ARCHFLOW_STUDIO_SYNC_URL"
SYNC_TOKEN_ENV = "ARCHFLOW_STUDIO_SYNC_TOKEN"
SYNC_PROJECT_ID_ENV = "ARCHFLOW_STUDIO_SYNC_PROJECT_ID"
RUNTIME_ROLE = "runtime"
SHARED_PROJECT_ROLE = "shared_project"

# What a candidate run does with each seat's compiled program. One setting,
# three values, and it is the only word the process has for it:
#
# * ``occt`` - the ordinary export: in process, no host, an exact STEP file and
#   a mesh ``.3dm`` preview of the same model per seat, on the ordinary worker
#   lanes. The default of a process nothing configured.
# * ``rhino`` - the supervised Rhino host export, one at a time on this
#   machine. Only ever chosen by name.
# * ``off`` - no export: the candidate is compiled and checked, no file is written.
CAD_EXPORT_OCCT = "occt"
CAD_EXPORT_RHINO = "rhino"
CAD_EXPORT_OFF = "off"
CAD_EXPORTS = (CAD_EXPORT_OCCT, CAD_EXPORT_RHINO, CAD_EXPORT_OFF)

DEFAULT_BIND_HOST = "127.0.0.1"

# The compiler a process runs when nothing names one: the grammar alone, no
# model. ``application/intent_agent.DETERMINISTIC`` is the same name; it is
# spelled here rather than imported because settings is read before the
# application layer exists.
DEFAULT_INTENT_PROVIDER = "deterministic"
DEFAULT_INTENT_TIMEOUT_S = 120.0
DEFAULT_CODEX_EXECUTABLE = "codex"


class SettingsError(ValueError):
    """The process cannot be configured from what the environment supplied."""


@dataclass(frozen=True, slots=True)
class StudioSettings:
    """Everything the API is allowed to know before a request arrives."""

    project_dir: Path
    # What a candidate does with its geometry: ``occt`` (the default), ``rhino``
    # or ``off``. Read once, here; every caller that asks "does this run
    # export" or "does it need the Rhino lane" asks this one field.
    cad_export: str = CAD_EXPORT_OCCT
    powershell: Path | None = None
    # Which run the projection answers for. Unset means "let the rule choose";
    # it is never a run id written into the code.
    reference_run: str | None = None
    # How many candidates may run at once. An OCCT export runs on these lanes
    # like any other kernel work; only a Rhino export takes the exclusive lane
    # one at a time.
    workers: int = 2
    # Who compiles an architect's sentence into the grammar in this process:
    # deterministic (no model at all), codex, or anthropic. These four were
    # read straight from os.environ inside the compiler until the process's
    # configuration became one object; the compiler is now built from this.
    intent_provider: str = DEFAULT_INTENT_PROVIDER
    intent_model: str | None = None
    intent_timeout_s: float = DEFAULT_INTENT_TIMEOUT_S
    codex_executable: str = DEFAULT_CODEX_EXECUTABLE
    # Which side of the protocol boundary this process is on. It changes no
    # route and no answer; it changes who may ask.
    mode: str = LOCAL_MODE
    # The interface the listener binds. Loopback by default: a service that
    # bound every interface by default would be reachable before anyone
    # decided it should be.
    bind_host: str = DEFAULT_BIND_HOST
    # The bearer token every /api route but health and protocol requires in
    # remote mode. Never read in local mode.
    api_token: str | None = field(default=None, repr=False)
    # The browser origins allowed to call this API cross-origin, in remote
    # mode only. Local mode adds no CORS at all: the dev proxy makes the two
    # halves one origin, so there is nothing to allow.
    origins: tuple[str, ...] = ()
    # Optional engineering telemetry, outside the P036 project document.
    monitor_dir: Path | None = None
    # How much of the project a sentence may be compiled against, in tokens.
    intent_context_budget_tokens: int = 16000
    # Process assembly, not a project authority field. Actor credentials belong
    # to an operator-controlled file outside the project and are loaded once.
    service_role: str = RUNTIME_ROLE
    actors_file: Path | None = None
    sync_url: str | None = None
    sync_token: str | None = field(default=None, repr=False)
    sync_project_id: str | None = None
    render_provider: str = "off"
    render_model: str | None = None
    render_api_key: str | None = field(default=None, repr=False)
    render_timeout_s: float = 120.0

    def __post_init__(self) -> None:
        """Local listeners stay on loopback; remote listeners need credentials.

        The rule lives here rather than in ``create_app`` so that no path can
        build one: the environment, ``--project-dir``, and a test constructing
        settings directly all pass through this one constructor.
        """

        if self.render_provider not in ("off", "gemini"):
            raise SettingsError(f"{RENDER_PROVIDER_ENV} must be off or gemini.")
        if not math.isfinite(self.render_timeout_s) or not 0 < self.render_timeout_s <= 300:
            raise SettingsError(f"{RENDER_TIMEOUT_ENV} must be more than 0 and at most 300 seconds.")
        if type(self.intent_context_budget_tokens) is not int or self.intent_context_budget_tokens < 1:
            raise SettingsError(f"{CONTEXT_BUDGET_ENV} must be a positive whole number.")
        if self.cad_export not in CAD_EXPORTS:
            raise SettingsError(
                f"{CAD_EXPORT_ENV} must be one of {', '.join(CAD_EXPORTS)}, not "
                f"{self.cad_export!r}."
            )
        if self.mode not in MODES:
            raise SettingsError(
                f"{MODE_ENV} must be one of {', '.join(MODES)}, not "
                f"{self.mode!r}."
            )
        if self.service_role not in (RUNTIME_ROLE, SHARED_PROJECT_ROLE):
            raise SettingsError(f"{SERVICE_ROLE_ENV} must be runtime or shared_project.")
        if self.actors_file is not None and self.mode != REMOTE_MODE:
            raise SettingsError(f"{ACTORS_FILE_ENV} requires remote mode.")
        if self.service_role == SHARED_PROJECT_ROLE and (
            self.mode != REMOTE_MODE or self.actors_file is None
        ):
            raise SettingsError(f"shared_project requires remote mode and {ACTORS_FILE_ENV}.")
        sync_values = (self.sync_url, self.sync_token, self.sync_project_id)
        if any(value is not None for value in sync_values):
            if not all(isinstance(value, str) and value.strip() for value in sync_values):
                raise SettingsError("Sync requires URL, token and project id together.")
            if self.service_role != RUNTIME_ROLE:
                raise SettingsError("Only runtime services may configure a sync upstream.")
            if self.mode != LOCAL_MODE or self.actors_file is not None:
                raise SettingsError("A connected Runtime is a local single-user service bound to one upstream actor.")
            try:
                upstream = urlsplit(self.sync_url)
                valid_url = upstream.scheme in {"http", "https"} and bool(upstream.hostname) and upstream.username is None and upstream.password is None and not upstream.query and not upstream.fragment
                upstream.port
            except ValueError:
                valid_url = False
            if not valid_url:
                raise SettingsError("Sync URL must be absolute HTTP(S), without credentials, query or fragment.")
        if self.mode != REMOTE_MODE:
            try:
                loopback = ip_address(self.bind_host).is_loopback
            except ValueError:
                loopback = self.bind_host.lower() == "localhost"
            if not loopback:
                raise SettingsError(
                    f"{MODE_ENV}={LOCAL_MODE} requires a loopback {BIND_ENV}. "
                    "Use remote mode with a token and allowed origins for a network listener."
                )
            return
        if not self.api_token and self.actors_file is None:
            raise SettingsError(
                f"{MODE_ENV}={REMOTE_MODE} requires {TOKEN_ENV}: a remote "
                "server without a token would answer anyone who found the "
                "port. Set the token, or run in local mode."
            )
        if not self.origins:
            raise SettingsError(
                f"{MODE_ENV}={REMOTE_MODE} requires {ORIGINS_ENV}: a comma "
                "separated list of the origins a browser client is served "
                "from. A remote API never guesses which sites may call it."
            )

    @property
    def exports(self) -> bool:
        """Whether a candidate run writes geometry at all."""

        return self.cad_export != CAD_EXPORT_OFF

    @property
    def rhino_lane(self) -> bool:
        """Whether a candidate needs the one Rhino this machine can export with."""

        return self.cad_export == CAD_EXPORT_RHINO

    @classmethod
    def from_env(cls) -> StudioSettings:
        """Read settings from the process environment, defaulting no project."""

        project_dir = os.environ.get(PROJECT_DIR_ENV, "").strip()
        if not project_dir:
            raise SettingsError(
                f"{PROJECT_DIR_ENV} is not set. The Studio API takes its "
                "project root from that variable or from --project-dir, and "
                "never defaults one in code."
            )
        powershell = os.environ.get(POWERSHELL_ENV, "").strip()
        reference_run = os.environ.get(REFERENCE_RUN_ENV, "").strip()
        workers_text = os.environ.get(WORKERS_ENV, "").strip()
        try:
            workers = int(workers_text) if workers_text else 2
        except ValueError as exc:
            raise SettingsError(
                f"{WORKERS_ENV} must be a whole number of workers, not "
                f"{workers_text!r}."
            ) from exc
        if workers < 1:
            raise SettingsError(f"{WORKERS_ENV} must be at least 1, not {workers}.")
        timeout_text = os.environ.get(INTENT_TIMEOUT_ENV, "").strip()
        try:
            intent_timeout_s = (
                float(timeout_text) if timeout_text else DEFAULT_INTENT_TIMEOUT_S
            )
        except ValueError as exc:
            raise SettingsError(
                f"{INTENT_TIMEOUT_ENV} must be a number of seconds, not "
                f"{timeout_text!r}."
            ) from exc
        if intent_timeout_s <= 0:
            raise SettingsError(
                f"{INTENT_TIMEOUT_ENV} must be more than 0 s, not {intent_timeout_s:g}."
            )
        try:
            context_budget = int(os.environ.get(CONTEXT_BUDGET_ENV, "").strip() or "16000")
        except ValueError as exc:
            raise SettingsError(f"{CONTEXT_BUDGET_ENV} must be a positive whole number.") from exc
        try:
            render_timeout = float(os.environ.get(RENDER_TIMEOUT_ENV, "").strip() or "120")
        except ValueError as exc:
            raise SettingsError(f"{RENDER_TIMEOUT_ENV} must be a number of seconds.") from exc
        return cls(
            project_dir=Path(project_dir),
            render_provider=os.environ.get(RENDER_PROVIDER_ENV, "").strip() or "off",
            render_model=os.environ.get(RENDER_MODEL_ENV, "").strip() or None,
            render_api_key=os.environ.get(RENDER_API_KEY_ENV, "").strip() or None,
            render_timeout_s=render_timeout,
            cad_export=cad_export_from_env(os.environ),
            powershell=Path(powershell) if powershell else None,
            reference_run=reference_run or None,
            workers=workers,
            intent_provider=(
                os.environ.get(INTENT_PROVIDER_ENV, "").strip()
                or DEFAULT_INTENT_PROVIDER
            ),
            intent_model=os.environ.get(INTENT_MODEL_ENV, "").strip() or None,
            intent_timeout_s=intent_timeout_s,
            intent_context_budget_tokens=context_budget,
            codex_executable=(
                os.environ.get(CODEX_ENV, "").strip() or DEFAULT_CODEX_EXECUTABLE
            ),
            mode=os.environ.get(MODE_ENV, "").strip() or LOCAL_MODE,
            bind_host=(
                os.environ.get(BIND_ENV, "").strip() or DEFAULT_BIND_HOST
            ),
            api_token=os.environ.get(TOKEN_ENV, "").strip() or None,
            service_role=os.environ.get(SERVICE_ROLE_ENV, "").strip() or RUNTIME_ROLE,
            actors_file=Path(os.environ[ACTORS_FILE_ENV]) if os.environ.get(ACTORS_FILE_ENV, "").strip() else None,
            sync_url=os.environ.get(SYNC_URL_ENV, "").strip() or None,
            sync_token=os.environ.get(SYNC_TOKEN_ENV, "").strip() or None,
            sync_project_id=os.environ.get(SYNC_PROJECT_ID_ENV, "").strip() or None,
            monitor_dir=Path(os.environ[MONITOR_DIR_ENV]) if os.environ.get(MONITOR_DIR_ENV, "").strip() else None,
            origins=tuple(
                origin
                for origin in (
                    piece.strip()
                    for piece in os.environ.get(ORIGINS_ENV, "").split(",")
                )
                if origin
            ),
        )


def cad_export_from_env(environ: Mapping[str, str]) -> str:
    """The one CAD export setting, from the new variable or the legacy one.

    ``ARCHFLOW_STUDIO_CAD_EXPORT`` names it outright and wins. Without it the
    legacy ``ARCHFLOW_STUDIO_RHINO_EXPORT`` is still honoured as what it always
    meant - ``1`` turns export on, any other value it was set to keeps it off -
    except that "on", which previously ran Rhino, now means the ordinary OCCT
    export. Rhino now requires explicit ``cad_export=rhino``. A process with
    neither variable exports through OCCT.
    """

    explicit = environ.get(CAD_EXPORT_ENV, "").strip().lower()
    if explicit:
        if explicit not in CAD_EXPORTS:
            raise SettingsError(
                f"{CAD_EXPORT_ENV} must be one of {', '.join(CAD_EXPORTS)}, not "
                f"{explicit!r}."
            )
        return explicit
    legacy = environ.get(RHINO_EXPORT_ENV)
    if legacy is None or not legacy.strip():
        return CAD_EXPORT_OCCT
    return CAD_EXPORT_OCCT if legacy.strip() == "1" else CAD_EXPORT_OFF


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
