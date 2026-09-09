"""Stage declaration gates: mandatory typed decisions before a stage passes.

A ``StageDeclarationContract`` names the decision fields a provider must
declare at one maturity stage, grouped into quadrants (dimensions,
structure, openings, detail). Every field carries a kind, unit, authorized
range, and range provenance; the framework stores no field values or
stage defaults of its own. Validation is fail-closed: a missing field, an
out-of-range value, or a declaration that disagrees with the authored
massing geometry beyond the contract tolerance is a typed rejection —
never a repair.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from archflow.contracts.canonical import canonical_digest

_ID = re.compile(r"^[a-z0-9][a-z0-9\-]{0,80}$")


class DeclarationError(ValueError):
    """A declaration contract or a declared value set is invalid."""


class DeclarationQuadrant(StrEnum):
    SITE = "site"
    DIMENSIONS = "dimensions"
    STRUCTURE = "structure"
    OPENINGS = "openings"
    DETAIL = "detail"


class DeclarationKind(StrEnum):
    NUMBER = "number"
    COUNT = "count"
    RATIO = "ratio"


class GeometryCheck(StrEnum):
    """Deterministic cross-check binding a declared value to the massing."""

    NONE = "none"
    FOOTPRINT_WIDTH = "footprint_width"
    FOOTPRINT_DEPTH = "footprint_depth"
    OVERALL_HEIGHT = "overall_height"
    PRIMARY_SPAN = "primary_span"
    FOOTPRINT_FILL_RATIO = "footprint_fill_ratio"


@dataclass(frozen=True, slots=True)
class DeclarationField:
    field_id: str
    quadrant: DeclarationQuadrant
    kind: DeclarationKind
    unit: str | None
    minimum: float
    maximum: float
    geometry_check: GeometryCheck
    source_refs: tuple[str, ...]
    statement: str

    SCHEMA = "DeclarationField@1"

    def __post_init__(self) -> None:
        if not _ID.match(self.field_id):
            raise DeclarationError("field_id must be a kebab identifier")
        if not isinstance(self.quadrant, DeclarationQuadrant):
            raise TypeError("quadrant must be DeclarationQuadrant")
        if not isinstance(self.kind, DeclarationKind):
            raise TypeError("kind must be DeclarationKind")
        if not isinstance(self.geometry_check, GeometryCheck):
            raise TypeError("geometry_check must be GeometryCheck")
        if not (
            isinstance(self.minimum, (int, float))
            and isinstance(self.maximum, (int, float))
            and self.minimum <= self.maximum
        ):
            raise DeclarationError("range must satisfy minimum <= maximum")
        if not isinstance(self.source_refs, tuple) or not self.source_refs:
            raise DeclarationError("range provenance source_refs required")
        if not isinstance(self.statement, str) or not self.statement.strip():
            raise DeclarationError("statement must be non-empty text")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "field_id": self.field_id,
            "quadrant": self.quadrant.value,
            "kind": self.kind.value,
            "unit": self.unit,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "geometry_check": self.geometry_check.value,
            "source_refs": list(self.source_refs),
            "statement": self.statement,
        }

    @classmethod
    def from_dict(cls, value) -> "DeclarationField":
        if not isinstance(value, Mapping) or value.get("schema") != cls.SCHEMA:
            raise DeclarationError("declaration field schema drifted")
        return cls(
            field_id=value["field_id"],
            quadrant=DeclarationQuadrant(value["quadrant"]),
            kind=DeclarationKind(value["kind"]),
            unit=value.get("unit"),
            minimum=float(value["minimum"]),
            maximum=float(value["maximum"]),
            geometry_check=GeometryCheck(value["geometry_check"]),
            source_refs=tuple(value["source_refs"]),
            statement=value["statement"],
        )


@dataclass(frozen=True, slots=True)
class StageDeclarationContract:
    """The mandatory declaration set for one maturity stage."""

    stage: str
    fields: tuple[DeclarationField, ...]
    tolerance_ratio: float

    SCHEMA = "StageDeclarationContract@1"

    def __post_init__(self) -> None:
        if not isinstance(self.stage, str) or not self.stage.strip():
            raise DeclarationError("stage must be non-empty text")
        if not isinstance(self.fields, tuple) or not self.fields:
            raise DeclarationError("contract requires at least one field")
        field_ids = [field.field_id for field in self.fields]
        if field_ids != sorted(set(field_ids)):
            raise DeclarationError("field ids must be sorted and unique")
        if not (
            isinstance(self.tolerance_ratio, (int, float))
            and 0.0 < self.tolerance_ratio < 1.0
        ):
            raise DeclarationError("tolerance_ratio must be inside (0, 1)")

    @property
    def contract_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "stage": self.stage,
            "fields": [field.to_dict() for field in self.fields],
            "tolerance_ratio": self.tolerance_ratio,
            "required_output_field": "stage_declarations",
            "pass_rule": (
                "every field declared, every value inside its authorized "
                "range, and every geometry-checked value matching the "
                "authored massing within the tolerance ratio"
            ),
            "field_value_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value) -> "StageDeclarationContract":
        if not isinstance(value, Mapping) or value.get("schema") != cls.SCHEMA:
            raise DeclarationError("stage declaration contract drifted")
        return cls(
            stage=value["stage"],
            fields=tuple(
                DeclarationField.from_dict(item) for item in value["fields"]
            ),
            tolerance_ratio=float(value["tolerance_ratio"]),
        )


def _derived_measures(proposal) -> dict[str, float]:
    """Deterministic massing measures from an accepted spatial proposal."""

    payload = proposal.to_dict() if hasattr(proposal, "to_dict") else proposal
    volumes = payload.get("volumes", ())
    if not volumes:
        raise DeclarationError("proposal carries no massing volumes")
    x0 = min(v["bounds"]["minimum"][0] for v in volumes)
    x1 = max(v["bounds"]["maximum"][0] for v in volumes)
    y0 = min(v["bounds"]["minimum"][1] for v in volumes)
    y1 = max(v["bounds"]["maximum"][1] for v in volumes)
    z0 = min(v["bounds"]["minimum"][2] for v in volumes)
    z1 = max(v["bounds"]["maximum"][2] for v in volumes)
    span = max(
        max(
            v["bounds"]["maximum"][0] - v["bounds"]["minimum"][0],
            v["bounds"]["maximum"][2] - v["bounds"]["minimum"][2],
        )
        for v in volumes
    )
    covered: set[tuple[int, int]] = set()
    for volume in volumes:
        lo, hi = volume["bounds"]["minimum"], volume["bounds"]["maximum"]
        for x in range(int(lo[0]), int(hi[0])):
            for z in range(int(lo[2]), int(hi[2])):
                covered.add((x, z))
    grid = payload.get("grid_basis", {})
    cell_area = float(grid.get("horizontal_area_per_cell", 1.0))
    footprint_area = len(payload.get("footprint_cells", ())) * cell_area
    fill = (len(covered) / footprint_area) if footprint_area else 0.0
    return {
        GeometryCheck.FOOTPRINT_WIDTH.value: float(x1 - x0),
        GeometryCheck.FOOTPRINT_DEPTH.value: float(z1 - z0),
        GeometryCheck.OVERALL_HEIGHT.value: float(y1 - y0),
        GeometryCheck.PRIMARY_SPAN.value: float(span),
        GeometryCheck.FOOTPRINT_FILL_RATIO.value: round(fill, 6),
    }


def validate_stage_declarations(
    contract: StageDeclarationContract,
    declarations: Mapping[str, object],
    proposal,
) -> dict[str, float]:
    """Fail closed unless the declaration set passes the stage gate.

    Returns the derived geometry measures so callers can retain them as
    evidence beside the declared values.
    """

    if not isinstance(contract, StageDeclarationContract):
        raise TypeError("contract must be StageDeclarationContract")
    if not isinstance(declarations, Mapping):
        raise DeclarationError("stage_declarations must be a mapping")
    expected = {field.field_id for field in contract.fields}
    supplied = set(declarations)
    missing = sorted(expected - supplied)
    extra = sorted(supplied - expected)
    if missing or extra:
        raise DeclarationError(
            f"declaration set drifted; missing={missing}, extra={extra}"
        )
    derived = _derived_measures(proposal)
    for field in contract.fields:
        raw = declarations[field.field_id]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise DeclarationError(
                f"{field.field_id}: declared value must be numeric"
            )
        value = float(raw)
        if field.kind is DeclarationKind.COUNT and value != int(value):
            raise DeclarationError(
                f"{field.field_id}: count declarations must be integers"
            )
        if not (field.minimum <= value <= field.maximum):
            raise DeclarationError(
                f"{field.field_id}: declared {value} is outside the "
                f"authorized range [{field.minimum}, {field.maximum}]"
            )
        if field.geometry_check is GeometryCheck.NONE:
            continue
        measured = derived[field.geometry_check.value]
        allowance = max(
            abs(value) * contract.tolerance_ratio,
            contract.tolerance_ratio,
        )
        if abs(measured - value) > allowance:
            raise DeclarationError(
                f"{field.field_id}: declared {value} but the authored "
                f"massing measures {round(measured, 3)} "
                f"({field.geometry_check.value}); the declaration does not "
                "hold the geometry"
            )
    return derived


def compile_declaration_commitments(
    contract: StageDeclarationContract,
    declarations: Mapping[str, object],
    *,
    quadrants,
    gate_receipt_ref: str,
    authority_id: str,
    authorized_by: str,
    source_event_ref: str,
    criterion_provider_id: str,
):
    """Compile gate-passed declarations of the named quadrants into HARD
    commitments.

    Runs only after ``validate_stage_declarations`` has passed the gate;
    the caller names which quadrants are irreversible at this gate — the
    framework holds no stage default. Each commitment retains the gate
    receipt and the field's range provenance as evidence, and its
    satisfaction criterion is the field itself, so reopening a declared
    value later requires an authority-gated commitment transition.
    """

    from archflow.state.commitments import Commitment, CommitmentKind, CommitmentStatus, CommitmentStrength, CriterionRef

    if not isinstance(contract, StageDeclarationContract):
        raise TypeError("contract must be StageDeclarationContract")
    selected = {DeclarationQuadrant(item) for item in quadrants}
    if not selected:
        raise DeclarationError("at least one quadrant required")
    commitments = []
    for field in contract.fields:
        if field.quadrant not in selected:
            continue
        if field.field_id not in declarations:
            raise DeclarationError(
                f"{field.field_id}: cannot commit an undeclared value"
            )
        value = float(declarations[field.field_id])
        if not (field.minimum <= value <= field.maximum):
            raise DeclarationError(
                f"{field.field_id}: cannot commit an out-of-range value"
            )
        unit = f" {field.unit}" if field.unit else ""
        commitments.append(
            Commitment(
                commitment_id=f"declared-{field.field_id}",
                kind=CommitmentKind.MAINTENANCE,
                strength=CommitmentStrength.HARD,
                status=CommitmentStatus.ACTIVE,
                authority_id=authority_id,
                authorized_by=authorized_by,
                source_event_ref=source_event_ref,
                satisfaction_criterion=CriterionRef(
                    criterion_id=field.field_id,
                    provider_id=criterion_provider_id,
                ),
                evidence_refs=tuple(
                    sorted({gate_receipt_ref, *field.source_refs})
                ),
            )
        )
    return tuple(commitments)


def select_decision_basis(
    contract: StageDeclarationContract,
    decision_shards: Mapping[str, Mapping[str, object]],
) -> tuple[dict[str, list], dict[str, int]]:
    """Select exactly the basis shards for this contract's fields.

    ``decision_shards`` is the derived basis index's decision mapping
    (decision ref -> shard). The selection injects only the adopted
    facts whose decision ref matches ``declaration:<field_id>`` for a
    contract field — nothing else reaches the prompt — and reports the
    bounding metrics (facts and characters, selected versus store-wide)
    so the saving is measurable, not asserted.
    """

    import json as _json

    if not isinstance(contract, StageDeclarationContract):
        raise TypeError("contract must be StageDeclarationContract")
    keep = ("fact_id", "statement", "strength", "quote", "snapshot_ref")
    payload: dict[str, list] = {}
    facts_selected = 0
    for field in contract.fields:
        ref = f"declaration:{field.field_id}"
        shard = decision_shards.get(ref)
        if not isinstance(shard, Mapping):
            continue
        facts = shard.get("facts") or ()
        if not facts:
            continue
        payload[ref] = [
            {key: fact.get(key) for key in keep} for fact in facts
        ]
        facts_selected += len(facts)
    facts_total = sum(
        len(shard.get("facts") or ())
        for shard in decision_shards.values()
        if isinstance(shard, Mapping)
    )
    metrics = {
        "facts_selected": facts_selected,
        "facts_total": facts_total,
        "chars_selected": len(_json.dumps(payload, sort_keys=True)),
        "chars_total": len(
            _json.dumps(
                {
                    ref: shard
                    for ref, shard in sorted(decision_shards.items())
                },
                sort_keys=True,
                default=str,
            )
        ),
    }
    return payload, metrics
