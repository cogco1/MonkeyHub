"""Immutable project identity and format metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Mapping

from archflow.project.refs import require_identifier


class ProjectManifestError(ValueError):
    """The project manifest is malformed or carries mutable state."""


@dataclass(frozen=True, slots=True)
class ProjectManifest:
    project_id: str
    format_version: int = 1

    SCHEMA: ClassVar[str] = "ArchFlowProject@1"
    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"schema", "project_id", "format_version"}
    )

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        if (
            not isinstance(self.format_version, int)
            or isinstance(self.format_version, bool)
            or self.format_version < 1
        ):
            raise ProjectManifestError(
                "format_version must be a positive integer"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "format_version": self.format_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProjectManifest:
        if not isinstance(payload, Mapping):
            raise ProjectManifestError("project manifest must be an object")
        if set(payload) != cls._FIELDS:
            raise ProjectManifestError(
                "project manifest fields are incomplete or unsupported"
            )
        if payload.get("schema") != cls.SCHEMA:
            raise ProjectManifestError("project manifest schema is unsupported")
        try:
            return cls(
                project_id=payload["project_id"],
                format_version=payload["format_version"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectManifestError("project manifest values are invalid") from exc
