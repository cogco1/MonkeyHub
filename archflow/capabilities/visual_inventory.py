"""Fail-closed visual evidence inventory contracts.

This module is deliberately a contract compiler, not a computer-vision
provider.  It records exact image identity, bounded perceptual fingerprints,
pixel-space regions, proposed machine observations, and explicit resolution of
every region.  Machine output has no design, dimension, acceptance, physical
identity merge, or canonical-write authority.

Image duplication and physical-component identity are separate domains.  A
near-duplicate image assessment can never merge observations into one physical
component.  A cross-view hypothesis requires an explicit, evidence-backed
architectural locator (facade, level, bay, and zone); otherwise the possible
equivalence remains unresolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Sequence

from archflow.contracts.canonical import canonical_digest, require_sha256


class VisualInventoryError(ValueError):
    """A visual evidence inventory contract is malformed or overclaims."""


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VisualInventoryError(f"{field} must be non-empty text")
    return value.strip()


def _identifier(value: object, field: str) -> str:
    result = _text(value, field)
    if any(character.isspace() for character in result):
        raise VisualInventoryError(f"{field} must not contain whitespace")
    return result


def _refs(value: object, field: str, *, required: bool = False) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    refs = tuple(_text(item, field) for item in value)
    if refs != tuple(sorted(set(refs))):
        raise VisualInventoryError(f"{field} must be sorted and unique")
    if required and not refs:
        raise VisualInventoryError(f"{field} must not be empty")
    return refs


def _exact_mapping(
    value: object,
    *,
    schema: str,
    fields: frozenset[str],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or value.get("schema") != schema:
        raise VisualInventoryError(f"{schema} schema drifted")
    expected = fields | {"schema"}
    supplied = set(value)
    if supplied != expected:
        raise VisualInventoryError(
            f"{schema} fields drifted; missing={sorted(expected - supplied)}, "
            f"extra={sorted(supplied - expected)}"
        )
    return value


def _false(value: object, field: str) -> None:
    if value is not False:
        raise VisualInventoryError(f"{field} must remain false")


class SourceDerivationKind(StrEnum):
    ORIGINAL = "original"
    DERIVED = "derived"


class ROISelection(StrEnum):
    SELECTED = "selected"
    PARKED = "parked"
    REJECTED = "rejected"


class MachineLabelStatus(StrEnum):
    PROPOSED = "proposed"


class VisualEvidenceAspect(StrEnum):
    EXISTENCE = "existence"
    MORPHOLOGY = "morphology"
    TOPOLOGY = "topology"
    RELATIVE_POSITION = "relative_position"


class ImageDuplicateKind(StrEnum):
    EXACT_DUPLICATE = "exact_duplicate"
    NEAR_DUPLICATE = "near_duplicate"
    DISTINCT = "distinct"


class EquivalenceResolution(StrEnum):
    CONFIRMED_IDENTITY = "confirmed_identity"
    UNRESOLVED = "unresolved"


class CoverageOutcome(StrEnum):
    COMPONENT_HYPOTHESIS = "component_hypothesis"
    UNKNOWN_QUESTION = "unknown_question"
    PARKED = "parked"
    REJECTED = "rejected"


class VisualInventoryStatus(StrEnum):
    PASS = "pass"
    BLOCKED = "blocked"


class VisualSourceDispositionKind(StrEnum):
    VISUAL_SOURCES = "visual_sources"
    NO_VISUAL_SOURCES = "no_visual_sources"
    TEXT_ONLY = "text_only"


@dataclass(frozen=True, slots=True)
class PerceptualHash:
    """A bounded fingerprint for image-level near-duplicate assessment."""

    algorithm: str
    bit_length: int
    hex_value: str

    SCHEMA = "PerceptualHash@1"
    MAX_BITS = 256

    def __post_init__(self) -> None:
        _identifier(self.algorithm, "algorithm")
        if (
            isinstance(self.bit_length, bool)
            or not isinstance(self.bit_length, int)
            or self.bit_length < 4
            or self.bit_length > self.MAX_BITS
            or self.bit_length % 4
        ):
            raise VisualInventoryError(
                "bit_length must be a multiple of four inside [4, 256]"
            )
        if (
            not isinstance(self.hex_value, str)
            or len(self.hex_value) != self.bit_length // 4
            or any(character not in "0123456789abcdef" for character in self.hex_value)
        ):
            raise VisualInventoryError(
                "hex_value must be lowercase hexadecimal matching bit_length"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "algorithm": self.algorithm,
            "bit_length": self.bit_length,
            "hex_value": self.hex_value,
        }

    @classmethod
    def from_dict(cls, value: object) -> "PerceptualHash":
        payload = _exact_mapping(
            value,
            schema=cls.SCHEMA,
            fields=frozenset({"algorithm", "bit_length", "hex_value"}),
        )
        return cls(
            algorithm=payload["algorithm"],
            bit_length=payload["bit_length"],
            hex_value=payload["hex_value"],
        )


@dataclass(frozen=True, slots=True)
class SourceImageEvidence:
    image_id: str
    exact_sha256: str
    width_px: int
    height_px: int
    derivation_kind: SourceDerivationKind
    derivation_refs: tuple[str, ...]
    perceptual_hash: PerceptualHash | None = None

    SCHEMA = "SourceImageEvidence@1"

    def __post_init__(self) -> None:
        _identifier(self.image_id, "image_id")
        object.__setattr__(
            self, "exact_sha256", require_sha256(self.exact_sha256, "exact_sha256")
        )
        for field, value in (("width_px", self.width_px), ("height_px", self.height_px)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise VisualInventoryError(f"{field} must be a positive integer")
        if not isinstance(self.derivation_kind, SourceDerivationKind):
            raise TypeError("derivation_kind must be SourceDerivationKind")
        refs = _refs(self.derivation_refs, "derivation_refs")
        if self.derivation_kind is SourceDerivationKind.ORIGINAL and refs:
            raise VisualInventoryError("original images cannot carry derivation_refs")
        if self.derivation_kind is SourceDerivationKind.DERIVED and not refs:
            raise VisualInventoryError("derived images require derivation_refs")
        if self.perceptual_hash is not None and not isinstance(
            self.perceptual_hash, PerceptualHash
        ):
            raise TypeError("perceptual_hash must be PerceptualHash or None")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "image_id": self.image_id,
            "exact_sha256": self.exact_sha256,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "derivation_kind": self.derivation_kind.value,
            "derivation_refs": list(self.derivation_refs),
            "perceptual_hash": (
                None if self.perceptual_hash is None else self.perceptual_hash.to_dict()
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> "SourceImageEvidence":
        payload = _exact_mapping(
            value,
            schema=cls.SCHEMA,
            fields=frozenset(
                {
                    "image_id",
                    "exact_sha256",
                    "width_px",
                    "height_px",
                    "derivation_kind",
                    "derivation_refs",
                    "perceptual_hash",
                }
            ),
        )
        perceptual_hash = payload["perceptual_hash"]
        return cls(
            image_id=payload["image_id"],
            exact_sha256=payload["exact_sha256"],
            width_px=payload["width_px"],
            height_px=payload["height_px"],
            derivation_kind=SourceDerivationKind(payload["derivation_kind"]),
            derivation_refs=tuple(payload["derivation_refs"]),
            perceptual_hash=(
                None
                if perceptual_hash is None
                else PerceptualHash.from_dict(perceptual_hash)
            ),
        )


@dataclass(frozen=True, slots=True)
class ImageDuplicateAssessment:
    """Image-level equality only; never physical-component equivalence."""

    assessment_id: str
    image_ids: tuple[str, str]
    kind: ImageDuplicateKind
    evidence_refs: tuple[str, ...]
    hamming_distance: int | None = None
    near_duplicate_threshold: int | None = None

    SCHEMA = "ImageDuplicateAssessment@1"

    def __post_init__(self) -> None:
        _identifier(self.assessment_id, "assessment_id")
        if (
            not isinstance(self.image_ids, tuple)
            or len(self.image_ids) != 2
            or self.image_ids != tuple(sorted(set(self.image_ids)))
        ):
            raise VisualInventoryError("image_ids must be two sorted unique ids")
        if not isinstance(self.kind, ImageDuplicateKind):
            raise TypeError("kind must be ImageDuplicateKind")
        _refs(self.evidence_refs, "evidence_refs", required=True)
        if self.kind is ImageDuplicateKind.NEAR_DUPLICATE:
            for field, value in (
                ("hamming_distance", self.hamming_distance),
                ("near_duplicate_threshold", self.near_duplicate_threshold),
            ):
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise VisualInventoryError(
                        f"near-duplicate {field} must be a non-negative integer"
                    )
            if self.hamming_distance > self.near_duplicate_threshold:
                raise VisualInventoryError(
                    "near-duplicate distance exceeds its declared threshold"
                )
        elif self.hamming_distance is not None or self.near_duplicate_threshold is not None:
            raise VisualInventoryError(
                "only near-duplicate assessments carry Hamming bounds"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "assessment_id": self.assessment_id,
            "image_ids": list(self.image_ids),
            "kind": self.kind.value,
            "evidence_refs": list(self.evidence_refs),
            "hamming_distance": self.hamming_distance,
            "near_duplicate_threshold": self.near_duplicate_threshold,
            "physical_component_merge_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ImageDuplicateAssessment":
        payload = _exact_mapping(
            value,
            schema=cls.SCHEMA,
            fields=frozenset(
                {
                    "assessment_id",
                    "image_ids",
                    "kind",
                    "evidence_refs",
                    "hamming_distance",
                    "near_duplicate_threshold",
                    "physical_component_merge_authority",
                }
            ),
        )
        _false(
            payload["physical_component_merge_authority"],
            "physical_component_merge_authority",
        )
        return cls(
            assessment_id=payload["assessment_id"],
            image_ids=tuple(payload["image_ids"]),
            kind=ImageDuplicateKind(payload["kind"]),
            evidence_refs=tuple(payload["evidence_refs"]),
            hamming_distance=payload["hamming_distance"],
            near_duplicate_threshold=payload["near_duplicate_threshold"],
        )


@dataclass(frozen=True, slots=True)
class PixelRegion:
    roi_id: str
    image_id: str
    source_width_px: int
    source_height_px: int
    x_px: int
    y_px: int
    width_px: int
    height_px: int
    selection: ROISelection
    reason: str | None = None

    SCHEMA = "PixelRegion@1"

    def __post_init__(self) -> None:
        _identifier(self.roi_id, "roi_id")
        _identifier(self.image_id, "image_id")
        for field, value in (
            ("source_width_px", self.source_width_px),
            ("source_height_px", self.source_height_px),
            ("x_px", self.x_px),
            ("y_px", self.y_px),
            ("width_px", self.width_px),
            ("height_px", self.height_px),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise VisualInventoryError(f"{field} must be an integer")
        if self.source_width_px <= 0 or self.source_height_px <= 0:
            raise VisualInventoryError("source pixel dimensions must be positive")
        if self.x_px < 0 or self.y_px < 0 or self.width_px <= 0 or self.height_px <= 0:
            raise VisualInventoryError("ROI origin must be non-negative and size positive")
        if (
            self.x_px + self.width_px > self.source_width_px
            or self.y_px + self.height_px > self.source_height_px
        ):
            raise VisualInventoryError("ROI must remain inside source pixel dimensions")
        if not isinstance(self.selection, ROISelection):
            raise TypeError("selection must be ROISelection")
        if self.selection is ROISelection.SELECTED:
            if self.reason is not None:
                raise VisualInventoryError("selected ROI cannot carry a disposition reason")
        else:
            _text(self.reason, "reason")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "roi_id": self.roi_id,
            "image_id": self.image_id,
            "source_width_px": self.source_width_px,
            "source_height_px": self.source_height_px,
            "x_px": self.x_px,
            "y_px": self.y_px,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "selection": self.selection.value,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: object) -> "PixelRegion":
        payload = _exact_mapping(
            value,
            schema=cls.SCHEMA,
            fields=frozenset(
                {
                    "roi_id",
                    "image_id",
                    "source_width_px",
                    "source_height_px",
                    "x_px",
                    "y_px",
                    "width_px",
                    "height_px",
                    "selection",
                    "reason",
                }
            ),
        )
        return cls(
            roi_id=payload["roi_id"],
            image_id=payload["image_id"],
            source_width_px=payload["source_width_px"],
            source_height_px=payload["source_height_px"],
            x_px=payload["x_px"],
            y_px=payload["y_px"],
            width_px=payload["width_px"],
            height_px=payload["height_px"],
            selection=ROISelection(payload["selection"]),
            reason=payload["reason"],
        )


@dataclass(frozen=True, slots=True)
class ProposedMachineLabel:
    label: str
    confidence: float
    status: MachineLabelStatus = MachineLabelStatus.PROPOSED

    SCHEMA = "ProposedMachineLabel@1"

    def __post_init__(self) -> None:
        _text(self.label, "label")
        if not isinstance(self.status, MachineLabelStatus):
            raise TypeError("status must be MachineLabelStatus")
        if self.status is not MachineLabelStatus.PROPOSED:
            raise VisualInventoryError("machine labels may only be PROPOSED")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not 0.0 <= float(self.confidence) <= 1.0
        ):
            raise VisualInventoryError("confidence must be inside [0, 1]")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "label": self.label,
            "confidence": float(self.confidence),
            "status": self.status.value,
            "design_authority": False,
            "dimension_authority": False,
            "acceptance_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ProposedMachineLabel":
        payload = _exact_mapping(
            value,
            schema=cls.SCHEMA,
            fields=frozenset(
                {
                    "label",
                    "confidence",
                    "status",
                    "design_authority",
                    "dimension_authority",
                    "acceptance_authority",
                }
            ),
        )
        for field in ("design_authority", "dimension_authority", "acceptance_authority"):
            _false(payload[field], field)
        return cls(
            label=payload["label"],
            confidence=payload["confidence"],
            status=MachineLabelStatus(payload["status"]),
        )


@dataclass(frozen=True, slots=True)
class VisualComponentObservation:
    observation_id: str
    roi_id: str
    proposed_component_kind: str
    evidence_aspects: tuple[VisualEvidenceAspect, ...]
    statement: str
    source_refs: tuple[str, ...]
    machine_labels: tuple[ProposedMachineLabel, ...] = ()

    SCHEMA = "VisualComponentObservation@1"

    def __post_init__(self) -> None:
        _identifier(self.observation_id, "observation_id")
        _identifier(self.roi_id, "roi_id")
        _text(self.proposed_component_kind, "proposed_component_kind")
        if not isinstance(self.evidence_aspects, tuple) or not self.evidence_aspects:
            raise VisualInventoryError("evidence_aspects must be a non-empty tuple")
        if any(
            not isinstance(aspect, VisualEvidenceAspect)
            for aspect in self.evidence_aspects
        ):
            raise TypeError("evidence_aspects must contain VisualEvidenceAspect")
        if self.evidence_aspects != tuple(sorted(set(self.evidence_aspects), key=str)):
            raise VisualInventoryError("evidence_aspects must be sorted and unique")
        _text(self.statement, "statement")
        _refs(self.source_refs, "source_refs", required=True)
        if not isinstance(self.machine_labels, tuple) or any(
            not isinstance(label, ProposedMachineLabel) for label in self.machine_labels
        ):
            raise TypeError("machine_labels must be a tuple of ProposedMachineLabel")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "observation_id": self.observation_id,
            "roi_id": self.roi_id,
            "proposed_component_kind": self.proposed_component_kind,
            "evidence_aspects": [aspect.value for aspect in self.evidence_aspects],
            "statement": self.statement,
            "source_refs": list(self.source_refs),
            "machine_labels": [label.to_dict() for label in self.machine_labels],
            "exact_dimension_authority": False,
            "design_authority": False,
            "acceptance_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VisualComponentObservation":
        payload = _exact_mapping(
            value,
            schema=cls.SCHEMA,
            fields=frozenset(
                {
                    "observation_id",
                    "roi_id",
                    "proposed_component_kind",
                    "evidence_aspects",
                    "statement",
                    "source_refs",
                    "machine_labels",
                    "exact_dimension_authority",
                    "design_authority",
                    "acceptance_authority",
                }
            ),
        )
        for field in (
            "exact_dimension_authority",
            "design_authority",
            "acceptance_authority",
        ):
            _false(payload[field], field)
        return cls(
            observation_id=payload["observation_id"],
            roi_id=payload["roi_id"],
            proposed_component_kind=payload["proposed_component_kind"],
            evidence_aspects=tuple(
                VisualEvidenceAspect(item) for item in payload["evidence_aspects"]
            ),
            statement=payload["statement"],
            source_refs=tuple(payload["source_refs"]),
            machine_labels=tuple(
                ProposedMachineLabel.from_dict(item)
                for item in payload["machine_labels"]
            ),
        )


@dataclass(frozen=True, slots=True)
class PhysicalComponentIdentity:
    facade: str
    level: str
    bay: str
    zone: str

    SCHEMA = "PhysicalComponentIdentity@1"

    def __post_init__(self) -> None:
        for field in ("facade", "level", "bay", "zone"):
            _identifier(getattr(self, field), field)

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, **{field: getattr(self, field) for field in ("facade", "level", "bay", "zone")}}

    @classmethod
    def from_dict(cls, value: object) -> "PhysicalComponentIdentity":
        payload = _exact_mapping(
            value,
            schema=cls.SCHEMA,
            fields=frozenset({"facade", "level", "bay", "zone"}),
        )
        return cls(**{field: payload[field] for field in ("facade", "level", "bay", "zone")})


@dataclass(frozen=True, slots=True)
class PhysicalComponentEquivalence:
    equivalence_id: str
    observation_ids: tuple[str, ...]
    resolution: EquivalenceResolution
    evidence_refs: tuple[str, ...]
    identity: PhysicalComponentIdentity | None = None
    unresolved_question: str | None = None

    SCHEMA = "PhysicalComponentEquivalence@1"

    def __post_init__(self) -> None:
        _identifier(self.equivalence_id, "equivalence_id")
        if (
            not isinstance(self.observation_ids, tuple)
            or len(self.observation_ids) < 2
            or self.observation_ids != tuple(sorted(set(self.observation_ids)))
        ):
            raise VisualInventoryError(
                "equivalence observation_ids must be at least two sorted unique ids"
            )
        if not isinstance(self.resolution, EquivalenceResolution):
            raise TypeError("resolution must be EquivalenceResolution")
        _refs(self.evidence_refs, "evidence_refs", required=True)
        if self.resolution is EquivalenceResolution.CONFIRMED_IDENTITY:
            if not isinstance(self.identity, PhysicalComponentIdentity):
                raise VisualInventoryError(
                    "confirmed cross-view identity requires facade/level/bay/zone"
                )
            if self.unresolved_question is not None:
                raise VisualInventoryError(
                    "confirmed identity cannot carry an unresolved question"
                )
        else:
            if self.identity is not None:
                raise VisualInventoryError(
                    "unresolved equivalence cannot assert physical identity"
                )
            _text(self.unresolved_question, "unresolved_question")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "equivalence_id": self.equivalence_id,
            "observation_ids": list(self.observation_ids),
            "resolution": self.resolution.value,
            "evidence_refs": list(self.evidence_refs),
            "identity": None if self.identity is None else self.identity.to_dict(),
            "unresolved_question": self.unresolved_question,
            "automatic_merge": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "PhysicalComponentEquivalence":
        payload = _exact_mapping(
            value,
            schema=cls.SCHEMA,
            fields=frozenset(
                {
                    "equivalence_id",
                    "observation_ids",
                    "resolution",
                    "evidence_refs",
                    "identity",
                    "unresolved_question",
                    "automatic_merge",
                }
            ),
        )
        _false(payload["automatic_merge"], "automatic_merge")
        identity = payload["identity"]
        return cls(
            equivalence_id=payload["equivalence_id"],
            observation_ids=tuple(payload["observation_ids"]),
            resolution=EquivalenceResolution(payload["resolution"]),
            evidence_refs=tuple(payload["evidence_refs"]),
            identity=(
                None if identity is None else PhysicalComponentIdentity.from_dict(identity)
            ),
            unresolved_question=payload["unresolved_question"],
        )


@dataclass(frozen=True, slots=True)
class ComponentHypothesis:
    hypothesis_id: str
    observation_ids: tuple[str, ...]
    proposed_component_kind: str
    source_refs: tuple[str, ...]
    identity: PhysicalComponentIdentity | None = None
    equivalence_ref: str | None = None

    SCHEMA = "ComponentHypothesis@1"

    def __post_init__(self) -> None:
        _identifier(self.hypothesis_id, "hypothesis_id")
        if (
            not isinstance(self.observation_ids, tuple)
            or not self.observation_ids
            or self.observation_ids != tuple(sorted(set(self.observation_ids)))
        ):
            raise VisualInventoryError("observation_ids must be sorted, unique, and non-empty")
        _text(self.proposed_component_kind, "proposed_component_kind")
        _refs(self.source_refs, "source_refs", required=True)
        if self.identity is not None and not isinstance(
            self.identity, PhysicalComponentIdentity
        ):
            raise TypeError("identity must be PhysicalComponentIdentity or None")
        if self.equivalence_ref is not None:
            _identifier(self.equivalence_ref, "equivalence_ref")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "hypothesis_id": self.hypothesis_id,
            "observation_ids": list(self.observation_ids),
            "proposed_component_kind": self.proposed_component_kind,
            "source_refs": list(self.source_refs),
            "identity": None if self.identity is None else self.identity.to_dict(),
            "equivalence_ref": self.equivalence_ref,
            "acceptance_authority": False,
            "design_authority": False,
            "dimension_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ComponentHypothesis":
        payload = _exact_mapping(
            value,
            schema=cls.SCHEMA,
            fields=frozenset(
                {
                    "hypothesis_id",
                    "observation_ids",
                    "proposed_component_kind",
                    "source_refs",
                    "identity",
                    "equivalence_ref",
                    "acceptance_authority",
                    "design_authority",
                    "dimension_authority",
                }
            ),
        )
        for field in ("acceptance_authority", "design_authority", "dimension_authority"):
            _false(payload[field], field)
        identity = payload["identity"]
        return cls(
            hypothesis_id=payload["hypothesis_id"],
            observation_ids=tuple(payload["observation_ids"]),
            proposed_component_kind=payload["proposed_component_kind"],
            source_refs=tuple(payload["source_refs"]),
            identity=(
                None if identity is None else PhysicalComponentIdentity.from_dict(identity)
            ),
            equivalence_ref=payload["equivalence_ref"],
        )


@dataclass(frozen=True, slots=True)
class UnknownComponentQuestion:
    question_id: str
    roi_id: str
    question: str
    evidence_refs: tuple[str, ...]

    SCHEMA = "UnknownComponentQuestion@1"

    def __post_init__(self) -> None:
        _identifier(self.question_id, "question_id")
        _identifier(self.roi_id, "roi_id")
        _text(self.question, "question")
        _refs(self.evidence_refs, "evidence_refs", required=True)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "question_id": self.question_id,
            "roi_id": self.roi_id,
            "question": self.question,
            "evidence_refs": list(self.evidence_refs),
            "resolution_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "UnknownComponentQuestion":
        payload = _exact_mapping(
            value,
            schema=cls.SCHEMA,
            fields=frozenset(
                {"question_id", "roi_id", "question", "evidence_refs", "resolution_authority"}
            ),
        )
        _false(payload["resolution_authority"], "resolution_authority")
        return cls(
            question_id=payload["question_id"],
            roi_id=payload["roi_id"],
            question=payload["question"],
            evidence_refs=tuple(payload["evidence_refs"]),
        )


@dataclass(frozen=True, slots=True)
class VisualSourceDisposition:
    """Evidence-bound declaration of the project's visual intake mode."""

    kind: VisualSourceDispositionKind
    source_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]

    SCHEMA = "VisualSourceDisposition@1"

    def __post_init__(self) -> None:
        if not isinstance(self.kind, VisualSourceDispositionKind):
            raise TypeError("kind must be VisualSourceDispositionKind")
        _refs(self.source_refs, "source_refs", required=True)
        _refs(self.authority_refs, "authority_refs", required=True)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "kind": self.kind.value,
            "source_refs": list(self.source_refs),
            "authority_refs": list(self.authority_refs),
            "design_authority": False,
            "acceptance_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VisualSourceDisposition":
        payload = _exact_mapping(
            value,
            schema=cls.SCHEMA,
            fields=frozenset(
                {
                    "kind",
                    "source_refs",
                    "authority_refs",
                    "design_authority",
                    "acceptance_authority",
                }
            ),
        )
        _false(payload["design_authority"], "design_authority")
        _false(payload["acceptance_authority"], "acceptance_authority")
        return cls(
            kind=VisualSourceDispositionKind(payload["kind"]),
            source_refs=tuple(payload["source_refs"]),
            authority_refs=tuple(payload["authority_refs"]),
        )


