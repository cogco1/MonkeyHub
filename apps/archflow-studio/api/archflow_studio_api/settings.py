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
        return cls(
            project_dir=Path(project_dir),
            rhino_export=os.environ.get(RHINO_EXPORT_ENV) == "1",
            powershell=Path(powershell) if powershell else None,
            reference_run=reference_run or None,
            workers=workers,
        )
