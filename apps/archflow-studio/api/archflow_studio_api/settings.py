"""Configuration for one running ArchFlow Studio API process.

The project root is never defaulted in code: it arrives from the environment or
from ``--project-dir``. An API that guessed a project root could bind, and then
write to, a project nobody chose.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

PROJECT_DIR_ENV = "ARCHFLOW_STUDIO_PROJECT_DIR"
RHINO_EXPORT_ENV = "ARCHFLOW_STUDIO_RHINO_EXPORT"
WORKERS_ENV = "ARCHFLOW_STUDIO_WORKERS"
POWERSHELL_ENV = "ARCHFLOW_STUDIO_POWERSHELL"
REFERENCE_RUN_ENV = "ARCHFLOW_STUDIO_REFERENCE_RUN"
INTENT_PROVIDER_ENV = "ARCHFLOW_STUDIO_INTENT_PROVIDER"
INTENT_MODEL_ENV = "ARCHFLOW_STUDIO_INTENT_MODEL"
INTENT_TIMEOUT_ENV = "ARCHFLOW_STUDIO_INTENT_TIMEOUT_S"
CODEX_ENV = "ARCHFLOW_STUDIO_CODEX"

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
    rhino_export: bool = False
    powershell: Path | None = None
    # Which run the projection answers for. Unset means "let the rule choose";
    # it is never a run id written into the code.
    reference_run: str | None = None
    # How many candidates may run at once. Exports still take the exclusive
    # lane one at a time; this bounds the kernel-only runs beside them.
    workers: int = 2
    # Who compiles an architect's sentence into the grammar in this process:
    # deterministic (no model at all), codex, or anthropic. These four were
    # read straight from os.environ inside the compiler until the process's
    # configuration became one object; the compiler is now built from this.
    intent_provider: str = DEFAULT_INTENT_PROVIDER
    intent_model: str | None = None
    intent_timeout_s: float = DEFAULT_INTENT_TIMEOUT_S
    codex_executable: str = DEFAULT_CODEX_EXECUTABLE

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
            rhino_export=os.environ.get(RHINO_EXPORT_ENV) == "1",
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
        )
