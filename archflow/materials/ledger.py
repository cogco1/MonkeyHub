"""Component-level material intent with mandatory provenance.

Material "correctness" in this framework means the assignment and its
sources are auditable — never that a voxel looks like stone. A
``MaterialIntent`` names one material with non-empty source refs
(adopted facts, evidence records); a ``MaterialLedger`` assigns intents
to components; ``ledger_coverage`` joins the ledger against the
program's semantic bindings and reports every unassigned component as a
typed entry. The framework ships no material vocabulary, no default
palette, and no assignment of its own.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from archflow.contracts.fields import exact_mapping

_ID = re.compile(r"^[a-z0-9][a-z0-9\-]{0,80}$")


class MaterialError(ValueError):
    """A material intent or ledger is invalid."""


def material_display_color(material_id: str) -> tuple[int, int, int]:
    """Deterministic presentation color for a material id."""

    digest = hashlib.sha256(material_id.encode("utf-8")).digest()
    return (
        70 + digest[3] % 150,
        70 + digest[4] % 150,
        70 + digest[5] % 150,
    )


@dataclass(frozen=True, slots=True)
class MaterialIntent:
    """One material with its provenance; display color is presentation."""

    material_id: str
    label: str
    source_refs: tuple[str, ...]
    display_color: tuple[int, int, int] | None = None

    SCHEMA = "MaterialIntent@1"

    def __post_init__(self) -> None:
        if not _ID.match(self.material_id):
            raise MaterialError("material_id must be a kebab identifier")
        if not isinstance(self.label, str) or not self.label.strip():
            raise MaterialError("label must be non-empty text")
        if not isinstance(self.source_refs, tuple) or not self.source_refs:
            raise MaterialError(
                f"{self.material_id}: material provenance source_refs "
                "required"
            )
        if self.display_color is not None and (
            not isinstance(self.display_color, tuple)
            or len(self.display_color) != 3
            or any(
                not isinstance(v, int) or not 0 <= v <= 255
                for v in self.display_color
            )
        ):
            raise MaterialError("display_color must be an RGB triple")

    @property
    def color(self) -> tuple[int, int, int]:
        return self.display_color or material_display_color(
            self.material_id
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "material_id": self.material_id,
            "label": self.label,
            "source_refs": list(self.source_refs),
            "display_color": (
                list(self.display_color) if self.display_color else None
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> "MaterialIntent":
        payload = exact_mapping(
            value,
            {
                "schema",
                "material_id",
                "label",
                "source_refs",
                "display_color",
            },
            "material intent",
        )
        if payload["schema"] != cls.SCHEMA:
            raise MaterialError("unsupported material intent schema")
        if not isinstance(payload["source_refs"], list):
            raise TypeError("source_refs must be a list")
        raw_color = payload["display_color"]
        if raw_color is not None and not isinstance(raw_color, list):
            raise TypeError("display_color must be a list or null")
        result = cls(
            material_id=payload["material_id"],
            label=payload["label"],
            source_refs=tuple(payload["source_refs"]),
            display_color=(
                None if raw_color is None else tuple(raw_color)
            ),
        )
        if result.to_dict() != payload:
            raise MaterialError("material intent is not canonical")
        return result


@dataclass(frozen=True, slots=True)
class MaterialLedger:
    """Component-to-material assignments over declared intents."""

    intents: tuple[MaterialIntent, ...]
    assignments: tuple[tuple[str, str], ...]

    SCHEMA = "MaterialLedger@1"

    def __post_init__(self) -> None:
        if not isinstance(self.intents, tuple) or not self.intents:
            raise MaterialError("ledger requires at least one intent")
        ids = [intent.material_id for intent in self.intents]
        if ids != sorted(set(ids)):
            raise MaterialError("intent ids must be sorted and unique")
        known = set(ids)
        if not isinstance(self.assignments, tuple):
            raise MaterialError("assignments must be a tuple")
        components = [component for component, _ in self.assignments]
        if components != sorted(set(components)):
            raise MaterialError(
                "assignment components must be sorted and unique"
            )
        for component, material_id in self.assignments:
            if not _ID.match(component):
                raise MaterialError(
                    f"assignment component {component!r} is not an id"
                )
            if material_id not in known:
                raise MaterialError(
                    f"{component}: assigned material {material_id!r} is "
                    "not declared"
                )

    def material_of(self, component_id: str) -> str | None:
        for component, material_id in self.assignments:
            if component == component_id:
                return material_id
        return None

    def intent_of(self, material_id: str) -> MaterialIntent:
        for intent in self.intents:
            if intent.material_id == material_id:
                return intent
        raise MaterialError(f"unknown material {material_id!r}")

    def component_map(self) -> dict[str, str]:
        return dict(self.assignments)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "intents": [intent.to_dict() for intent in self.intents],
            "assignments": [
                {"component_id": component, "material_id": material_id}
                for component, material_id in self.assignments
            ],
            "material_vocabulary_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "MaterialLedger":
        payload = exact_mapping(
            value,
            {
                "schema",
                "intents",
                "assignments",
                "material_vocabulary_authority",
                "canonical_write_authority",
            },
            "material ledger",
        )
        if payload["schema"] != cls.SCHEMA:
            raise MaterialError("unsupported material ledger schema")
        if (
            payload["material_vocabulary_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise MaterialError("material ledger authority flags changed")
        if not isinstance(payload["intents"], list):
            raise TypeError("intents must be a list")
        if not isinstance(payload["assignments"], list):
            raise TypeError("assignments must be a list")
        assignments: list[tuple[str, str]] = []
        for item in payload["assignments"]:
            row = exact_mapping(
                item,
                {"component_id", "material_id"},
                "material ledger assignment",
            )
            assignments.append(
                (row["component_id"], row["material_id"])
            )
        result = cls(
            intents=tuple(
                MaterialIntent.from_dict(item)
                for item in payload["intents"]
            ),
            assignments=tuple(assignments),
        )
        if result.to_dict() != payload:
            raise MaterialError("material ledger is not canonical")
        return result


def ledger_coverage(
    bindings: Sequence[Mapping[str, object]],
    ledger: MaterialLedger,
) -> dict[str, object]:
    """Join assignments against the program's components, typed both ways."""

    components = sorted(
        {str(binding["component_id"]) for binding in bindings}
    )
    assigned = {}
    unassigned = []
    for component in components:
        material_id = ledger.material_of(component)
        if material_id is None:
            unassigned.append(component)
        else:
            intent = ledger.intent_of(material_id)
            assigned[component] = {
                "material_id": material_id,
                "source_refs": list(intent.source_refs),
            }
    orphan_assignments = sorted(
        component
        for component, _ in ledger.assignments
        if component not in components
    )
    return {
        "schema": "MaterialLedgerCoverage@1",
        "assigned": assigned,
        "unassigned": unassigned,
        "orphan_assignments": orphan_assignments,
        "coverage_ratio": (
            round(len(assigned) / len(components), 6)
            if components
            else 1.0
        ),
    }


__all__ = [
    "MaterialError",
    "MaterialIntent",
    "MaterialLedger",
    "ledger_coverage",
    "material_display_color",
]