@dataclass(frozen=True, slots=True)
class AcceptedComponentIdentityRef:
    """External acceptance join; this record performs no acceptance itself."""

    hypothesis_id: str
    proposal_component_id: str
    component_identity_ref: str
    acceptance_ref: str
    source_refs: tuple[str, ...]

    SCHEMA = "AcceptedComponentIdentityRef@1"

    def __post_init__(self) -> None:
        for field in (
            "hypothesis_id",
            "proposal_component_id",
            "component_identity_ref",
            "acceptance_ref",
        ):
            _identifier(getattr(self, field), field)
        _refs(self.source_refs, "source_refs", required=True)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "hypothesis_id": self.hypothesis_id,
            "proposal_component_id": self.proposal_component_id,
            "component_identity_ref": self.component_identity_ref,
            "acceptance_ref": self.acceptance_ref,
            "source_refs": list(self.source_refs),
            "acceptance_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "AcceptedComponentIdentityRef":
        payload = _exact_mapping(
            value,
            schema=cls.SCHEMA,
            fields=frozenset(
                {
                    "hypothesis_id",
                    "proposal_component_id",
                    "component_identity_ref",
                    "acceptance_ref",
                    "source_refs",
                    "acceptance_authority",
                    "canonical_write_authority",
                }
            ),
        )
        _false(payload["acceptance_authority"], "acceptance_authority")
        _false(payload["canonical_write_authority"], "canonical_write_authority")
        return cls(
            hypothesis_id=payload["hypothesis_id"],
            proposal_component_id=payload["proposal_component_id"],
            component_identity_ref=payload["component_identity_ref"],
            acceptance_ref=payload["acceptance_ref"],
            source_refs=tuple(payload["source_refs"]),
        )


