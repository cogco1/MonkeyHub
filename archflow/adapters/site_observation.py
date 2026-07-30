"""Validate a detached site observation against explicit read authorization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from archflow.project.refs import ProjectVersionRef, require_identifier
from archflow.state.operational_state import require_logical_ref
from archflow.state.site_context import (
    Coordinate,
    GroundModel,
    GroundModelKind,
    GroundSample,
    SiteApproach,
    SiteBounds,
    SiteUnknown,
)


_MAX_ITEMS = 2_048
_MAX_TEXT = 1_000


class SiteObservationErrorCode(StrEnum):
    MALFORMED = "site_observation.malformed"
    STALE_BASE = "site_observation.stale_base"
    CROSS_WORLD = "site_observation.cross_world"
    CROSS_DIMENSION = "site_observation.cross_dimension"
    UNAUTHORIZED_ENVELOPE = "site_observation.unauthorized_envelope"
    ANCHOR_MISMATCH = "site_observation.anchor_mismatch"
    CROSS_PROJECT = "site_observation.cross_project"
    WRITE_AUTHORITY = "site_observation.write_authority"


class SiteObservationError(ValueError):
    def __init__(
        self,
        code: SiteObservationErrorCode,
        field: str,
        message: str,
    ) -> None:
        super().__init__(f"{code.value}: {field}: {message[:_MAX_TEXT]}")
        self.code = code
        self.field = field


@dataclass(frozen=True, slots=True)
class SiteObservationAuthorization:
    """Bounded read authority; it cannot be upgraded into mutation authority."""

    project_id: str
    run_id: str
    base: ProjectVersionRef
    world_id: str
    dimension_id: str
    authorized_envelope: SiteBounds
    anchor: Coordinate
    authority_id: str
    authorization_ref: str

    SCHEMA = "SiteObservationAuthorization@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ValueError(
                "site authorization and base belong to different projects"
            )
        self.base.require_digest()
        require_identifier(self.world_id, "world_id")
        require_logical_ref(self.dimension_id, "dimension_id")
        if not isinstance(self.authorized_envelope, SiteBounds):
            raise TypeError("authorized_envelope must be SiteBounds")
        _coordinate(self.anchor, "anchor")
        if not self.authorized_envelope.contains(self.anchor):
            raise ValueError("anchor lies outside authorized envelope")
        _text(self.authority_id, "authority_id")
        require_logical_ref(self.authorization_ref, "authorization_ref")
        _project_ref(self.authorization_ref, self.project_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base_state_sha256": self.base.require_digest(),
            "world_id": self.world_id,
            "dimension_id": self.dimension_id,
            "authorized_envelope": self.authorized_envelope.to_dict(),
            "anchor": list(self.anchor),
            "authority_id": self.authority_id,
            "authorization_ref": self.authorization_ref,
            "read_authorized": True,
            "world_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class AuthorizedSiteObservation:
    observation_id: str
    project_id: str
    run_id: str
    base: ProjectVersionRef
    world_id: str
    dimension_id: str
    authorization_ref: str
    authority_id: str
    authorized_envelope: SiteBounds
    observed_envelope: SiteBounds
    anchor: Coordinate
    approaches: tuple[SiteApproach, ...]
    ground_model: GroundModel
    protected_cells: tuple[Coordinate, ...]
    protection_source_refs: tuple[str, ...]
    unknowns: tuple[SiteUnknown, ...]
    source_refs: tuple[str, ...]

    SCHEMA = "AuthorizedSiteObservation@1"

    def __post_init__(self) -> None:
        require_identifier(self.observation_id, "observation_id")
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ValueError(
                "site observation and base belong to different projects"
            )
        self.base.require_digest()
        require_identifier(self.world_id, "world_id")
        require_logical_ref(self.dimension_id, "dimension_id")
        require_logical_ref(self.authorization_ref, "authorization_ref")
        _text(self.authority_id, "authority_id")
        if not isinstance(self.authorized_envelope, SiteBounds):
            raise TypeError("authorized_envelope must be SiteBounds")
        if not isinstance(self.observed_envelope, SiteBounds):
            raise TypeError("observed_envelope must be SiteBounds")
        if not self.authorized_envelope.contains_bounds(
            self.observed_envelope
        ):
            raise ValueError(
                "observed envelope exceeds authorized envelope"
            )
        _coordinate(self.anchor, "anchor")
        if not self.authorized_envelope.contains(self.anchor):
            raise ValueError("anchor lies outside authorized envelope")
        if not self.observed_envelope.contains(self.anchor):
            raise ValueError("anchor lies outside observed envelope")
        _typed(self.approaches, SiteApproach, "approaches")
        if not isinstance(self.ground_model, GroundModel):
            raise TypeError("ground_model must be GroundModel")
        _coordinates(
            self.protected_cells,
            "protected_cells",
            allow_empty=True,
        )
        _refs(
            self.protection_source_refs,
            "protection_source_refs",
            allow_empty=not self.protected_cells,
        )
        if self.protection_source_refs and not self.protected_cells:
            raise ValueError(
                "protection sources require protected cells"
            )
        _typed(self.unknowns, SiteUnknown, "unknowns")
        _refs(self.source_refs, "source_refs")
        sources = set(self.source_refs)
        source_groups = (
            *(item.source_refs for item in self.approaches),
            self.ground_model.source_refs,
            self.protection_source_refs,
            *(item.source_refs for item in self.unknowns),
        )
        if any(not set(group) <= sources for group in source_groups):
            raise ValueError(
                "site component source is absent from observation sources"
            )
        for source_ref in self.source_refs:
            _project_ref(source_ref, self.project_id)
        for coordinate in (
            *self.protected_cells,
            *(
                cell
                for approach in self.approaches
                for cell in approach.cells
            ),
            *(item.coordinate for item in self.ground_model.samples),
        ):
            if not self.observed_envelope.contains(coordinate):
                raise ValueError(
                    "site coordinate lies outside observed envelope"
                )

    @property
    def observation_digest(self) -> str:
        return _digest(self._identity())

    def _identity(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "observation_id": self.observation_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base_state_sha256": self.base.require_digest(),
            "world_id": self.world_id,
            "dimension_id": self.dimension_id,
            "authorization_ref": self.authorization_ref,
            "authority_id": self.authority_id,
            "authorized_envelope": self.authorized_envelope.to_dict(),
            "observed_envelope": self.observed_envelope.to_dict(),
            "anchor": list(self.anchor),
            "approaches": [item.to_dict() for item in self.approaches],
            "ground_model": self.ground_model.to_dict(),
            "protected_cells": [
                list(item) for item in self.protected_cells
            ],
            "protection_source_refs": list(
                self.protection_source_refs
            ),
            "unknowns": [item.to_dict() for item in self.unknowns],
            "source_refs": list(self.source_refs),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._identity(),
            "observation_digest": self.observation_digest,
            "read_only": True,
            "world_write_authority": False,
        }


def authorize_site_observation(
    payload: Mapping[str, object],
    *,
    authorization: SiteObservationAuthorization,
) -> AuthorizedSiteObservation:
    """Validate one bounded tool result without calling or mutating the world."""

    if not isinstance(authorization, SiteObservationAuthorization):
        raise TypeError(
            "authorization must be SiteObservationAuthorization"
        )
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    expected = {
        "schema",
        "observation_id",
        "project_id",
        "run_id",
        "base_state_sha256",
        "world_id",
        "dimension_id",
        "observed_envelope",
        "anchor",
        "approaches",
        "ground_model",
        "protected_cells",
        "protection_source_refs",
        "unknowns",
        "source_refs",
        "read_only",
        "world_write_authority",
    }
    if set(payload) != expected or payload.get("schema") != "SiteToolObservation@1":
        _raise(
            SiteObservationErrorCode.MALFORMED,
            "schema",
            "site tool observation schema drifted",
        )
    if payload["read_only"] is not True:
        _raise(
            SiteObservationErrorCode.MALFORMED,
            "read_only",
            "site observation must be explicitly read-only",
        )
    if payload["world_write_authority"] is not False:
        _raise(
            SiteObservationErrorCode.WRITE_AUTHORITY,
            "world_write_authority",
            "site observation cannot carry mutation authority",
        )
    if payload["project_id"] != authorization.project_id:
        _raise(
            SiteObservationErrorCode.CROSS_PROJECT,
            "project_id",
            "observation belongs to another project",
        )
    if payload["run_id"] != authorization.run_id:
        _raise(
            SiteObservationErrorCode.CROSS_PROJECT,
            "run_id",
            "observation belongs to another run",
        )
    if payload["base_state_sha256"] != authorization.base.require_digest():
        _raise(
            SiteObservationErrorCode.STALE_BASE,
            "base_state_sha256",
            "observation was made against another project version",
        )
    if payload["world_id"] != authorization.world_id:
        _raise(
            SiteObservationErrorCode.CROSS_WORLD,
            "world_id",
            "observation belongs to another world",
        )
    if payload["dimension_id"] != authorization.dimension_id:
        _raise(
            SiteObservationErrorCode.CROSS_DIMENSION,
            "dimension_id",
            "observation belongs to another dimension",
        )

    try:
        observed_envelope = SiteBounds.from_dict(
            payload["observed_envelope"]
        )
        anchor = _coordinate_from_json(payload["anchor"], "anchor")
        approaches = tuple(
            SiteApproach.from_dict(item)
            for item in _list(payload["approaches"], "approaches")
        )
        ground_model = _ground_model(payload["ground_model"])
        protected_cells = tuple(
            _coordinate_from_json(item, "protected cell")
            for item in _list(
                payload["protected_cells"],
                "protected_cells",
            )
        )
        protection_source_refs = _strings(
            payload["protection_source_refs"],
            "protection_source_refs",
        )
        unknowns = tuple(
            SiteUnknown.from_dict(item)
            for item in _list(payload["unknowns"], "unknowns")
        )
        source_refs = _strings(payload["source_refs"], "source_refs")
    except (TypeError, ValueError) as exc:
        _raise(
            SiteObservationErrorCode.MALFORMED,
            "payload",
            str(exc),
        )
    if not authorization.authorized_envelope.contains_bounds(
        observed_envelope
    ):
        _raise(
            SiteObservationErrorCode.UNAUTHORIZED_ENVELOPE,
            "observed_envelope",
            "observation exceeds the authorized envelope",
        )
    if anchor != authorization.anchor:
        _raise(
            SiteObservationErrorCode.ANCHOR_MISMATCH,
            "anchor",
            "observation anchor differs from the authorized anchor",
        )
    try:
        return AuthorizedSiteObservation(
            observation_id=payload["observation_id"],
            project_id=authorization.project_id,
            run_id=authorization.run_id,
            base=authorization.base,
            world_id=authorization.world_id,
            dimension_id=authorization.dimension_id,
            authorization_ref=authorization.authorization_ref,
            authority_id=authorization.authority_id,
            authorized_envelope=authorization.authorized_envelope,
            observed_envelope=observed_envelope,
            anchor=anchor,
            approaches=approaches,
            ground_model=ground_model,
            protected_cells=protected_cells,
            protection_source_refs=protection_source_refs,
            unknowns=unknowns,
            source_refs=source_refs,
        )
    except (TypeError, ValueError) as exc:
        _raise(
            SiteObservationErrorCode.MALFORMED,
            "payload",
            str(exc),
        )


def _ground_model(value: object) -> GroundModel:
    payload = _mapping(value, "ground_model")
    if set(payload) != {"kind", "samples", "source_refs"}:
        raise ValueError("ground_model schema drifted")
    return GroundModel(
        kind=_ground_kind(payload["kind"]),
        samples=tuple(
            GroundSample.from_dict(item)
            for item in _list(payload["samples"], "samples")
        ),
        source_refs=_strings(
            payload["source_refs"],
            "source_refs",
        ),
    )


def _ground_kind(value: object) -> GroundModelKind:
    try:
        return GroundModelKind(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("unsupported ground model kind") from exc


def _raise(
    code: SiteObservationErrorCode,
    field: str,
    message: str,
) -> None:
    raise SiteObservationError(code, field, message)


def _project_ref(value: str, project_id: str) -> None:
    require_logical_ref(value, "source_ref")
    if value.startswith("project://") and not value.startswith(
        f"project://{project_id}/"
    ):
        raise ValueError("source_ref belongs to another project")


def _coordinate(value: object, field: str) -> Coordinate:
    if (
        not isinstance(value, tuple)
        or len(value) != 3
        or any(
            not isinstance(item, int) or isinstance(item, bool)
            for item in value
        )
    ):
        raise ValueError(f"{field} must be an integer xyz tuple")
    return value


def _coordinate_from_json(value: object, field: str) -> Coordinate:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a JSON coordinate list")
    return _coordinate(tuple(value), field)


def _coordinates(
    value: object,
    field: str,
    *,
    allow_empty: bool,
) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds bounded item count")
    if not value and not allow_empty:
        raise ValueError(f"{field} cannot be empty")
    for item in value:
        _coordinate(item, field)
    if len(value) != len(set(value)):
        raise ValueError(f"{field} contains duplicates")


def _typed(value: object, item_type: type, field: str) -> None:
    if (
        not isinstance(value, tuple)
        or len(value) > _MAX_ITEMS
        or any(not isinstance(item, item_type) for item in value)
    ):
        raise TypeError(f"{field} contains the wrong item type")


def _refs(
    value: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> None:
    if not isinstance(value, tuple) or len(value) > _MAX_ITEMS:
        raise TypeError(f"{field} must be a bounded tuple")
    if not value and not allow_empty:
        raise ValueError(f"{field} cannot be empty")
    for item in value:
        require_logical_ref(item, field)
    if len(value) != len(set(value)):
        raise ValueError(f"{field} contains duplicates")


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return value


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list) or len(value) > _MAX_ITEMS:
        raise TypeError(f"{field} must be a bounded list")
    return value


def _strings(value: object, field: str) -> tuple[str, ...]:
    values = _list(value, field)
    if any(not isinstance(item, str) for item in values):
        raise TypeError(f"{field} must contain strings")
    return tuple(values)


def _text(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_TEXT
    ):
        raise ValueError(f"{field} must be bounded non-empty text")
    return value


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
