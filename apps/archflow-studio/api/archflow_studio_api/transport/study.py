"""Wire contract for evidence-grounded precedent Studies."""

from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from ..application.study import StudyView


IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class StudySourceRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    run_id: str = Field(alias="runId", min_length=1)
    asset_sha256: str = Field(alias="assetSha256", pattern=SHA256_PATTERN)
    revision_ref: str | None = Field(default=None, alias="revisionRef")
    page_index: int = Field(alias="pageIndex", ge=0)


class TraceEvidenceRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    evidence_id: str = Field(alias="evidenceId", pattern=IDENTIFIER_PATTERN)
    kind: Literal["envelope", "mass", "void", "floor_plate"]
    points: list[tuple[float, float]] = Field(min_length=3)
    status: Literal["proposed", "confirmed", "rejected"] = "proposed"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    origin: Literal["machine", "user", "imported"] = "user"


class SaveStudyRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    study_id: str = Field(alias="studyId", pattern=IDENTIFIER_PATTERN)
    source: StudySourceRequestDto
    evidence: list[TraceEvidenceRequestDto]
    expected_previous_ref: str | None = Field(default=None, alias="expectedPreviousRef")


class StudyRevisionRequestDto(BaseModel):
    """One exact retained Study revision; comparison never guesses a current head."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    study_id: str = Field(alias="studyId", pattern=IDENTIFIER_PATTERN)
    ledger_ref: str = Field(alias="ledgerRef", min_length=1)


class CompareStudiesRequestDto(BaseModel):
    """A bounded exact-revision comparison inside one bound project."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    studies: list[StudyRevisionRequestDto] = Field(min_length=2, max_length=6)


class StudyViewDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    project_id: str = Field(alias="projectId")
    run_id: str = Field(alias="runId")
    study_id: str = Field(alias="studyId")
    ledger_ref: str = Field(alias="ledgerRef")
    previous_ref: str | None = Field(alias="previousRef")
    source: dict[str, Any]
    evidence: list[dict[str, Any]]
    measurements: list[dict[str, Any]]
    relations: list[dict[str, Any]]
    composition_graph: dict[str, Any] = Field(alias="compositionGraph")
    hypotheses: list[dict[str, Any]]
    counterfactuals: list[dict[str, Any]]
    # Which method produced the retained measurements, relations, hypotheses and
    # counterfactuals above. A cold read replays them instead of re-deriving
    # them, so without this a consumer would read an old revision's archived
    # findings as though the current method had just validated them. Null means
    # a revision retained before the method was stamped: still inspectable,
    # still not a current-method result.
    derivation_method: str | None = Field(alias="derivationMethod")
    canonical_state_changed: bool = Field(alias="canonicalStateChanged")


class StudyComparisonDto(BaseModel):
    """Read-only comparison projection; it is neither a Study ledger nor design truth."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    schema_: Literal["StudyComparison@1"] = Field(alias="schema")
    project_id: str = Field(alias="projectId")
    method: Literal["aspect-correct-composition-compare@1"]
    metric_frame: Literal["page-aspect-correct-long-edge@1"] = Field(alias="metricFrame")
    studies: list[dict[str, Any]]
    shared_topology: list[dict[str, Any]] = Field(alias="sharedTopology")
    pairwise: list[dict[str, Any]]
    canonical_state_changed: bool = Field(alias="canonicalStateChanged")


def _camel_key(key: str) -> str:
    head, *tail = key.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _camelize(value: Any) -> Any:
    if isinstance(value, dict):
        return {_camel_key(str(key)): _camelize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    return value


def study_dto(view: StudyView) -> StudyViewDto:
    payload = view.payload
    return StudyViewDto.model_validate({
        "projectId": payload["project_id"],
        "runId": payload["run_id"],
        "studyId": payload["study_id"],
        "ledgerRef": view.ref.uri,
        "previousRef": payload["previous_ref"],
        "source": _camelize(payload["source"]),
        "evidence": _camelize(payload["evidence"]),
        "measurements": _camelize(payload["measurements"]),
        "relations": _camelize(payload["relations"]),
        "compositionGraph": _camelize(view.composition_graph),
        "hypotheses": _camelize(payload["hypotheses"]),
        "counterfactuals": _camelize(payload["counterfactuals"]),
        "derivationMethod": payload.get("derivation_method"),
        "canonicalStateChanged": payload["canonical_state_changed"],
    })


def study_comparison_dto(comparison: Mapping[str, Any]) -> StudyComparisonDto:
    """Camel-case a deterministic comparison without changing its evidence."""

    return StudyComparisonDto.model_validate(_camelize(dict(comparison)))