@dataclass(frozen=True, slots=True)
class ROICoverageEntry:
    roi_id: str
    outcome: CoverageOutcome
    target_ref: str | None = None
    reason: str | None = None

    SCHEMA = "ROICoverageEntry@1"

    def __post_init__(self) -> None:
        _identifier(self.roi_id, "roi_id")
        if not isinstance(self.outcome, CoverageOutcome):
            raise TypeError("outcome must be CoverageOutcome")
        if self.outcome in (
            CoverageOutcome.COMPONENT_HYPOTHESIS,
            CoverageOutcome.UNKNOWN_QUESTION,
        ):
            _identifier(self.target_ref, "target_ref")
            if self.reason is not None:
                raise VisualInventoryError("selected ROI coverage cannot carry reason")
        else:
            if self.target_ref is not None:
                raise VisualInventoryError("parked/rejected coverage cannot carry target_ref")
            _text(self.reason, "reason")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "roi_id": self.roi_id,
            "outcome": self.outcome.value,
            "target_ref": self.target_ref,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ROICoverageEntry":
        payload = _exact_mapping(
            value,
            schema=cls.SCHEMA,
            fields=frozenset({"roi_id", "outcome", "target_ref", "reason"}),
        )
        return cls(
            roi_id=payload["roi_id"],
            outcome=CoverageOutcome(payload["outcome"]),
            target_ref=payload["target_ref"],
            reason=payload["reason"],
        )


