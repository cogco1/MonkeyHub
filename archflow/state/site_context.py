"""Authorized, evidence-bound site context with no site-response authority."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from archflow.project.refs import ProjectVersionRef, require_identifier
from archflow.state.operational_state import (
    DesignObligation,
    require_local_id,
    require_logical_ref,
)
from archflow.contracts.canonical import canonical_digest, require_sha256


Coordinate = tuple[int, int, int]
_MAX_ITEMS = 2_048
_MAX_TEXT = 1_000


class GroundModelKind(StrEnum):
    """Observed ground character, not a selected construction response."""

    SUPERFLAT = "superflat"
    SAMPLED_LEVEL = "sampled_level"
    UNEVEN = "uneven"
    UNKNOWN = "unknown"


class SiteApproachStatus(StrEnum):
    OBSERVED = "observed"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class SiteUnknownTopic(StrEnum):
    ENVELOPE = "envelope"
    ANCHOR = "anchor"
    APPROACH = "approach"
    GROUND = "ground"
    PROTECTED_CELLS = "protected_cells"
    WORLD = "world"


@dataclass(frozen=True, slots=True, order=True)
class SiteBounds:
    minimum: Coordinate
    maximum: Coordinate

    def __post_init__(self) -> None:
        _coordinate(self.minimum, "minimum")
        _coordinate(self.maximum, "maximum")
        if any(
            high < low
            for low, high in zip(
                self.minimum,
                self.maximum,
                strict=True,
            )
        ):
            raise ValueError("site bounds maximum precedes minimum")

    @property
    def volume(self) -> int:
        return (
            (self.maximum[0] - self.minimum[0] + 1)
            * (self.maximum[1] - self.minimum[1] + 1)
            * (self.maximum[2] - self.minimum[2] + 1)
        )

    def contains(self, coordinate: Coordinate) -> bool:
        _coordinate(coordinate, "coordinate")
        return all(
            low <= value <= high
            for value, low, high in zip(
                coordinate,
                self.minimum,
                self.maximum,
                strict=True,
            )
        )

    def contains_bounds(self, other: SiteBounds) -> bool:
        if not isinstance(other, SiteBounds):
            raise TypeError("other must be SiteBounds")
        return self.contains(other.minimum) and self.contains(other.maximum)

    def to_dict(self) -> dict[str, object]:
        return {
            "minimum": list(self.minimum),
            "maximum": list(self.maximum),
        }

    @classmethod
    def from_dict(cls, value: object) -> SiteBounds:
        payload = _mapping(value, "site bounds")
        _exact(payload, {"minimum", "maximum"}, "site bounds")
        return cls(
            minimum=_coordinate_from_json(payload["minimum"], "minimum"),
            maximum=_coordinate_from_json(payload["maximum"], "maximum"),
        )


@dataclass(frozen=True, slots=True, order=True)
class GroundSample:
    coordinate: Coordinate
    source_ref: str

    def __post_init__(self) -> None:
        _coordinate(self.coordinate, "coordinate")
        require_logical_ref(self.source_ref, "source_ref")

    def to_dict(self) -> dict[str, object]:
        return {
            "coordinate": list(self.coordinate),
            "source_ref": self.source_ref,
        }

    @classmethod
    def from_dict(cls, value: object) -> GroundSample:
        payload = _mapping(value, "ground sample")
        _exact(payload, {"coordinate", "source_ref"}, "ground sample")
        return cls(
            coordinate=_coordinate_from_json(
                payload["coordinate"],
                "coordinate",
            ),
            source_ref=payload["source_ref"],
        )


@dataclass(frozen=True, slots=True)
class GroundModel:
    kind: GroundModelKind
    samples: tuple[GroundSample, ...]
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, GroundModelKind):
            raise TypeError("kind must be GroundModelKind")
        _typed(self.samples, GroundSample, "samples")
        _refs(self.source_refs, "source_refs")
        if any(
            item.source_ref not in self.source_refs
            for item in self.samples
        ):
            raise ValueError(
                "ground sample source must remain in ground source_refs"
            )
        elevations = {item.coordinate[1] for item in self.samples}
        if self.kind is GroundModelKind.SUPERFLAT:
            if len(self.samples) < 4 or len(elevations) != 1:
                raise ValueError(
                    "superflat requires at least four equal-elevation samples"
                )
        elif self.kind is GroundModelKind.SAMPLED_LEVEL:
            if not self.samples or len(elevations) != 1:
                raise ValueError(
                    "sampled_level requires equal-elevation samples"
                )
        elif self.kind is GroundModelKind.UNEVEN:
            if len(self.samples) < 2 or len(elevations) < 2:
                raise ValueError(
                    "uneven ground requires differing elevation samples"
                )
        elif self.samples:
            raise ValueError("unknown ground cannot claim samples")

    @property
    def elevation_range(self) -> tuple[int, int] | None:
        if not self.samples:
            return None
        values = tuple(item.coordinate[1] for item in self.samples)
        return (min(values), max(values))

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "samples": [item.to_dict() for item in self.samples],
            "source_refs": list(self.source_refs),
            "elevation_range": (
                list(self.elevation_range)
                if self.elevation_range is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> GroundModel:
        payload = _mapping(value, "ground model")
        _exact(
            payload,
            {"kind", "samples", "source_refs", "elevation_range"},
            "ground model",
        )
        model = cls(
            kind=_enum(GroundModelKind, payload["kind"], "kind"),
            samples=tuple(
                GroundSample.from_dict(item)
                for item in _list(payload["samples"], "samples")
            ),
            source_refs=_strings(
                payload["source_refs"],
                "source_refs",
            ),
        )
        expected = (
            list(model.elevation_range)
            if model.elevation_range is not None
            else None
        )
        if payload["elevation_range"] != expected:
            raise ValueError("ground elevation range drifted")
        return model


@dataclass(frozen=True, slots=True)
class SiteApproach:
    approach_id: str
    status: SiteApproachStatus
    cells: tuple[Coordinate, ...]
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        require_local_id(self.approach_id, "approach_id")
        if not isinstance(self.status, SiteApproachStatus):
            raise TypeError("status must be SiteApproachStatus")
        _coordinates(self.cells, "cells", allow_empty=True)
        _refs(self.source_refs, "source_refs")
        if (
            self.status is SiteApproachStatus.OBSERVED
            and not self.cells
        ):
            raise ValueError("observed approach requires observed cells")
        if (
            self.status is SiteApproachStatus.UNKNOWN
            and self.cells
        ):
            raise ValueError("unknown approach cannot claim observed cells")

    def to_dict(self) -> dict[str, object]:
        return {
            "approach_id": self.approach_id,
            "status": self.status.value,
            "cells": [list(item) for item in self.cells],
            "source_refs": list(self.source_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> SiteApproach:
        payload = _mapping(value, "site approach")
        _exact(
            payload,
            {"approach_id", "status", "cells", "source_refs"},
            "site approach",
        )
        return cls(
            approach_id=payload["approach_id"],
            status=_enum(
                SiteApproachStatus,
                payload["status"],
                "status",
            ),
            cells=tuple(
                _coordinate_from_json(item, "approach cell")
                for item in _list(payload["cells"], "cells")
            ),
            source_refs=_strings(
                payload["source_refs"],
                "source_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class SiteUnknown:
    unknown_id: str
    topic: SiteUnknownTopic
    statement: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        require_local_id(self.unknown_id, "unknown_id")
        if not isinstance(self.topic, SiteUnknownTopic):
            raise TypeError("topic must be SiteUnknownTopic")
        _text(self.statement, "statement")
        _refs(self.source_refs, "source_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "unknown_id": self.unknown_id,
            "topic": self.topic.value,
            "statement": self.statement,
            "source_refs": list(self.source_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> SiteUnknown:
        payload = _mapping(value, "site unknown")
        _exact(
            payload,
            {"unknown_id", "topic", "statement", "source_refs"},
            "site unknown",
        )
        return cls(
            unknown_id=payload["unknown_id"],
            topic=_enum(
                SiteUnknownTopic,
                payload["topic"],
                "topic",
            ),
            statement=payload["statement"],
            source_refs=_strings(
                payload["source_refs"],
                "source_refs",
            ),
        )


@dataclass(frozen=True, slots=True)
class SiteContext:
    """Observed site input with no design-response vocabulary or authority."""

    project_id: str
    run_id: str
    base: ProjectVersionRef
    brief_digest: str
    compiler_id: str
    compiler_version: str
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
    observation_digest: str
    unknowns: tuple[SiteUnknown, ...]
    obligations: tuple[DesignObligation, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "SiteContext@2"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ValueError("site context and base belong to different projects")
        self.base.require_digest()
        require_sha256(self.brief_digest, "brief_digest")
        _text(self.compiler_id, "compiler_id")
        _text(self.compiler_version, "compiler_version")
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
        require_sha256(self.observation_digest, "observation_digest")
        _typed(self.unknowns, SiteUnknown, "unknowns")
        _typed(self.obligations, DesignObligation, "obligations")
        _refs(self.evidence_refs, "evidence_refs")
        evidence = set(self.evidence_refs)
        source_groups = (
            *(item.source_refs for item in self.approaches),
            self.ground_model.source_refs,
            self.protection_source_refs,
            *(item.source_refs for item in self.unknowns),
        )
        if any(not set(group) <= evidence for group in source_groups):
            raise ValueError(
                "site component source is absent from context evidence"
            )
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
                    "observed site coordinate lies outside observed envelope"
                )
        _unique(
            tuple(item.approach_id for item in self.approaches),
            "approach ids",
        )
        _unique(
            tuple(item.unknown_id for item in self.unknowns),
            "unknown ids",
        )
        _unique(
            tuple(item.obligation_id for item in self.obligations),
            "obligation ids",
        )

    @property
    def context_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": {
                "project_id": self.base.project_id,
                "version": self.base.version,
                "state_sha256": self.base.require_digest(),
            },
            "brief_digest": self.brief_digest,
            "compiler_id": self.compiler_id,
            "compiler_version": self.compiler_version,
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
            "observation_digest": self.observation_digest,
            "unknowns": [item.to_dict() for item in self.unknowns],
            "obligations": [
                item.to_dict() for item in self.obligations
            ],
            "evidence_refs": list(self.evidence_refs),
            "generation_authority": False,
            "world_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> SiteContext:
        payload = _mapping(value, "site context")
        _exact(
            payload,
            {
                "schema",
                "project_id",
                "run_id",
                "base",
                "brief_digest",
                "compiler_id",
                "compiler_version",
                "world_id",
                "dimension_id",
                "authorization_ref",
                "authority_id",
                "authorized_envelope",
                "observed_envelope",
                "anchor",
                "approaches",
                "ground_model",
                "protected_cells",
                "protection_source_refs",
                "observation_digest",
                "unknowns",
                "obligations",
                "evidence_refs",
                "generation_authority",
                "world_write_authority",
            },
            "site context",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ValueError("unsupported site context schema")
        base = _mapping(payload["base"], "base")
        _exact(
            base,
            {"project_id", "version", "state_sha256"},
            "base",
        )
        return cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=ProjectVersionRef(
                project_id=base["project_id"],
                version=base["version"],
                state_sha256=base["state_sha256"],
            ),
            brief_digest=payload["brief_digest"],
            compiler_id=payload["compiler_id"],
            compiler_version=payload["compiler_version"],
            world_id=payload["world_id"],
            dimension_id=payload["dimension_id"],
            authorization_ref=payload["authorization_ref"],
            authority_id=payload["authority_id"],
            authorized_envelope=SiteBounds.from_dict(
                payload["authorized_envelope"]
            ),
            observed_envelope=SiteBounds.from_dict(
                payload["observed_envelope"]
            ),
            anchor=_coordinate_from_json(payload["anchor"], "anchor"),
            approaches=tuple(
                SiteApproach.from_dict(item)
                for item in _list(payload["approaches"], "approaches")
            ),
            ground_model=GroundModel.from_dict(
                payload["ground_model"]
            ),
            protected_cells=tuple(
                _coordinate_from_json(item, "protected cell")
                for item in _list(
                    payload["protected_cells"],
                    "protected_cells",
                )
            ),
            protection_source_refs=_strings(
                payload["protection_source_refs"],
                "protection_source_refs",
            ),
            observation_digest=payload["observation_digest"],
            unknowns=tuple(
                SiteUnknown.from_dict(item)
                for item in _list(payload["unknowns"], "unknowns")
            ),
            obligations=tuple(
                DesignObligation.from_dict(item)
                for item in _list(
                    payload["obligations"],
                    "obligations",
                )
            ),
            evidence_refs=_strings(
                payload["evidence_refs"],
                "evidence_refs",
            ),
        )


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
    items = _tuple(value, field)
    if not items and not allow_empty:
        raise ValueError(f"{field} cannot be empty")
    for item in items:
        _coordinate(item, field)
    _unique(tuple(items), field)


def _text(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_TEXT
    ):
        raise ValueError(f"{field} must be bounded non-empty text")
    return value


def _tuple(value: object, field: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds bounded item count")
    return value


def _typed(value: object, item_type: type, field: str) -> None:
    items = _tuple(value, field)
    if any(not isinstance(item, item_type) for item in items):
        raise TypeError(f"{field} contains the wrong item type")


def _refs(
    value: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> None:
    items = _tuple(value, field)
    if not items and not allow_empty:
        raise ValueError(f"{field} cannot be empty")
    for item in items:
        require_logical_ref(item, field)
    _unique(tuple(items), field)


def _unique(values: tuple[object, ...], field: str) -> None:
    if len(values) != len(set(values)):
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


def _exact(
    payload: Mapping[str, object],
    keys: set[str],
    field: str,
) -> None:
    if set(payload) != keys:
        raise ValueError(f"{field} schema drifted")


def _enum(enum_type: type[StrEnum], value: object, field: str) -> StrEnum:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not a supported value") from exc


