"""Exact, persistence-neutral bindings from relations to realized geometry.

The architectural relation graph states semantic intent.  This module records
how every relation endpoint is bound to a compiled geometry object and to the
exact object recovered by CAD readback.  Pairings and paths are caller-
supplied manifests: no Cartesian product, proximity search, or operation-input
inference is performed here.

The contracts carry no design, mutation, stage-acceptance, commit, persistence,
or canonical-write authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from archive.archflow.contracts.branch import (
    branch_ref_from_dict,
    branch_ref_to_dict,
    require_exact_branch,
)
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import exact_mapping, identifier, logical_ref
from archflow.project.refs import BranchRef
from archflow.relations.contracts import (
    ArchitecturalRelationGraph,
    RelationParticipant,
)


_MAX_ITEMS = 4_096
RELATION_REALIZATION_CHECKER_ID = "architectural-relation-realization-checker"
_AUTHORITY_FIELDS = {
    "design_authority": False,
    "geometry_mutation_authority": False,
    "stage_acceptance_authority": False,
    "commit_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}


class RelationRealizationError(ValueError):
    """A relation-realization binding is malformed or identity-ambiguous."""


class RelationRealizationPurpose(StrEnum):
    """Independent verification purpose; purposes are never interchangeable."""

    GENERIC_PAIR = "generic_pair"
    WALKING_PATH = "walking_path"
    LOAD_PATH = "load_path"
    SUPPORT_CHAIN = "support_chain"
    HOST_INTERFACE = "host_interface"
    CONTACT_INTERFACE = "contact_interface"


def _bounded_tuple(
    values: object,
    item_type: type,
    field: str,
) -> tuple[object, ...]:
    if not isinstance(values, tuple) or len(values) > _MAX_ITEMS:
        raise TypeError(f"{field} must be a bounded tuple")
    if any(not isinstance(item, item_type) for item in values):
        raise TypeError(f"{field} contains an invalid item")
    return values


def _ordered_refs(
    values: object,
    field: str,
    *,
    minimum: int,
) -> tuple[str, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) < minimum
        or len(values) > _MAX_ITEMS
    ):
        raise RelationRealizationError(
            f"{field} must contain {minimum}..{_MAX_ITEMS} refs"
        )
    normalized = tuple(logical_ref(item, field) for item in values)
    if len(normalized) != len(set(normalized)):
        raise RelationRealizationError(f"{field} contains duplicates")
    return normalized


def _slot_ref(
    *,
    relation_ref: str,
    participant_digest: str,
    role: str,
    node_ref: str,
    ordinal: int | None,
) -> str:
    digest = canonical_digest(
        {
            "schema": "RelationEndpointSlot@1",
            "relation_ref": relation_ref,
            "participant_digest": participant_digest,
            "role": role,
            "node_ref": node_ref,
            "ordinal": ordinal,
        }
    )
    return f"relation-endpoint-slot:{digest}"


def relation_endpoint_slot_ref(
    relation_ref: str,
    participant: RelationParticipant,
) -> str:
    """Return the exact denominator ref for one relation participant."""

    relation_ref = logical_ref(relation_ref, "relation_ref")
    if not isinstance(participant, RelationParticipant):
        raise TypeError("participant must be RelationParticipant")
    return _slot_ref(
        relation_ref=relation_ref,
        participant_digest=participant.participant_digest,
        role=participant.role,
        node_ref=participant.node_ref,
        ordinal=participant.ordinal,
    )


@dataclass(frozen=True, slots=True)
class RelationEndpointObjectBinding:
    """One semantic relation endpoint bound to one exact realized object.

    A participant may intentionally own more than one binding, but every object
    remains a separate record so later pairings cannot silently expand the
    endpoint sets.
    """

    binding_id: str
    relation_ref: str
    participant_digest: str
    role: str
    node_ref: str
    ordinal: int | None
    semantic_binding_id: str
    program_object_id: str
    program_object_digest: str
    producer_operation_id: str
    readback_object_ref: str
    readback_operation_ref: str

    SCHEMA: ClassVar[str] = "RelationEndpointObjectBinding@1"

    def __post_init__(self) -> None:
        identifier(self.binding_id, "relation endpoint binding_id")
        object.__setattr__(
            self,
            "relation_ref",
            logical_ref(self.relation_ref, "relation endpoint relation_ref"),
        )
        object.__setattr__(
            self,
            "participant_digest",
            require_sha256(
                self.participant_digest,
                "relation endpoint participant_digest",
            ),
        )
        identifier(self.role, "relation endpoint role")
        object.__setattr__(
            self,
            "node_ref",
            logical_ref(self.node_ref, "relation endpoint node_ref"),
        )
        if self.ordinal is not None and (
            not isinstance(self.ordinal, int)
            or isinstance(self.ordinal, bool)
            or self.ordinal < 0
            or self.ordinal >= _MAX_ITEMS
        ):
            raise RelationRealizationError(
                "relation endpoint ordinal must be inside 0..4095"
            )
        identifier(
            self.semantic_binding_id,
            "relation endpoint semantic_binding_id",
        )
        identifier(
            self.program_object_id,
            "relation endpoint program_object_id",
        )
        object.__setattr__(
            self,
            "program_object_digest",
            require_sha256(
                self.program_object_digest,
                "relation endpoint program_object_digest",
            ),
        )
        identifier(
            self.producer_operation_id,
            "relation endpoint producer_operation_id",
        )
        object.__setattr__(
            self,
            "readback_object_ref",
            logical_ref(
                self.readback_object_ref,
                "relation endpoint readback_object_ref",
            ),
        )
        object.__setattr__(
            self,
            "readback_operation_ref",
            logical_ref(
                self.readback_operation_ref,
                "relation endpoint readback_operation_ref",
            ),
        )

    @property
    def participant_slot_ref(self) -> str:
        return _slot_ref(
            relation_ref=self.relation_ref,
            participant_digest=self.participant_digest,
            role=self.role,
            node_ref=self.node_ref,
            ordinal=self.ordinal,
        )

    @property
    def ref(self) -> str:
        return (
            f"relation-endpoint-object:{self.binding_id}:"
            f"{canonical_digest(self.to_dict())[:20]}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "binding_id": self.binding_id,
            "relation_ref": self.relation_ref,
            "participant_digest": self.participant_digest,
            "role": self.role,
            "node_ref": self.node_ref,
            "ordinal": self.ordinal,
            "semantic_binding_id": self.semantic_binding_id,
            "program_object_id": self.program_object_id,
            "program_object_digest": self.program_object_digest,
            "producer_operation_id": self.producer_operation_id,
            "readback_object_ref": self.readback_object_ref,
            "readback_operation_ref": self.readback_operation_ref,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationEndpointObjectBinding":
        payload = exact_mapping(
            value,
            {
                "schema",
                "binding_id",
                "relation_ref",
                "participant_digest",
                "role",
                "node_ref",
                "ordinal",
                "semantic_binding_id",
                "program_object_id",
                "program_object_digest",
                "producer_operation_id",
                "readback_object_ref",
                "readback_operation_ref",
                *_AUTHORITY_FIELDS,
            },
            "relation endpoint object binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationRealizationError(
                "unsupported relation endpoint object binding schema"
            )
        result = cls(
            binding_id=payload["binding_id"],
            relation_ref=payload["relation_ref"],
            participant_digest=payload["participant_digest"],
            role=payload["role"],
            node_ref=payload["node_ref"],
            ordinal=payload["ordinal"],
            semantic_binding_id=payload["semantic_binding_id"],
            program_object_id=payload["program_object_id"],
            program_object_digest=payload["program_object_digest"],
            producer_operation_id=payload["producer_operation_id"],
            readback_object_ref=payload["readback_object_ref"],
            readback_operation_ref=payload["readback_operation_ref"],
        )
        if result.to_dict() != payload:
            raise RelationRealizationError(
                "relation endpoint object binding identity changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class RelationVerificationBinding:
    """Exact no-authority binding to an independent check receipt."""

    purpose: RelationRealizationPurpose
    checker_id: str
    receipt_digest: str
    subject_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "RelationVerificationBinding@1"

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, RelationRealizationPurpose):
            raise TypeError("purpose must be RelationRealizationPurpose")
        identifier(self.checker_id, "relation verification checker_id")
        object.__setattr__(
            self,
            "receipt_digest",
            require_sha256(
                self.receipt_digest,
                "relation verification receipt_digest",
            ),
        )
        refs = _ordered_refs(
            self.subject_refs,
            "relation verification subject_refs",
            minimum=1,
        )
        if refs != tuple(sorted(refs)):
            raise RelationRealizationError(
                "relation verification subject_refs must be deterministic"
            )
        object.__setattr__(self, "subject_refs", refs)

    @property
    def receipt_ref(self) -> str:
        return f"check-receipt:{self.receipt_digest}"

    @property
    def ref(self) -> str:
        return f"relation-verification-binding:{canonical_digest(self.to_dict())}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "purpose": self.purpose.value,
            "checker_id": self.checker_id,
            "receipt_digest": self.receipt_digest,
            "subject_refs": list(self.subject_refs),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationVerificationBinding":
        payload = exact_mapping(
            value,
            {
                "schema",
                "purpose",
                "checker_id",
                "receipt_digest",
                "subject_refs",
                *_AUTHORITY_FIELDS,
            },
            "relation verification binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationRealizationError(
                "unsupported relation verification binding schema"
            )
        if not isinstance(payload["subject_refs"], list):
            raise TypeError("relation verification subject_refs must be a list")
        result = cls(
            purpose=RelationRealizationPurpose(payload["purpose"]),
            checker_id=payload["checker_id"],
            receipt_digest=payload["receipt_digest"],
            subject_refs=tuple(payload["subject_refs"]),
        )
        if result.to_dict() != payload:
            raise RelationRealizationError(
                "relation verification binding identity changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class RelationEndpointPairing:
    """One explicitly selected, ordered pair of endpoint-object bindings."""

    pairing_id: str
    relation_ref: str
    first_binding_ref: str
    second_binding_ref: str
    verification: RelationVerificationBinding | None = None

    SCHEMA: ClassVar[str] = "RelationEndpointPairing@1"

    def __post_init__(self) -> None:
        identifier(self.pairing_id, "relation pairing_id")
        for field in (
            "relation_ref",
            "first_binding_ref",
            "second_binding_ref",
        ):
            object.__setattr__(
                self,
                field,
                logical_ref(getattr(self, field), f"relation pairing {field}"),
            )
        if self.first_binding_ref == self.second_binding_ref:
            raise RelationRealizationError(
                "relation endpoint pairing requires two distinct bindings"
            )
        if self.verification is not None and not isinstance(
            self.verification,
            RelationVerificationBinding,
        ):
            raise TypeError(
                "pairing verification must be RelationVerificationBinding or None"
            )

    @property
    def ref(self) -> str:
        return (
            f"relation-endpoint-pairing:{self.pairing_id}:"
            f"{canonical_digest(self.to_dict())[:20]}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "pairing_id": self.pairing_id,
            "relation_ref": self.relation_ref,
            "first_binding_ref": self.first_binding_ref,
            "second_binding_ref": self.second_binding_ref,
            "verification": (
                None
                if self.verification is None
                else self.verification.to_dict()
            ),
            "automatic_pair_expansion": False,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationEndpointPairing":
        payload = exact_mapping(
            value,
            {
                "schema",
                "pairing_id",
                "relation_ref",
                "first_binding_ref",
                "second_binding_ref",
                "verification",
                "automatic_pair_expansion",
                *_AUTHORITY_FIELDS,
            },
            "relation endpoint pairing",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["automatic_pair_expansion"] is not False
        ):
            raise RelationRealizationError(
                "unsupported or automatically expanded relation pairing"
            )
        result = cls(
            pairing_id=payload["pairing_id"],
            relation_ref=payload["relation_ref"],
            first_binding_ref=payload["first_binding_ref"],
            second_binding_ref=payload["second_binding_ref"],
            verification=(
                None
                if payload["verification"] is None
                else RelationVerificationBinding.from_dict(
                    payload["verification"]
                )
            ),
        )
        if result.to_dict() != payload:
            raise RelationRealizationError(
                "relation endpoint pairing identity changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class RelationObjectPath:
    """One explicit ordered path through endpoint-object pairings."""

    path_id: str
    relation_ref: str
    endpoint_binding_refs: tuple[str, ...]
    pairing_refs: tuple[str, ...]
    purpose: RelationRealizationPurpose
    verification: RelationVerificationBinding | None = None

    SCHEMA: ClassVar[str] = "RelationObjectPath@1"

    def __post_init__(self) -> None:
        identifier(self.path_id, "relation object path_id")
        object.__setattr__(
            self,
            "relation_ref",
            logical_ref(self.relation_ref, "relation object path relation_ref"),
        )
        endpoints = _ordered_refs(
            self.endpoint_binding_refs,
            "relation object path endpoint_binding_refs",
            minimum=2,
        )
        pairings = _ordered_refs(
            self.pairing_refs,
            "relation object path pairing_refs",
            minimum=1,
        )
        if len(pairings) != len(endpoints) - 1:
            raise RelationRealizationError(
                "relation object path requires one explicit pairing per hop"
            )
        object.__setattr__(self, "endpoint_binding_refs", endpoints)
        object.__setattr__(self, "pairing_refs", pairings)
        if not isinstance(self.purpose, RelationRealizationPurpose):
            raise TypeError("path purpose must be RelationRealizationPurpose")
        if self.purpose is RelationRealizationPurpose.GENERIC_PAIR:
            raise RelationRealizationError(
                "an ordered relation path requires a path-specific purpose"
            )
        if self.verification is not None:
            if not isinstance(self.verification, RelationVerificationBinding):
                raise TypeError(
                    "path verification must be RelationVerificationBinding or None"
                )
            if self.verification.purpose is not self.purpose:
                raise RelationRealizationError(
                    "path and verification purposes must be identical"
                )

    @property
    def ref(self) -> str:
        return (
            f"relation-object-path:{self.path_id}:"
            f"{canonical_digest(self.to_dict())[:20]}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "path_id": self.path_id,
            "relation_ref": self.relation_ref,
            "endpoint_binding_refs": list(self.endpoint_binding_refs),
            "pairing_refs": list(self.pairing_refs),
            "purpose": self.purpose.value,
            "verification": (
                None
                if self.verification is None
                else self.verification.to_dict()
            ),
            "automatic_path_expansion": False,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationObjectPath":
        payload = exact_mapping(
            value,
            {
                "schema",
                "path_id",
                "relation_ref",
                "endpoint_binding_refs",
                "pairing_refs",
                "purpose",
                "verification",
                "automatic_path_expansion",
                *_AUTHORITY_FIELDS,
            },
            "relation object path",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["automatic_path_expansion"] is not False
        ):
            raise RelationRealizationError(
                "unsupported or automatically expanded relation object path"
            )
        for field in ("endpoint_binding_refs", "pairing_refs"):
            if not isinstance(payload[field], list):
                raise TypeError(f"relation object path {field} must be a list")
        result = cls(
            path_id=payload["path_id"],
            relation_ref=payload["relation_ref"],
            endpoint_binding_refs=tuple(payload["endpoint_binding_refs"]),
            pairing_refs=tuple(payload["pairing_refs"]),
            purpose=RelationRealizationPurpose(payload["purpose"]),
            verification=(
                None
                if payload["verification"] is None
                else RelationVerificationBinding.from_dict(
                    payload["verification"]
                )
            ),
        )
        if result.to_dict() != payload:
            raise RelationRealizationError(
                "relation object path identity changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class RelationRealizationManifest:
    """Exact graph/program/readback denominator for one stage relation check."""

    manifest_id: str
    branch: BranchRef
    stage_id: str
    scope_digest: str
    relation_graph_digest: str
    program_digest: str
    readback_digest: str
    stage_subject_digest: str
    endpoint_bindings: tuple[RelationEndpointObjectBinding, ...]
    pairings: tuple[RelationEndpointPairing, ...]
    paths: tuple[RelationObjectPath, ...] = ()

    SCHEMA: ClassVar[str] = "RelationRealizationManifest@1"

    def __post_init__(self) -> None:
        identifier(self.manifest_id, "relation realization manifest_id")
        require_exact_branch(self.branch, "relation realization branch")
        identifier(self.stage_id, "relation realization stage_id")
        for field in (
            "scope_digest",
            "relation_graph_digest",
            "program_digest",
            "readback_digest",
            "stage_subject_digest",
        ):
            object.__setattr__(
                self,
                field,
                require_sha256(getattr(self, field), field),
            )

        collections = (
            (
                "endpoint_bindings",
                RelationEndpointObjectBinding,
                "binding_id",
            ),
            ("pairings", RelationEndpointPairing, "pairing_id"),
            ("paths", RelationObjectPath, "path_id"),
        )
        for field, item_type, id_field in collections:
            values = _bounded_tuple(getattr(self, field), item_type, field)
            ordered = tuple(sorted(values, key=lambda item: item.ref))
            identifiers = tuple(getattr(item, id_field) for item in ordered)
            refs = tuple(item.ref for item in ordered)
            if len(identifiers) != len(set(identifiers)):
                raise RelationRealizationError(
                    f"relation realization {field} contains duplicate ids"
                )
            if len(refs) != len(set(refs)):
                raise RelationRealizationError(
                    f"relation realization {field} contains duplicate refs"
                )
            object.__setattr__(self, field, ordered)

    @property
    def graph_ref(self) -> str:
        return f"architectural-relation-graph:{self.relation_graph_digest}"

    @property
    def program_ref(self) -> str:
        return f"compiled-geometry-program:{self.program_digest}"

    @property
    def readback_ref(self) -> str:
        return f"cad-readback-snapshot:{self.readback_digest}"

    @property
    def stage_subject_ref(self) -> str:
        return f"stage-subject-set:{self.stage_subject_digest}"

    @property
    def manifest_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"relation-realization-manifest:{self.manifest_digest}"

    @property
    def check_id(self) -> str:
        return f"relation-realization-{self.manifest_digest[:24]}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "manifest_id": self.manifest_id,
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "scope_digest": self.scope_digest,
            "relation_graph_digest": self.relation_graph_digest,
            "program_digest": self.program_digest,
            "readback_digest": self.readback_digest,
            "stage_subject_digest": self.stage_subject_digest,
            "endpoint_bindings": [
                item.to_dict() for item in self.endpoint_bindings
            ],
            "pairings": [item.to_dict() for item in self.pairings],
            "paths": [item.to_dict() for item in self.paths],
            "automatic_pair_expansion": False,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationRealizationManifest":
        payload = exact_mapping(
            value,
            {
                "schema",
                "manifest_id",
                "branch",
                "stage_id",
                "scope_digest",
                "relation_graph_digest",
                "program_digest",
                "readback_digest",
                "stage_subject_digest",
                "endpoint_bindings",
                "pairings",
                "paths",
                "automatic_pair_expansion",
                *_AUTHORITY_FIELDS,
            },
            "relation realization manifest",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["automatic_pair_expansion"] is not False
        ):
            raise RelationRealizationError(
                "unsupported or automatically expanded realization manifest"
            )
        for field in ("endpoint_bindings", "pairings", "paths"):
            if not isinstance(payload[field], list):
                raise TypeError(
                    f"relation realization manifest {field} must be a list"
                )
        result = cls(
            manifest_id=payload["manifest_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            scope_digest=payload["scope_digest"],
            relation_graph_digest=payload["relation_graph_digest"],
            program_digest=payload["program_digest"],
            readback_digest=payload["readback_digest"],
            stage_subject_digest=payload["stage_subject_digest"],
            endpoint_bindings=tuple(
                RelationEndpointObjectBinding.from_dict(item)
                for item in payload["endpoint_bindings"]
            ),
            pairings=tuple(
                RelationEndpointPairing.from_dict(item)
                for item in payload["pairings"]
            ),
            paths=tuple(
                RelationObjectPath.from_dict(item) for item in payload["paths"]
            ),
        )
        if result.to_dict() != payload:
            raise RelationRealizationError(
                "relation realization manifest identity changed"
            )
        return result


def relation_realization_denominator(
    graph: ArchitecturalRelationGraph,
    manifest: RelationRealizationManifest,
) -> tuple[str, ...]:
    """Return the non-self-shrinking exact denominator for stage closure."""

    if not isinstance(graph, ArchitecturalRelationGraph):
        raise TypeError("graph must be ArchitecturalRelationGraph")
    if not isinstance(manifest, RelationRealizationManifest):
        raise TypeError("manifest must be RelationRealizationManifest")

    refs = {
        graph.ref,
        manifest.ref,
        manifest.graph_ref,
        manifest.program_ref,
        manifest.readback_ref,
        manifest.stage_subject_ref,
        *(relation.ref for relation in graph.relations),
        *(
            relation_endpoint_slot_ref(relation.ref, participant)
            for relation in graph.relations
            for participant in relation.participants
        ),
    }
    for binding in manifest.endpoint_bindings:
        refs.update(
            (
                binding.ref,
                binding.relation_ref,
                binding.participant_slot_ref,
                binding.readback_object_ref,
                binding.readback_operation_ref,
            )
        )
    for pairing in manifest.pairings:
        refs.update(
            (
                pairing.ref,
                pairing.relation_ref,
                pairing.first_binding_ref,
                pairing.second_binding_ref,
            )
        )
        if pairing.verification is not None:
            refs.update(
                (
                    pairing.verification.ref,
                    pairing.verification.receipt_ref,
                    *pairing.verification.subject_refs,
                )
            )
    for path in manifest.paths:
        refs.update(
            (
                path.ref,
                path.relation_ref,
                *path.endpoint_binding_refs,
                *path.pairing_refs,
            )
        )
        if path.verification is not None:
            refs.update(
                (
                    path.verification.ref,
                    path.verification.receipt_ref,
                    *path.verification.subject_refs,
                )
            )
    return tuple(sorted(refs))


__all__ = [
    "RELATION_REALIZATION_CHECKER_ID",
    "RelationEndpointObjectBinding",
    "RelationEndpointPairing",
    "RelationObjectPath",
    "RelationRealizationError",
    "RelationRealizationManifest",
    "RelationRealizationPurpose",
    "RelationVerificationBinding",
    "relation_endpoint_slot_ref",
    "relation_realization_denominator",
]