def _unique_sorted(items: Sequence[object], field: str, id_field: str) -> None:
    ids = tuple(getattr(item, id_field) for item in items)
    if ids != tuple(sorted(set(ids))):
        raise VisualInventoryError(f"{field} must be sorted by unique {id_field}")


@dataclass(frozen=True, slots=True)
class VisualEvidenceInventoryReceipt:
    """One exhaustive ROI manifest and its evidence graph."""

    source_disposition: VisualSourceDisposition
    source_images: tuple[SourceImageEvidence, ...]
    duplicate_assessments: tuple[ImageDuplicateAssessment, ...]
    rois: tuple[PixelRegion, ...]
    observations: tuple[VisualComponentObservation, ...]
    equivalences: tuple[PhysicalComponentEquivalence, ...]
    component_hypotheses: tuple[ComponentHypothesis, ...]
    unknown_questions: tuple[UnknownComponentQuestion, ...]
    accepted_component_identity_refs: tuple[AcceptedComponentIdentityRef, ...]
    visual_origin_proposal_component_ids: tuple[str, ...]
    coverage_entries: tuple[ROICoverageEntry, ...]
    missing_roi_ids: tuple[str, ...]
    status: VisualInventoryStatus

    SCHEMA = "VisualEvidenceInventoryReceipt@1"

    def __post_init__(self) -> None:
        if not isinstance(self.source_disposition, VisualSourceDisposition):
            raise TypeError("source_disposition must be VisualSourceDisposition")
        typed_groups = (
            (self.source_images, SourceImageEvidence, "source_images", "image_id"),
            (
                self.duplicate_assessments,
                ImageDuplicateAssessment,
                "duplicate_assessments",
                "assessment_id",
            ),
            (self.rois, PixelRegion, "rois", "roi_id"),
            (
                self.observations,
                VisualComponentObservation,
                "observations",
                "observation_id",
            ),
            (
                self.equivalences,
                PhysicalComponentEquivalence,
                "equivalences",
                "equivalence_id",
            ),
            (
                self.component_hypotheses,
                ComponentHypothesis,
                "component_hypotheses",
                "hypothesis_id",
            ),
            (
                self.unknown_questions,
                UnknownComponentQuestion,
                "unknown_questions",
                "question_id",
            ),
            (
                self.accepted_component_identity_refs,
                AcceptedComponentIdentityRef,
                "accepted_component_identity_refs",
                "hypothesis_id",
            ),
        )
        for values, expected, field, id_field in typed_groups:
            if not isinstance(values, tuple) or any(
                not isinstance(item, expected) for item in values
            ):
                raise TypeError(f"{field} must be a tuple of {expected.__name__}")
            _unique_sorted(values, field, id_field)
        if not isinstance(self.coverage_entries, tuple) or any(
            not isinstance(item, ROICoverageEntry) for item in self.coverage_entries
        ):
            raise TypeError("coverage_entries must be a tuple of ROICoverageEntry")
        _unique_sorted(self.coverage_entries, "coverage_entries", "roi_id")
        _refs(
            self.visual_origin_proposal_component_ids,
            "visual_origin_proposal_component_ids",
        )
        _refs(self.missing_roi_ids, "missing_roi_ids")
        if not isinstance(self.status, VisualInventoryStatus):
            raise TypeError("status must be VisualInventoryStatus")
        expected_status = (
            VisualInventoryStatus.BLOCKED
            if self.missing_roi_ids
            else VisualInventoryStatus.PASS
        )
        if self.status is not expected_status:
            raise VisualInventoryError("status does not match missing ROI coverage")

    def _payload_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "source_disposition": self.source_disposition.to_dict(),
            "source_images": [item.to_dict() for item in self.source_images],
            "duplicate_assessments": [
                item.to_dict() for item in self.duplicate_assessments
            ],
            "rois": [item.to_dict() for item in self.rois],
            "observations": [item.to_dict() for item in self.observations],
            "equivalences": [item.to_dict() for item in self.equivalences],
            "component_hypotheses": [
                item.to_dict() for item in self.component_hypotheses
            ],
            "unknown_questions": [
                item.to_dict() for item in self.unknown_questions
            ],
            "accepted_component_identity_refs": [
                item.to_dict() for item in self.accepted_component_identity_refs
            ],
            "visual_origin_proposal_component_ids": list(
                self.visual_origin_proposal_component_ids
            ),
            "coverage_entries": [item.to_dict() for item in self.coverage_entries],
            "missing_roi_ids": list(self.missing_roi_ids),
            "status": self.status.value,
            "machine_design_authority": False,
            "machine_dimension_authority": False,
            "machine_acceptance_authority": False,
            "automatic_physical_merge_authority": False,
            "canonical_write_authority": False,
        }

    @property
    def inventory_digest(self) -> str:
        return canonical_digest(self._payload_dict())

    def to_dict(self) -> dict[str, object]:
        return {**self._payload_dict(), "inventory_digest": self.inventory_digest}

    @classmethod
    def from_dict(cls, value: object) -> "VisualEvidenceInventoryReceipt":
        authority_fields = {
            "machine_design_authority",
            "machine_dimension_authority",
            "machine_acceptance_authority",
            "automatic_physical_merge_authority",
            "canonical_write_authority",
        }
        payload = _exact_mapping(
            value,
            schema=cls.SCHEMA,
            fields=frozenset(
                {
                    "source_disposition",
                    "source_images",
                    "duplicate_assessments",
                    "rois",
                    "observations",
                    "equivalences",
                    "component_hypotheses",
                    "unknown_questions",
                    "accepted_component_identity_refs",
                    "visual_origin_proposal_component_ids",
                    "coverage_entries",
                    "missing_roi_ids",
                    "status",
                    "inventory_digest",
                    *authority_fields,
                }
            ),
        )
        for field in authority_fields:
            _false(payload[field], field)
        receipt = compile_visual_evidence_inventory(
            source_disposition=VisualSourceDisposition.from_dict(
                payload["source_disposition"]
            ),
            source_images=tuple(
                SourceImageEvidence.from_dict(item) for item in payload["source_images"]
            ),
            duplicate_assessments=tuple(
                ImageDuplicateAssessment.from_dict(item)
                for item in payload["duplicate_assessments"]
            ),
            rois=tuple(PixelRegion.from_dict(item) for item in payload["rois"]),
            observations=tuple(
                VisualComponentObservation.from_dict(item)
                for item in payload["observations"]
            ),
            equivalences=tuple(
                PhysicalComponentEquivalence.from_dict(item)
                for item in payload["equivalences"]
            ),
            component_hypotheses=tuple(
                ComponentHypothesis.from_dict(item)
                for item in payload["component_hypotheses"]
            ),
            unknown_questions=tuple(
                UnknownComponentQuestion.from_dict(item)
                for item in payload["unknown_questions"]
            ),
            accepted_component_identity_refs=tuple(
                AcceptedComponentIdentityRef.from_dict(item)
                for item in payload["accepted_component_identity_refs"]
            ),
            visual_origin_proposal_component_ids=tuple(
                payload["visual_origin_proposal_component_ids"]
            ),
            coverage_entries=tuple(
                ROICoverageEntry.from_dict(item)
                for item in payload["coverage_entries"]
            ),
        )
        if tuple(payload["missing_roi_ids"]) != receipt.missing_roi_ids:
            raise VisualInventoryError("missing_roi_ids do not match coverage denominator")
        if VisualInventoryStatus(payload["status"]) is not receipt.status:
            raise VisualInventoryError("status does not match compiled coverage")
        if payload["inventory_digest"] != receipt.inventory_digest:
            raise VisualInventoryError("inventory_digest does not match payload")
        return receipt


