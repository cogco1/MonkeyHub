"""Configuration for one running ArchFlow Studio API process.

The project root is never defaulted in code: it arrives from the environment or
from ``--project-dir``. An API that guessed a project root could bind, and then
write to, a project nobody chose.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping

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
CODEX_ENV = "ARCHFLOW_STUDIO_CODEX"
MODE_ENV = "ARCHFLOW_STUDIO_MODE"
BIND_ENV = "ARCHFLOW_STUDIO_BIND"
TOKEN_ENV = "ARCHFLOW_STUDIO_TOKEN"
ORIGINS_ENV = "ARCHFLOW_STUDIO_ORIGINS"

# The two modes of the protocol boundary. ``local`` is the pair the launcher
# starts on this machine: one loopback listener, one user, no token. ``remote``
# is the same API reached over a network, and it is a different security
# question, not a different protocol: it must name a token and the origins its
# browser clients are served from.
LOCAL_MODE = "local"
REMOTE_MODE = "remote"
MODES = (LOCAL_MODE, REMOTE_MODE)

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
    api_token: str | None = None
    # The browser origins allowed to call this API cross-origin, in remote
    # mode only. Local mode adds no CORS at all: the dev proxy makes the two
    # halves one origin, so there is nothing to allow.
    origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """A remote process without a token is not a configuration that exists.

        The rule lives here rather than in ``create_app`` so that no path can
        build one: the environment, ``--project-dir``, and a test constructing
        settings directly all pass through this one constructor.
        """

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
        if self.mode != REMOTE_MODE:
            return
        if not self.api_token:
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
        return cls(
            project_dir=Path(project_dir),
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
            codex_executable=(
                os.environ.get(CODEX_ENV, "").strip() or DEFAULT_CODEX_EXECUTABLE
            ),
            mode=os.environ.get(MODE_ENV, "").strip() or LOCAL_MODE,
            bind_host=(
                os.environ.get(BIND_ENV, "").strip() or DEFAULT_BIND_HOST
            ),
            api_token=os.environ.get(TOKEN_ENV, "").strip() or None,
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