def compile_visual_evidence_inventory(
    *,
    source_disposition: VisualSourceDisposition | None,
    source_images: Sequence[SourceImageEvidence],
    duplicate_assessments: Sequence[ImageDuplicateAssessment] = (),
    rois: Sequence[PixelRegion],
    observations: Sequence[VisualComponentObservation] = (),
    equivalences: Sequence[PhysicalComponentEquivalence] = (),
    component_hypotheses: Sequence[ComponentHypothesis] = (),
    unknown_questions: Sequence[UnknownComponentQuestion] = (),
    accepted_component_identity_refs: Sequence[AcceptedComponentIdentityRef] = (),
    visual_origin_proposal_component_ids: Sequence[str] = (),
    coverage_entries: Sequence[ROICoverageEntry] = (),
) -> VisualEvidenceInventoryReceipt:
    """Validate the evidence graph and compile exhaustive ROI coverage.

    Structurally invalid claims raise :class:`VisualInventoryError`.  A valid
    but incomplete coverage submission returns ``BLOCKED`` with the exact
    missing ROI denominator, allowing callers to retain a typed failure receipt.
    """

    if source_disposition is None:
        raise VisualInventoryError("source_disposition must be explicitly declared")
    if not isinstance(source_disposition, VisualSourceDisposition):
        raise TypeError("source_disposition must be VisualSourceDisposition")
    sources = tuple(sorted(source_images, key=lambda item: item.image_id))
    duplicates = tuple(
        sorted(duplicate_assessments, key=lambda item: item.assessment_id)
    )
    regions = tuple(sorted(rois, key=lambda item: item.roi_id))
    observed = tuple(sorted(observations, key=lambda item: item.observation_id))
    equivalence_rows = tuple(
        sorted(equivalences, key=lambda item: item.equivalence_id)
    )
    hypotheses = tuple(
        sorted(component_hypotheses, key=lambda item: item.hypothesis_id)
    )
    questions = tuple(
        sorted(unknown_questions, key=lambda item: item.question_id)
    )
    accepted_refs = tuple(
        sorted(accepted_component_identity_refs, key=lambda item: item.hypothesis_id)
    )
    visual_origin_ids = tuple(sorted(visual_origin_proposal_component_ids))
    coverage = tuple(sorted(coverage_entries, key=lambda item: item.roi_id))

    for values, field, id_field in (
        (sources, "source_images", "image_id"),
        (duplicates, "duplicate_assessments", "assessment_id"),
        (regions, "rois", "roi_id"),
        (observed, "observations", "observation_id"),
        (equivalence_rows, "equivalences", "equivalence_id"),
        (hypotheses, "component_hypotheses", "hypothesis_id"),
        (questions, "unknown_questions", "question_id"),
        (
            accepted_refs,
            "accepted_component_identity_refs",
            "hypothesis_id",
        ),
        (coverage, "coverage_entries", "roi_id"),
    ):
        _unique_sorted(values, field, id_field)
    _refs(visual_origin_ids, "visual_origin_proposal_component_ids")

    visual_payload_present = any(
        (
            sources,
            duplicates,
            regions,
            observed,
            equivalence_rows,
            hypotheses,
            questions,
            accepted_refs,
            visual_origin_ids,
            coverage,
        )
    )
    if source_disposition.kind is VisualSourceDispositionKind.VISUAL_SOURCES:
        if not sources or not regions:
            raise VisualInventoryError(
                "visual_sources disposition requires source images and ROI inventory"
            )
    elif visual_payload_present:
        raise VisualInventoryError(
            "no_visual_sources/text_only disposition cannot carry visual inventory"
        )

    source_by_id = {item.image_id: item for item in sources}
    roi_by_id = {item.roi_id: item for item in regions}
    observation_by_id = {item.observation_id: item for item in observed}
    equivalence_by_id = {
        item.equivalence_id: item for item in equivalence_rows
    }
    hypothesis_by_id = {item.hypothesis_id: item for item in hypotheses}
    question_by_id = {item.question_id: item for item in questions}

    for roi in regions:
        source = source_by_id.get(roi.image_id)
        if source is None:
            raise VisualInventoryError(f"{roi.roi_id}: unknown image_id")
        if (roi.source_width_px, roi.source_height_px) != (
            source.width_px,
            source.height_px,
        ):
            raise VisualInventoryError(
                f"{roi.roi_id}: source dimensions disagree with image evidence"
            )

    for assessment in duplicates:
        try:
            left, right = (source_by_id[item] for item in assessment.image_ids)
        except KeyError as exc:
            raise VisualInventoryError(
                f"{assessment.assessment_id}: unknown image id"
            ) from exc
        if assessment.kind is ImageDuplicateKind.EXACT_DUPLICATE:
            if left.exact_sha256 != right.exact_sha256:
                raise VisualInventoryError(
                    f"{assessment.assessment_id}: exact duplicates require equal SHA-256"
                )
        elif assessment.kind is ImageDuplicateKind.NEAR_DUPLICATE:
            if left.exact_sha256 == right.exact_sha256:
                raise VisualInventoryError(
                    f"{assessment.assessment_id}: equal SHA-256 is exact, not near"
                )
            if left.perceptual_hash is None or right.perceptual_hash is None:
                raise VisualInventoryError(
                    f"{assessment.assessment_id}: near duplicates require perceptual hashes"
                )
            if (
                left.perceptual_hash.algorithm != right.perceptual_hash.algorithm
                or left.perceptual_hash.bit_length != right.perceptual_hash.bit_length
            ):
                raise VisualInventoryError(
                    f"{assessment.assessment_id}: perceptual hashes are incompatible"
                )
            if assessment.near_duplicate_threshold >= left.perceptual_hash.bit_length:
                raise VisualInventoryError(
                    f"{assessment.assessment_id}: threshold must be below hash bit length"
                )
            observed_distance = (
                int(left.perceptual_hash.hex_value, 16)
                ^ int(right.perceptual_hash.hex_value, 16)
            ).bit_count()
            if assessment.hamming_distance != observed_distance:
                raise VisualInventoryError(
                    f"{assessment.assessment_id}: Hamming distance disagrees with hashes"
                )

    assessment_pairs = [item.image_ids for item in duplicates]
    if len(assessment_pairs) != len(set(assessment_pairs)):
        raise VisualInventoryError("each image pair may have only one duplicate assessment")
    exact_assessment_pairs = {
        item.image_ids
        for item in duplicates
        if item.kind is ImageDuplicateKind.EXACT_DUPLICATE
    }
    source_items = tuple(sorted(source_by_id))
    exact_sha_pairs = {
        (left_id, right_id)
        for index, left_id in enumerate(source_items)
        for right_id in source_items[index + 1 :]
        if source_by_id[left_id].exact_sha256 == source_by_id[right_id].exact_sha256
    }
    if exact_sha_pairs != exact_assessment_pairs:
        raise VisualInventoryError(
            "exact SHA duplicate pairs must be exhaustively assessed"
        )

    for observation in observed:
        roi = roi_by_id.get(observation.roi_id)
        if roi is None:
            raise VisualInventoryError(
                f"{observation.observation_id}: unknown roi_id"
            )
        if roi.selection is not ROISelection.SELECTED:
            raise VisualInventoryError(
                f"{observation.observation_id}: observations require SELECTED ROI"
            )

    for equivalence in equivalence_rows:
        unknown = sorted(set(equivalence.observation_ids) - set(observation_by_id))
        if unknown:
            raise VisualInventoryError(
                f"{equivalence.equivalence_id}: unknown observations {unknown}"
            )
        image_ids = {
            roi_by_id[observation_by_id[item].roi_id].image_id
            for item in equivalence.observation_ids
        }
        if len(image_ids) < 2:
            raise VisualInventoryError(
                f"{equivalence.equivalence_id}: cross-view equivalence requires distinct images"
            )

    for hypothesis in hypotheses:
        unknown = sorted(set(hypothesis.observation_ids) - set(observation_by_id))
        if unknown:
            raise VisualInventoryError(
                f"{hypothesis.hypothesis_id}: unknown observations {unknown}"
            )
        kinds = {
            observation_by_id[item].proposed_component_kind
            for item in hypothesis.observation_ids
        }
        if kinds != {hypothesis.proposed_component_kind}:
            raise VisualInventoryError(
                f"{hypothesis.hypothesis_id}: observation component kinds disagree"
            )
        image_ids = {
            roi_by_id[observation_by_id[item].roi_id].image_id
            for item in hypothesis.observation_ids
        }
        if len(image_ids) > 1:
            if hypothesis.equivalence_ref is None:
                raise VisualInventoryError(
                    f"{hypothesis.hypothesis_id}: cross-view merge requires equivalence_ref"
                )
            equivalence = equivalence_by_id.get(hypothesis.equivalence_ref)
            if equivalence is None:
                raise VisualInventoryError(
                    f"{hypothesis.hypothesis_id}: unknown equivalence_ref"
                )
            if equivalence.resolution is not EquivalenceResolution.CONFIRMED_IDENTITY:
                raise VisualInventoryError(
                    f"{hypothesis.hypothesis_id}: unresolved equivalence cannot merge"
                )
            if equivalence.observation_ids != hypothesis.observation_ids:
                raise VisualInventoryError(
                    f"{hypothesis.hypothesis_id}: equivalence observations disagree"
                )
            if hypothesis.identity != equivalence.identity:
                raise VisualInventoryError(
                    f"{hypothesis.hypothesis_id}: facade/level/bay/zone identity disagrees"
                )
        elif hypothesis.equivalence_ref is not None:
            raise VisualInventoryError(
                f"{hypothesis.hypothesis_id}: single-view hypothesis cannot cite equivalence"
            )

    for question in questions:
        roi = roi_by_id.get(question.roi_id)
        if roi is None or roi.selection is not ROISelection.SELECTED:
            raise VisualInventoryError(
                f"{question.question_id}: unknown question requires SELECTED ROI"
            )

    accepted_proposal_ids: set[str] = set()
    for accepted in accepted_refs:
        if accepted.hypothesis_id not in hypothesis_by_id:
            raise VisualInventoryError(
                f"{accepted.hypothesis_id}: accepted ref has no component hypothesis"
            )
        if accepted.proposal_component_id in accepted_proposal_ids:
            raise VisualInventoryError(
                f"{accepted.proposal_component_id}: proposal component maps to multiple hypotheses"
            )
        accepted_proposal_ids.add(accepted.proposal_component_id)
    if accepted_proposal_ids != set(visual_origin_ids):
        raise VisualInventoryError(
            "visual-origin proposal components must exactly reverse-map to accepted hypotheses"
        )

    known_roi_ids = set(roi_by_id)
    coverage_roi_ids = {item.roi_id for item in coverage}
    unknown_coverage = sorted(coverage_roi_ids - known_roi_ids)
    if unknown_coverage:
        raise VisualInventoryError(
            f"coverage contains unknown ROI ids {unknown_coverage}"
        )
    for entry in coverage:
        roi = roi_by_id[entry.roi_id]
        expected_outcome = {
            ROISelection.PARKED: CoverageOutcome.PARKED,
            ROISelection.REJECTED: CoverageOutcome.REJECTED,
        }.get(roi.selection)
        if expected_outcome is not None:
            if entry.outcome is not expected_outcome or entry.reason != roi.reason:
                raise VisualInventoryError(
                    f"{entry.roi_id}: disposition coverage must preserve {roi.selection.value} reason"
                )
            continue
        if entry.outcome is CoverageOutcome.COMPONENT_HYPOTHESIS:
            hypothesis = hypothesis_by_id.get(entry.target_ref)
            if hypothesis is None:
                raise VisualInventoryError(
                    f"{entry.roi_id}: unknown component hypothesis"
                )
            hypothesis_roi_ids = {
                observation_by_id[item].roi_id for item in hypothesis.observation_ids
            }
            if entry.roi_id not in hypothesis_roi_ids:
                raise VisualInventoryError(
                    f"{entry.roi_id}: hypothesis does not contain an observation for ROI"
                )
        elif entry.outcome is CoverageOutcome.UNKNOWN_QUESTION:
            question = question_by_id.get(entry.target_ref)
            if question is None or question.roi_id != entry.roi_id:
                raise VisualInventoryError(
                    f"{entry.roi_id}: unknown-question resolution does not match ROI"
                )
        else:
            raise VisualInventoryError(
                f"{entry.roi_id}: SELECTED ROI must map to hypothesis or unknown question"
            )

    missing = tuple(sorted(known_roi_ids - coverage_roi_ids))
    return VisualEvidenceInventoryReceipt(
        source_disposition=source_disposition,
        source_images=sources,
        duplicate_assessments=duplicates,
        rois=regions,
        observations=observed,
        equivalences=equivalence_rows,
        component_hypotheses=hypotheses,
        unknown_questions=questions,
        accepted_component_identity_refs=accepted_refs,
        visual_origin_proposal_component_ids=visual_origin_ids,
        coverage_entries=coverage,
        missing_roi_ids=missing,
        status=(
            VisualInventoryStatus.BLOCKED if missing else VisualInventoryStatus.PASS
        ),
    )


__all__ = [
    "AcceptedComponentIdentityRef",
    "ComponentHypothesis",
    "CoverageOutcome",
    "EquivalenceResolution",
    "ImageDuplicateAssessment",
    "ImageDuplicateKind",
    "MachineLabelStatus",
    "PerceptualHash",
    "PhysicalComponentEquivalence",
    "PhysicalComponentIdentity",
    "PixelRegion",
    "ProposedMachineLabel",
    "ROICoverageEntry",
    "ROISelection",
    "SourceDerivationKind",
    "SourceImageEvidence",
    "UnknownComponentQuestion",
    "VisualComponentObservation",
    "VisualEvidenceAspect",
    "VisualEvidenceInventoryReceipt",
    "VisualInventoryError",
    "VisualInventoryStatus",
    "VisualSourceDisposition",
    "VisualSourceDispositionKind",
    "compile_visual_evidence_inventory",
]
