"""Candidate convergence and branch-bound research contracts.

The framework does not know that one architectural answer is better than
another.  A project supplies evidence-backed scorecards and a selection
policy.  This module turns those inputs into one of two explicit outcomes:

* ``human_in_the_loop`` waits for an authorised named choice and parks the
  unselected alternatives;
* ``automatic`` selects only when both a minimum score and a minimum margin
  are met, then parks (prunes) the other candidates without deleting them.

After selection, ``BranchResearchScope`` binds every research question to the
exact project base, portfolio, branch revision, decision universe, search
vocabulary, source policy, and context refs.  Facts can only re-enter the
system through a ``BranchPrecedentAdoption`` bound to that same scope.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping
from urllib.parse import urlsplit

from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.research.adoption import PrecedentAdoption
from archflow.project.refs import (
    BranchRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
    require_identifier,
)
from archflow.state.design_portfolio import (
    BranchLifecycle,
    DesignOptionPortfolio,
    PortfolioTransitionKind,
    park_branch,
    select_branch,
)
from archflow.state.operational_state import (
    OperationalMarkovState,
    require_logical_ref,
)
from archflow.state.spatial import SchematicOptionSet


_HEX = frozenset("0123456789abcdef")
_MAX_TEXT = 4_000
_MAX_ITEMS = 4_096


class BranchResearchError(ValueError):
    """A selection or branch-bound research value is invalid or stale."""


class BranchSelectionMode(StrEnum):
    HUMAN_IN_THE_LOOP = "human_in_the_loop"
    AUTOMATIC = "automatic"


class BranchSelectionStatus(StrEnum):
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    SELECTED = "selected"


def _record_sha256(value: Mapping[str, object]) -> str:
    """Match the immutable JSON bytes written by the P036 repository."""

    encoded = json.dumps(
        dict(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256((encoded + "\n").encode("utf-8")).hexdigest()


def _record_ref_dict(ref: ProjectRecordRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _record_ref_from_dict(value: object, field: str) -> ProjectRecordRef:
    payload = _mapping(value, field)
    _exact(
        payload,
        {"project_id", "relative_path", "sha256", "media_type"},
        field,
    )
    return ProjectRecordRef(
        project_id=str(payload["project_id"]),
        relative_path=str(payload["relative_path"]),
        sha256=str(payload["sha256"]),
        media_type=str(payload["media_type"]),
    )


def require_record_payload(
    ref: ProjectRecordRef,
    payload: Mapping[str, object],
    *,
    run: RunRef,
    area_prefix: str,
    field: str,
) -> None:
    """Fail closed unless a P036 ref can name this exact JSON payload."""

    if not isinstance(ref, ProjectRecordRef):
        raise TypeError(f"{field} must be ProjectRecordRef")
    expected_prefix = f"runs/{run.run_id}/{area_prefix.strip('/')}/"
    if (
        ref.project_id != run.project_id
        or ref.media_type != "application/json"
        or not ref.relative_path.startswith(expected_prefix)
        or ref.sha256 != _record_sha256(payload)
    ):
        raise BranchResearchError(f"{field} does not bind the exact P036 record")


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BranchResearchError(f"{field} must be non-empty text")
    if len(value) > _MAX_TEXT:
        raise BranchResearchError(f"{field} exceeds bounded text")
    return value


def _strings(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
    refs: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(values) > _MAX_ITEMS or (not values and not allow_empty):
        raise BranchResearchError(f"{field} has invalid item count")
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise BranchResearchError(f"{field} must contain non-empty text")
    if values != tuple(sorted(set(values))):
        raise BranchResearchError(f"{field} must be sorted and unique")
    if refs:
        for item in values:
            require_logical_ref(item, field)
    return values


def _number(value: object, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise BranchResearchError(f"{field} must be finite")
    if positive and number <= 0:
        raise BranchResearchError(f"{field} must be positive")
    return number


def _unit_interval(value: object, field: str) -> float:
    number = _number(value, field)
    if not 0.0 <= number <= 1.0:
        raise BranchResearchError(f"{field} must be between 0 and 1")
    return number


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return value


def _exact(value: Mapping[str, object], fields: set[str], field: str) -> None:
    if set(value) != fields:
        raise BranchResearchError(f"{field} schema drifted")


def _normalise_domain(value: str) -> str:
    candidate = value.strip().lower().rstrip(".")
    if "://" in candidate:
        host = urlsplit(candidate).hostname
        if host is None:
            raise BranchResearchError(f"invalid source domain: {value!r}")
        candidate = host.rstrip(".")
    if not candidate or "/" in candidate or " " in candidate:
        raise BranchResearchError(f"invalid source domain: {value!r}")
    return candidate


def _domains(values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError("domain_allowlist must be a tuple")
    normalised = tuple(sorted({_normalise_domain(item) for item in values}))
    if tuple(values) != normalised:
        raise BranchResearchError(
            "domain_allowlist must be normalised, sorted, and unique"
        )
    return normalised


@dataclass(frozen=True, slots=True)
class BranchResearchProfile:
    """Project-authored retrieval envelope attached to one Candidate version."""

    branch_id: str
    branch_revision_digest: str
    active_decision_refs: tuple[str, ...]
    branch_search_terms: tuple[str, ...]
    excluded_search_terms: tuple[str, ...]
    domain_allowlist: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "BranchResearchProfile@1"

    def __post_init__(self) -> None:
        require_identifier(self.branch_id, "branch_id")
        require_sha256(self.branch_revision_digest, "branch_revision_digest")
        _strings(
            self.active_decision_refs,
            "profile active_decision_refs",
            refs=True,
        )
        _strings(self.branch_search_terms, "profile branch_search_terms")
        _strings(
            self.excluded_search_terms,
            "profile excluded_search_terms",
            allow_empty=True,
        )
        if set(item.casefold() for item in self.branch_search_terms) & set(
            item.casefold() for item in self.excluded_search_terms
        ):
            raise BranchResearchError(
                "profile included and excluded search terms overlap"
            )
        _domains(self.domain_allowlist)
        if not self.domain_allowlist:
            raise BranchResearchError(
                "branch research profile requires a domain allowlist"
            )
        _strings(self.evidence_refs, "profile evidence_refs", refs=True)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "branch_id": self.branch_id,
            "branch_revision_digest": self.branch_revision_digest,
            "active_decision_refs": list(self.active_decision_refs),
            "branch_search_terms": list(self.branch_search_terms),
            "excluded_search_terms": list(self.excluded_search_terms),
            "domain_allowlist": list(self.domain_allowlist),
            "evidence_refs": list(self.evidence_refs),
            "selection_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "BranchResearchProfile":
        payload = _mapping(value, "branch research profile")
        _exact(
            payload,
            {
                "schema",
                "branch_id",
                "branch_revision_digest",
                "active_decision_refs",
                "branch_search_terms",
                "excluded_search_terms",
                "domain_allowlist",
                "evidence_refs",
                "selection_authority",
                "canonical_write_authority",
            },
            "branch research profile",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["selection_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise BranchResearchError("branch research profile acquired authority")
        fields = (
            "active_decision_refs",
            "branch_search_terms",
            "excluded_search_terms",
            "domain_allowlist",
            "evidence_refs",
        )
        if any(not isinstance(payload[field], list) for field in fields):
            raise TypeError("branch research profile collections must be lists")
        return cls(
            branch_id=str(payload["branch_id"]),
            branch_revision_digest=str(payload["branch_revision_digest"]),
            active_decision_refs=tuple(
                str(item) for item in payload["active_decision_refs"]
            ),
            branch_search_terms=tuple(
                str(item) for item in payload["branch_search_terms"]
            ),
            excluded_search_terms=tuple(
                str(item) for item in payload["excluded_search_terms"]
            ),
            domain_allowlist=tuple(
                str(item) for item in payload["domain_allowlist"]
            ),
            evidence_refs=tuple(str(item) for item in payload["evidence_refs"]),
        )


@dataclass(frozen=True, slots=True)
class BranchScoreCriterion:
    criterion_id: str
    score: float
    weight: float
    evidence_refs: tuple[str, ...]
    rationale: str

    SCHEMA = "BranchScoreCriterion@1"

    def __post_init__(self) -> None:
        require_identifier(self.criterion_id, "criterion_id")
        _unit_interval(self.score, "criterion score")
        _number(self.weight, "criterion weight", positive=True)
        _strings(self.evidence_refs, "criterion evidence_refs", refs=True)
        _text(self.rationale, "criterion rationale")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "criterion_id": self.criterion_id,
            "score": self.score,
            "weight": self.weight,
            "evidence_refs": list(self.evidence_refs),
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, value: object) -> "BranchScoreCriterion":
        payload = _mapping(value, "branch score criterion")
        _exact(
            payload,
            {
                "schema",
                "criterion_id",
                "score",
                "weight",
                "evidence_refs",
                "rationale",
            },
            "branch score criterion",
        )
        if payload["schema"] != cls.SCHEMA:
            raise BranchResearchError("branch score criterion schema changed")
        refs = payload["evidence_refs"]
        if not isinstance(refs, list):
            raise TypeError("criterion evidence_refs must be a list")
        return cls(
            criterion_id=str(payload["criterion_id"]),
            score=float(payload["score"]),
            weight=float(payload["weight"]),
            evidence_refs=tuple(str(item) for item in refs),
            rationale=str(payload["rationale"]),
        )


class HardFeasibilityStatus(StrEnum):
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class BranchHardFeasibilityAssessment:
    """Project-authored hard gate, separate from weighted soft criteria."""

    assessment_id: str
    branch_id: str
    branch_revision_digest: str
    status: HardFeasibilityStatus
    evidence_refs: tuple[str, ...]
    rationale: str

    SCHEMA = "BranchHardFeasibilityAssessment@1"

    def __post_init__(self) -> None:
        require_identifier(self.assessment_id, "assessment_id")
        require_identifier(self.branch_id, "hard assessment branch_id")
        require_sha256(
            self.branch_revision_digest,
            "hard assessment branch_revision_digest",
        )
        if not isinstance(self.status, HardFeasibilityStatus):
            raise TypeError("status must be HardFeasibilityStatus")
        _strings(
            self.evidence_refs,
            "hard assessment evidence_refs",
            refs=True,
        )
        _text(self.rationale, "hard assessment rationale")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "assessment_id": self.assessment_id,
            "branch_id": self.branch_id,
            "branch_revision_digest": self.branch_revision_digest,
            "status": self.status.value,
            "evidence_refs": list(self.evidence_refs),
            "rationale": self.rationale,
            "selection_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "BranchHardFeasibilityAssessment":
        payload = _mapping(value, "hard feasibility assessment")
        _exact(
            payload,
            {
                "schema",
                "assessment_id",
                "branch_id",
                "branch_revision_digest",
                "status",
                "evidence_refs",
                "rationale",
                "selection_authority",
            },
            "hard feasibility assessment",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["selection_authority"] is not False
        ):
            raise BranchResearchError("hard feasibility assessment acquired authority")
        refs = payload["evidence_refs"]
        if not isinstance(refs, list):
            raise TypeError("hard assessment evidence_refs must be a list")
        return cls(
            assessment_id=str(payload["assessment_id"]),
            branch_id=str(payload["branch_id"]),
            branch_revision_digest=str(payload["branch_revision_digest"]),
            status=HardFeasibilityStatus(str(payload["status"])),
            evidence_refs=tuple(str(item) for item in refs),
            rationale=str(payload["rationale"]),
        )


@dataclass(frozen=True, slots=True)
class BranchScorecard:
    branch_id: str
    branch_revision_digest: str
    research_profile: BranchResearchProfile
    criteria: tuple[BranchScoreCriterion, ...]
    rationale: str
    hard_feasibility: BranchHardFeasibilityAssessment | None = None

    SCHEMA = "BranchScorecard@1"

    def __post_init__(self) -> None:
        require_identifier(self.branch_id, "branch_id")
        require_sha256(self.branch_revision_digest, "branch_revision_digest")
        if not isinstance(self.research_profile, BranchResearchProfile):
            raise TypeError("research_profile must be BranchResearchProfile")
        if (
            self.research_profile.branch_id != self.branch_id
            or self.research_profile.branch_revision_digest
            != self.branch_revision_digest
        ):
            raise BranchResearchError(
                "scorecard and research profile identify different Candidates"
            )
        if not isinstance(self.criteria, tuple) or not self.criteria:
            raise BranchResearchError("scorecard requires criteria")
        if any(not isinstance(item, BranchScoreCriterion) for item in self.criteria):
            raise TypeError("scorecard criteria contain an invalid item")
        ids = tuple(item.criterion_id for item in self.criteria)
        if ids != tuple(sorted(set(ids))):
            raise BranchResearchError(
                "scorecard criterion ids must be sorted and unique"
            )
        _text(self.rationale, "scorecard rationale")
        if self.hard_feasibility is not None:
            if not isinstance(
                self.hard_feasibility,
                BranchHardFeasibilityAssessment,
            ):
                raise TypeError(
                    "hard_feasibility must be BranchHardFeasibilityAssessment"
                )
            if (
                self.hard_feasibility.branch_id != self.branch_id
                or self.hard_feasibility.branch_revision_digest
                != self.branch_revision_digest
            ):
                raise BranchResearchError(
                    "hard feasibility assessment identifies another Candidate"
                )

    @property
    def aggregate_score(self) -> float:
        total_weight = sum(item.weight for item in self.criteria)
        weighted = sum(item.score * item.weight for item in self.criteria)
        return round(weighted / total_weight, 12)

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    ref
                    for criterion in self.criteria
                    for ref in criterion.evidence_refs
                }
                | set(self.research_profile.evidence_refs)
                | (
                    set(self.hard_feasibility.evidence_refs)
                    if self.hard_feasibility is not None
                    else set()
                )
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "branch_id": self.branch_id,
            "branch_revision_digest": self.branch_revision_digest,
            "research_profile": self.research_profile.to_dict(),
            "criteria": [item.to_dict() for item in self.criteria],
            "aggregate_score": self.aggregate_score,
            "rationale": self.rationale,
            "hard_feasibility": (
                self.hard_feasibility.to_dict()
                if self.hard_feasibility is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> "BranchScorecard":
        payload = _mapping(value, "branch scorecard")
        expected = {
                "schema",
                "branch_id",
                "branch_revision_digest",
                "research_profile",
                "criteria",
                "aggregate_score",
                "rationale",
            }
        legacy = set(payload) == expected
        if not legacy:
            expected.add("hard_feasibility")
        _exact(payload, expected, "branch scorecard")
        if payload["schema"] != cls.SCHEMA:
            raise BranchResearchError("branch scorecard schema changed")
        criteria = payload["criteria"]
        if not isinstance(criteria, list):
            raise TypeError("scorecard criteria must be a list")
        result = cls(
            branch_id=str(payload["branch_id"]),
            branch_revision_digest=str(payload["branch_revision_digest"]),
            research_profile=BranchResearchProfile.from_dict(
                payload["research_profile"]
            ),
            criteria=tuple(BranchScoreCriterion.from_dict(item) for item in criteria),
            rationale=str(payload["rationale"]),
            hard_feasibility=(
                BranchHardFeasibilityAssessment.from_dict(
                    payload["hard_feasibility"]
                )
                if not legacy and payload["hard_feasibility"] is not None
                else None
            ),
        )
        if payload["aggregate_score"] != result.aggregate_score:
            raise BranchResearchError("scorecard aggregate changed")
        return result


@dataclass(frozen=True, slots=True)
class BranchSelectionRules:
    mode: BranchSelectionMode
    minimum_score: float
    minimum_margin: float
    automatic_authority_id: str | None = None
    require_hard_feasibility: bool = False

    SCHEMA = "BranchSelectionRules@2"
    LEGACY_SCHEMA = "BranchSelectionRules@1"

    def __post_init__(self) -> None:
        if not isinstance(self.mode, BranchSelectionMode):
            raise TypeError("mode must be BranchSelectionMode")
        _unit_interval(self.minimum_score, "minimum_score")
        _unit_interval(self.minimum_margin, "minimum_margin")
        if self.mode is BranchSelectionMode.AUTOMATIC:
            if self.automatic_authority_id is None:
                raise BranchResearchError(
                    "automatic mode requires automatic_authority_id"
                )
            require_identifier(
                self.automatic_authority_id,
                "automatic_authority_id",
            )
        elif self.automatic_authority_id is not None:
            raise BranchResearchError(
                "human-in-the-loop rules cannot carry automatic authority"
            )
        if not isinstance(self.require_hard_feasibility, bool):
            raise TypeError("require_hard_feasibility must be boolean")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "mode": self.mode.value,
            "minimum_score": self.minimum_score,
            "minimum_margin": self.minimum_margin,
            "automatic_authority_id": self.automatic_authority_id,
            "require_hard_feasibility": self.require_hard_feasibility,
        }

    @classmethod
    def from_dict(cls, value: object) -> "BranchSelectionRules":
        payload = _mapping(value, "branch selection rules")
        legacy = payload.get("schema") == cls.LEGACY_SCHEMA
        fields = {
            "schema",
            "mode",
            "minimum_score",
            "minimum_margin",
            "automatic_authority_id",
        }
        if not legacy:
            fields.add("require_hard_feasibility")
        _exact(payload, fields, "branch selection rules")
        if payload["schema"] not in {cls.SCHEMA, cls.LEGACY_SCHEMA}:
            raise BranchResearchError("branch selection rules schema changed")
        authority = payload["automatic_authority_id"]
        return cls(
            mode=BranchSelectionMode(str(payload["mode"])),
            minimum_score=float(payload["minimum_score"]),
            minimum_margin=float(payload["minimum_margin"]),
            automatic_authority_id=(str(authority) if authority is not None else None),
            require_hard_feasibility=(
                False
                if legacy
                else payload["require_hard_feasibility"]
            ),
        )


def _automatic_winner(
    scorecards: tuple[BranchScorecard, ...],
    rules: BranchSelectionRules,
) -> tuple[BranchScorecard | None, float]:
    """Replay the deterministic score gate; a tie always requires HITL."""

    ordered = tuple(
        sorted(
            (
                item
                for item in scorecards
                if _hard_feasible(
                    item,
                    require_assessment=rules.require_hard_feasibility,
                )
            ),
            key=lambda item: (-item.aggregate_score, item.branch_id),
        )
    )
    if not ordered:
        return None, 0.0
    winner = ordered[0]
    margin = (
        winner.aggregate_score - ordered[1].aggregate_score
        if len(ordered) > 1
        else 1.0
    )
    if (
        winner.aggregate_score >= rules.minimum_score
        and margin >= rules.minimum_margin
        and margin > 0.0
    ):
        return winner, margin
    return None, margin


def _hard_feasible(
    scorecard: BranchScorecard,
    *,
    require_assessment: bool = False,
) -> bool:
    assessment = scorecard.hard_feasibility
    return (
        (assessment is None and not require_assessment)
        or (
            assessment is not None
            and assessment.status is HardFeasibilityStatus.SATISFIED
        )
    )


@dataclass(frozen=True, slots=True)
class BranchSelectionDecision:
    decision_id: str
    project_id: str
    run_id: str
    portfolio_id: str
    candidate_portfolio_digest: str
    base_state_sha256: str
    operational_state_digest: str
    rules: BranchSelectionRules
    status: BranchSelectionStatus
    scorecards: tuple[BranchScorecard, ...]
    selected_branch_id: str | None
    pruned_branch_ids: tuple[str, ...]
    parked_branch_ids: tuple[str, ...]
    authority_id: str | None
    decision_ref: str
    evidence_refs: tuple[str, ...]
    rationale: str

    SCHEMA = "BranchSelectionDecision@1"

    def __post_init__(self) -> None:
        require_identifier(self.decision_id, "decision_id")
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        require_identifier(self.portfolio_id, "portfolio_id")
        require_sha256(self.candidate_portfolio_digest, "candidate_portfolio_digest")
        require_sha256(self.base_state_sha256, "base_state_sha256")
        require_sha256(self.operational_state_digest, "operational_state_digest")
        if not isinstance(self.rules, BranchSelectionRules):
            raise TypeError("rules must be BranchSelectionRules")
        if not isinstance(self.status, BranchSelectionStatus):
            raise TypeError("status must be BranchSelectionStatus")
        if not isinstance(self.scorecards, tuple) or len(self.scorecards) < 2:
            raise BranchResearchError("selection decision requires two candidates")
        if any(not isinstance(item, BranchScorecard) for item in self.scorecards):
            raise TypeError("scorecards contain an invalid item")
        branch_ids = tuple(item.branch_id for item in self.scorecards)
        if branch_ids != tuple(sorted(set(branch_ids))):
            raise BranchResearchError("scorecards must use stable branch order")
        _strings(
            self.pruned_branch_ids,
            "pruned_branch_ids",
            allow_empty=True,
        )
        _strings(
            self.parked_branch_ids,
            "parked_branch_ids",
            allow_empty=True,
        )
        require_logical_ref(self.decision_ref, "decision_ref")
        _strings(self.evidence_refs, "decision evidence_refs", refs=True)
        card_evidence = {
            ref
            for card in self.scorecards
            for ref in card.evidence_refs
        }
        if not card_evidence <= set(self.evidence_refs):
            raise BranchResearchError(
                "selection decision dropped scorecard evidence"
            )
        _text(self.rationale, "selection rationale")
        others = set(branch_ids)
        if self.status is BranchSelectionStatus.HUMAN_REVIEW_REQUIRED:
            if any(
                (
                    self.selected_branch_id is not None,
                    self.authority_id is not None,
                    bool(self.pruned_branch_ids),
                    bool(self.parked_branch_ids),
                )
            ):
                raise BranchResearchError(
                    "pending human review cannot change branch lifecycle"
                )
            if self.rules.mode is BranchSelectionMode.AUTOMATIC:
                winner, _ = _automatic_winner(self.scorecards, self.rules)
                if winner is not None:
                    raise BranchResearchError(
                        "automatic decision ignored a passing score gate"
                    )
            return
        if self.selected_branch_id not in others:
            raise BranchResearchError("selected branch is not a scored candidate")
        selected_card = next(
            item
            for item in self.scorecards
            if item.branch_id == self.selected_branch_id
        )
        if not _hard_feasible(
            selected_card,
            require_assessment=self.rules.require_hard_feasibility,
        ):
            raise BranchResearchError(
                "hard feasibility forbids selecting this Candidate"
            )
        if self.authority_id is None:
            raise BranchResearchError("selected decision requires authority_id")
        require_identifier(self.authority_id, "authority_id")
        others.remove(self.selected_branch_id)
        if self.rules.mode is BranchSelectionMode.AUTOMATIC:
            winner, _ = _automatic_winner(self.scorecards, self.rules)
            if (
                winner is None
                or self.selected_branch_id != winner.branch_id
                or self.authority_id != self.rules.automatic_authority_id
            ):
                raise BranchResearchError(
                    "automatic selection does not replay its score gate"
                )
            if set(self.pruned_branch_ids) != others or self.parked_branch_ids:
                raise BranchResearchError(
                    "automatic selection must prune every other candidate"
                )
        elif set(self.parked_branch_ids) != others or self.pruned_branch_ids:
            raise BranchResearchError(
                "human selection must park every other candidate"
            )

    @property
    def decision_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "decision_id": self.decision_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "portfolio_id": self.portfolio_id,
            "candidate_portfolio_digest": self.candidate_portfolio_digest,
            "base_state_sha256": self.base_state_sha256,
            "operational_state_digest": self.operational_state_digest,
            "rules": self.rules.to_dict(),
            "status": self.status.value,
            "scorecards": [item.to_dict() for item in self.scorecards],
            "selected_branch_id": self.selected_branch_id,
            "pruned_branch_ids": list(self.pruned_branch_ids),
            "parked_branch_ids": list(self.parked_branch_ids),
            "authority_id": self.authority_id,
            "decision_ref": self.decision_ref,
            "evidence_refs": list(self.evidence_refs),
            "rationale": self.rationale,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "BranchSelectionDecision":
        payload = _mapping(value, "branch selection decision")
        _exact(
            payload,
            {
                "schema",
                "decision_id",
                "project_id",
                "run_id",
                "portfolio_id",
                "candidate_portfolio_digest",
                "base_state_sha256",
                "operational_state_digest",
                "rules",
                "status",
                "scorecards",
                "selected_branch_id",
                "pruned_branch_ids",
                "parked_branch_ids",
                "authority_id",
                "decision_ref",
                "evidence_refs",
                "rationale",
                "canonical_write_authority",
            },
            "branch selection decision",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["canonical_write_authority"] is not False
        ):
            raise BranchResearchError("branch selection decision acquired authority")
        list_fields = (
            "scorecards",
            "pruned_branch_ids",
            "parked_branch_ids",
            "evidence_refs",
        )
        if any(not isinstance(payload[field], list) for field in list_fields):
            raise TypeError("branch selection decision collections must be lists")
        selected = payload["selected_branch_id"]
        authority = payload["authority_id"]
        return cls(
            decision_id=str(payload["decision_id"]),
            project_id=str(payload["project_id"]),
            run_id=str(payload["run_id"]),
            portfolio_id=str(payload["portfolio_id"]),
            candidate_portfolio_digest=str(payload["candidate_portfolio_digest"]),
            base_state_sha256=str(payload["base_state_sha256"]),
            operational_state_digest=str(payload["operational_state_digest"]),
            rules=BranchSelectionRules.from_dict(payload["rules"]),
            status=BranchSelectionStatus(str(payload["status"])),
            scorecards=tuple(
                BranchScorecard.from_dict(item) for item in payload["scorecards"]
            ),
            selected_branch_id=(str(selected) if selected is not None else None),
            pruned_branch_ids=tuple(str(item) for item in payload["pruned_branch_ids"]),
            parked_branch_ids=tuple(str(item) for item in payload["parked_branch_ids"]),
            authority_id=(str(authority) if authority is not None else None),
            decision_ref=str(payload["decision_ref"]),
            evidence_refs=tuple(str(item) for item in payload["evidence_refs"]),
            rationale=str(payload["rationale"]),
        )


def evaluate_branch_selection(
    portfolio: DesignOptionPortfolio,
    *,
    decision_id: str,
    rules: BranchSelectionRules,
    scorecards: tuple[BranchScorecard, ...],
    decision_ref: str,
    evidence_refs: tuple[str, ...],
    rationale: str,
    human_branch_id: str | None = None,
    human_authority_id: str | None = None,
) -> BranchSelectionDecision:
    """Evaluate candidates without mutating the portfolio.

    Automatic selection fails to human review when the winning score is below
    threshold or its margin is too small.  It never resolves a tie by branch
    registration order.  Human mode never selects without an explicit branch
    and an authority permitted by the existing portfolio policy.
    """

    if not isinstance(portfolio, DesignOptionPortfolio):
        raise TypeError("portfolio must be DesignOptionPortfolio")
    if portfolio.selected_branch is not None:
        raise BranchResearchError("candidate portfolio already has a selection")
    if not isinstance(rules, BranchSelectionRules):
        raise TypeError("rules must be BranchSelectionRules")
    candidates = tuple(
        branch
        for branch in portfolio.branches
        if branch.lifecycle is BranchLifecycle.ACTIVE
    )
    candidate_ids = tuple(branch.branch_id for branch in candidates)
    card_ids = tuple(item.branch_id for item in scorecards)
    if card_ids != tuple(sorted(candidate_ids)):
        raise BranchResearchError(
            "scorecards must cover every active candidate exactly once"
        )
    for card in scorecards:
        if portfolio.branch(card.branch_id).head.revision_digest != (
            card.branch_revision_digest
        ):
            raise BranchResearchError(f"{card.branch_id}: scorecard is stale")
    combined_evidence = tuple(
        sorted(
            {
                *evidence_refs,
                *(ref for card in scorecards for ref in card.evidence_refs),
            }
        )
    )
    _strings(combined_evidence, "decision evidence_refs", refs=True)
    selected: str | None = None
    authority: str | None = None
    status = BranchSelectionStatus.HUMAN_REVIEW_REQUIRED
    reason = rationale
    if rules.mode is BranchSelectionMode.AUTOMATIC:
        if human_branch_id is not None or human_authority_id is not None:
            raise BranchResearchError("automatic selection cannot carry a human choice")
        winner, margin = _automatic_winner(scorecards, rules)
        if winner is not None:
            authority = rules.automatic_authority_id
            assert authority is not None
            if not portfolio.selection_policy.permits(authority):
                raise BranchResearchError(
                    "automatic authority is not permitted by selection policy"
                )
            selected = winner.branch_id
            status = BranchSelectionStatus.SELECTED
        else:
            hard_feasible = tuple(
                item
                for item in scorecards
                if _hard_feasible(
                    item,
                    require_assessment=rules.require_hard_feasibility,
                )
            )
            if not hard_feasible:
                reason = (
                    f"{rationale} Human review required: no Candidate has "
                    "a satisfied hard-feasibility assessment."
                )
            else:
                top_score = max(
                    item.aggregate_score for item in hard_feasible
                )
                reason = (
                    f"{rationale} Human review required: top eligible score "
                    f"{top_score:.6f}, margin {margin:.6f}, required "
                    f"score {rules.minimum_score:.6f}, margin "
                    f"{rules.minimum_margin:.6f}."
                )
    else:
        if (human_branch_id is None) != (human_authority_id is None):
            raise BranchResearchError(
                "human branch and authority must be supplied together"
            )
        if human_branch_id is not None:
            if human_branch_id not in candidate_ids:
                raise BranchResearchError("human selected an unscored branch")
            assert human_authority_id is not None
            if not portfolio.selection_policy.permits(human_authority_id):
                raise BranchResearchError(
                    "human authority is not permitted by selection policy"
                )
            chosen_card = next(
                item for item in scorecards if item.branch_id == human_branch_id
            )
            if not _hard_feasible(
                chosen_card,
                require_assessment=rules.require_hard_feasibility,
            ):
                raise BranchResearchError(
                    "hard feasibility forbids the human-selected Candidate"
                )
            selected = human_branch_id
            authority = human_authority_id
            status = BranchSelectionStatus.SELECTED
    others = tuple(sorted(set(candidate_ids) - ({selected} if selected else set())))
    return BranchSelectionDecision(
        decision_id=decision_id,
        project_id=portfolio.project_id,
        run_id=portfolio.run_id,
        portfolio_id=portfolio.portfolio_id,
        candidate_portfolio_digest=portfolio.portfolio_digest,
        base_state_sha256=portfolio.base.require_digest(),
        operational_state_digest=portfolio.operational_state_digest,
        rules=rules,
        status=status,
        scorecards=scorecards,
        selected_branch_id=selected,
        pruned_branch_ids=(
            others
            if selected is not None
            and rules.mode is BranchSelectionMode.AUTOMATIC
            else ()
        ),
        parked_branch_ids=(
            others
            if selected is not None
            and rules.mode is BranchSelectionMode.HUMAN_IN_THE_LOOP
            else ()
        ),
        authority_id=authority,
        decision_ref=decision_ref,
        evidence_refs=combined_evidence,
        rationale=reason,
    )


def _apply_branch_selection(
    portfolio: DesignOptionPortfolio,
    decision: BranchSelectionDecision,
    *,
    selection_record: ProjectRecordRef,
) -> DesignOptionPortfolio:
    """Apply one persisted selected decision through portfolio transitions."""

    if not isinstance(portfolio, DesignOptionPortfolio):
        raise TypeError("portfolio must be DesignOptionPortfolio")
    if not isinstance(decision, BranchSelectionDecision):
        raise TypeError("decision must be BranchSelectionDecision")
    require_record_payload(
        selection_record,
        decision.to_dict(),
        run=portfolio.run,
        area_prefix="records",
        field="selection_record",
    )
    if decision.status is not BranchSelectionStatus.SELECTED:
        raise BranchResearchError("human review is unresolved")
    if (
        portfolio.project_id != decision.project_id
        or portfolio.run_id != decision.run_id
        or portfolio.portfolio_id != decision.portfolio_id
        or portfolio.portfolio_digest != decision.candidate_portfolio_digest
        or portfolio.base.require_digest() != decision.base_state_sha256
        or portfolio.operational_state_digest != decision.operational_state_digest
    ):
        raise BranchResearchError("selection decision is stale or cross-scoped")
    active = tuple(
        branch
        for branch in portfolio.branches
        if branch.lifecycle is BranchLifecycle.ACTIVE
    )
    if tuple(item.branch_id for item in decision.scorecards) != tuple(
        item.branch_id for item in active
    ) or any(
        portfolio.branch(card.branch_id).head.revision_digest
        != card.branch_revision_digest
        for card in decision.scorecards
    ):
        raise BranchResearchError(
            "selection scorecards do not match the active Candidate heads"
        )
    assert decision.selected_branch_id is not None
    assert decision.authority_id is not None
    evidence = tuple(sorted({*decision.evidence_refs, selection_record.uri}))
    result = select_branch(
        portfolio,
        expected_portfolio_digest=portfolio.portfolio_digest,
        branch_id=decision.selected_branch_id,
        authority_id=decision.authority_id,
        decision_ref=selection_record.uri,
        rationale=decision.rationale,
        evidence_refs=evidence,
        transition_id=f"select-{decision.decision_digest[:16]}",
    )
    for index, branch_id in enumerate(decision.pruned_branch_ids, start=1):
        result = park_branch(
            result,
            expected_portfolio_digest=result.portfolio_digest,
            branch_id=branch_id,
            authority_id=decision.authority_id,
            decision_ref=selection_record.uri,
            rationale=(
                "Algorithmically pruned and parked after the recorded score "
                f"and margin gate; retained for reopening. {decision.rationale}"
            ),
            evidence_refs=evidence,
            transition_id=f"prune-{index:03d}-{decision.decision_digest[:12]}",
        )
    for index, branch_id in enumerate(decision.parked_branch_ids, start=1):
        result = park_branch(
            result,
            expected_portfolio_digest=result.portfolio_digest,
            branch_id=branch_id,
            authority_id=decision.authority_id,
            decision_ref=selection_record.uri,
            rationale=(
                "Parked after the recorded human selection; retained for "
                f"reopening. {decision.rationale}"
            ),
            evidence_refs=evidence,
            transition_id=f"park-{index:03d}-{decision.decision_digest[:12]}",
        )
    return result


@dataclass(frozen=True, slots=True)
class BranchResearchScope:
    scope_id: str
    run: RunRef
    portfolio_id: str
    portfolio_digest: str
    operational_state_digest: str
    source_branch: BranchRef
    branch_id: str
    branch_revision_id: str
    branch_revision_digest: str
    predecessor_scope_digest: str | None
    selection_decision_digest: str
    selection_record_ref: ProjectRecordRef
    source_option_set_ref: ProjectRecordRef
    source_state_ref: ProjectRecordRef
    active_decision_refs: tuple[str, ...]
    branch_search_terms: tuple[str, ...]
    excluded_search_terms: tuple[str, ...]
    domain_allowlist: tuple[str, ...]
    context_refs: tuple[str, ...]

    SCHEMA = "BranchResearchScope@1"

    def __post_init__(self) -> None:
        require_identifier(self.scope_id, "scope_id")
        if not isinstance(self.run, RunRef):
            raise TypeError("run must be RunRef")
        require_identifier(self.portfolio_id, "portfolio_id")
        require_sha256(self.portfolio_digest, "portfolio_digest")
        require_sha256(self.operational_state_digest, "operational_state_digest")
        if not isinstance(self.source_branch, BranchRef):
            raise TypeError("source_branch must be BranchRef")
        if self.source_branch.run != self.run:
            raise BranchResearchError("source branch belongs to another run")
        require_identifier(self.branch_id, "branch_id")
        require_identifier(self.branch_revision_id, "branch_revision_id")
        require_sha256(self.branch_revision_digest, "branch_revision_digest")
        if self.predecessor_scope_digest is not None:
            require_sha256(self.predecessor_scope_digest, "predecessor_scope_digest")
        require_sha256(self.selection_decision_digest, "selection_decision_digest")
        if not isinstance(self.selection_record_ref, ProjectRecordRef):
            raise TypeError("selection_record_ref must be ProjectRecordRef")
        for ref, prefix, field in (
            (
                self.selection_record_ref,
                "branch-selection-",
                "selection_record_ref",
            ),
            (
                self.source_option_set_ref,
                "branch-source-option-set-",
                "source_option_set_ref",
            ),
            (
                self.source_state_ref,
                "branch-source-state-",
                "source_state_ref",
            ),
        ):
            if not isinstance(ref, ProjectRecordRef):
                raise TypeError(f"{field} must be ProjectRecordRef")
            expected_prefix = f"runs/{self.run.run_id}/records/{prefix}"
            if (
                ref.project_id != self.run.project_id
                or ref.media_type != "application/json"
                or not ref.relative_path.startswith(expected_prefix)
            ):
                raise BranchResearchError(
                    f"{field} crosses its exact P036 run-record area"
                )
        _strings(
            self.active_decision_refs,
            "active_decision_refs",
            refs=True,
        )
        _strings(self.branch_search_terms, "branch_search_terms")
        _strings(
            self.excluded_search_terms,
            "excluded_search_terms",
            allow_empty=True,
        )
        if set(item.casefold() for item in self.branch_search_terms) & set(
            item.casefold() for item in self.excluded_search_terms
        ):
            raise BranchResearchError("included and excluded search terms overlap")
        _domains(self.domain_allowlist)
        if not self.domain_allowlist:
            raise BranchResearchError(
                "branch research requires an explicit domain allowlist"
            )
        _strings(self.context_refs, "context_refs", refs=True)
        required_context_refs = {
            self.selection_record_ref.uri,
            self.source_option_set_ref.uri,
            self.source_state_ref.uri,
        }
        if not required_context_refs <= set(self.context_refs):
            raise BranchResearchError(
                "branch context must retain selection and source records"
            )

    @property
    def scope_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def identity_dict(self) -> dict[str, object]:
        return {
            "project_id": self.run.project_id,
            "run_id": self.run.run_id,
            "base_state_sha256": self.run.base.require_digest(),
            "portfolio_id": self.portfolio_id,
            "portfolio_digest": self.portfolio_digest,
            "operational_state_digest": self.operational_state_digest,
            "source_branch": {
                "branch_id": self.source_branch.branch_id,
                "epoch": self.source_branch.epoch,
            },
            "branch_id": self.branch_id,
            "branch_revision_id": self.branch_revision_id,
            "branch_revision_digest": self.branch_revision_digest,
            "predecessor_scope_digest": self.predecessor_scope_digest,
            "selection_decision_digest": self.selection_decision_digest,
            "selection_record_ref": _record_ref_dict(self.selection_record_ref),
            "source_option_set_ref": _record_ref_dict(
                self.source_option_set_ref
            ),
            "source_state_ref": _record_ref_dict(self.source_state_ref),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "scope_id": self.scope_id,
            "branch_identity": self.identity_dict(),
            "base": {
                "project_id": self.run.base.project_id,
                "version": self.run.base.version,
                "state_sha256": self.run.base.require_digest(),
            },
            "active_decision_refs": list(self.active_decision_refs),
            "branch_search_terms": list(self.branch_search_terms),
            "excluded_search_terms": list(self.excluded_search_terms),
            "domain_allowlist": list(self.domain_allowlist),
            "context_refs": list(self.context_refs),
            "research_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "BranchResearchScope":
        payload = _mapping(value, "branch research scope")
        _exact(
            payload,
            {
                "schema",
                "scope_id",
                "branch_identity",
                "base",
                "active_decision_refs",
                "branch_search_terms",
                "excluded_search_terms",
                "domain_allowlist",
                "context_refs",
                "research_authority",
                "canonical_write_authority",
            },
            "branch research scope",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["research_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise BranchResearchError("branch research scope acquired authority")
        identity = _mapping(payload["branch_identity"], "branch identity")
        _exact(
            identity,
            {
                "project_id",
                "run_id",
                "base_state_sha256",
                "portfolio_id",
                "portfolio_digest",
                "operational_state_digest",
                "source_branch",
                "branch_id",
                "branch_revision_id",
                "branch_revision_digest",
                "predecessor_scope_digest",
                "selection_decision_digest",
                "selection_record_ref",
                "source_option_set_ref",
                "source_state_ref",
            },
            "branch identity",
        )
        base = _mapping(payload["base"], "base")
        _exact(base, {"project_id", "version", "state_sha256"}, "base")
        if (
            base["project_id"] != identity["project_id"]
            or base["state_sha256"] != identity["base_state_sha256"]
        ):
            raise BranchResearchError("scope base identity changed")
        source_branch = _mapping(identity["source_branch"], "source branch")
        _exact(source_branch, {"branch_id", "epoch"}, "source branch")
        collection_fields = (
            "active_decision_refs",
            "branch_search_terms",
            "excluded_search_terms",
            "domain_allowlist",
            "context_refs",
        )
        if any(not isinstance(payload[field], list) for field in collection_fields):
            raise TypeError("branch research scope collections must be lists")
        project_id = str(identity["project_id"])
        run = RunRef(
            project_id=project_id,
            run_id=str(identity["run_id"]),
            base=ProjectVersionRef(
                project_id=project_id,
                version=int(base["version"]),
                state_sha256=str(base["state_sha256"]),
            ),
        )
        return cls(
            scope_id=str(payload["scope_id"]),
            run=run,
            portfolio_id=str(identity["portfolio_id"]),
            portfolio_digest=str(identity["portfolio_digest"]),
            operational_state_digest=str(identity["operational_state_digest"]),
            source_branch=BranchRef(
                run=run,
                branch_id=str(source_branch["branch_id"]),
                epoch=int(source_branch["epoch"]),
            ),
            branch_id=str(identity["branch_id"]),
            branch_revision_id=str(identity["branch_revision_id"]),
            branch_revision_digest=str(identity["branch_revision_digest"]),
            predecessor_scope_digest=(
                str(identity["predecessor_scope_digest"])
                if identity["predecessor_scope_digest"] is not None
                else None
            ),
            selection_decision_digest=str(identity["selection_decision_digest"]),
            selection_record_ref=_record_ref_from_dict(
                identity["selection_record_ref"],
                "selection_record_ref",
            ),
            source_option_set_ref=_record_ref_from_dict(
                identity["source_option_set_ref"],
                "source_option_set_ref",
            ),
            source_state_ref=_record_ref_from_dict(
                identity["source_state_ref"],
                "source_state_ref",
            ),
            active_decision_refs=tuple(
                str(item) for item in payload["active_decision_refs"]
            ),
            branch_search_terms=tuple(
                str(item) for item in payload["branch_search_terms"]
            ),
            excluded_search_terms=tuple(
                str(item) for item in payload["excluded_search_terms"]
            ),
            domain_allowlist=tuple(
                str(item) for item in payload["domain_allowlist"]
            ),
            context_refs=tuple(str(item) for item in payload["context_refs"]),
        )


def _compile_branch_research_scope(
    portfolio: DesignOptionPortfolio,
    decision: BranchSelectionDecision,
    *,
    scope_id: str,
    selection_record: ProjectRecordRef,
    source_option_set: SchematicOptionSet,
    source_option_set_ref: ProjectRecordRef,
    source_state: OperationalMarkovState,
    source_state_ref: ProjectRecordRef,
    predecessor_scope_digest: str | None = None,
    context_refs: tuple[str, ...] = (),
) -> BranchResearchScope:
    """Compile the only legal research envelope for a selected branch."""

    if decision.status is not BranchSelectionStatus.SELECTED:
        raise BranchResearchError("research scope requires a selected decision")
    if not isinstance(source_option_set, SchematicOptionSet):
        raise TypeError("source_option_set must be SchematicOptionSet")
    if not isinstance(source_state, OperationalMarkovState):
        raise TypeError("source_state must be OperationalMarkovState")
    require_record_payload(
        selection_record,
        decision.to_dict(),
        run=portfolio.run,
        area_prefix="records",
        field="selection_record",
    )
    require_record_payload(
        source_option_set_ref,
        source_option_set.to_dict(),
        run=portfolio.run,
        area_prefix="records",
        field="source_option_set_ref",
    )
    require_record_payload(
        source_state_ref,
        source_state.to_dict(),
        run=portfolio.run,
        area_prefix="records",
        field="source_state_ref",
    )
    if (
        source_option_set.option_set_digest != portfolio.source_option_set_digest
        or source_option_set.branch != source_state.branch
        or source_option_set.operational_state_digest != source_state.state_digest
        or portfolio.operational_state_digest != source_state.state_digest
        or source_state.branch.run != portfolio.run
    ):
        raise BranchResearchError(
            "source option set, operational state, and portfolio disagree"
        )
    selected = portfolio.selected_branch
    if (
        selected is None
        or selected.branch_id != decision.selected_branch_id
        or portfolio.project_id != decision.project_id
        or portfolio.run_id != decision.run_id
        or portfolio.portfolio_id != decision.portfolio_id
        or portfolio.base.require_digest() != decision.base_state_sha256
        or portfolio.operational_state_digest != decision.operational_state_digest
    ):
        raise BranchResearchError("selected portfolio and decision do not match")
    selection_transition = next(
        (
            item
            for item in reversed(portfolio.transitions)
            if item.kind is PortfolioTransitionKind.SELECT
            and selected.branch_id in item.affected_branch_ids
        ),
        None,
    )
    if (
        selection_transition is None
        or selection_transition.decision_ref != selection_record.uri
        or selection_transition.predecessor_portfolio_digest
        != decision.candidate_portfolio_digest
        or selection_transition.authority_id != decision.authority_id
    ):
        raise BranchResearchError(
            "portfolio does not retain this selection record"
        )
    if decision.rules.mode is BranchSelectionMode.AUTOMATIC:
        expected = set(decision.pruned_branch_ids)
        actual = {
            item.branch_id
            for item in portfolio.branches
            if item.lifecycle is BranchLifecycle.PARKED
        }
        if not expected <= actual:
            raise BranchResearchError("automatic pruning was not applied")
    else:
        expected = set(decision.parked_branch_ids)
        actual = {
            item.branch_id
            for item in portfolio.branches
            if item.lifecycle is BranchLifecycle.PARKED
        }
        if not expected <= actual:
            raise BranchResearchError("human alternatives were not parked")
    selected_card = next(
        item
        for item in decision.scorecards
        if item.branch_id == selected.branch_id
    )
    profile = selected_card.research_profile
    return BranchResearchScope(
        scope_id=scope_id,
        run=portfolio.run,
        portfolio_id=portfolio.portfolio_id,
        portfolio_digest=portfolio.portfolio_digest,
        operational_state_digest=portfolio.operational_state_digest,
        source_branch=source_state.branch,
        branch_id=selected.branch_id,
        branch_revision_id=selected.head.revision_id,
        branch_revision_digest=selected.head.revision_digest,
        predecessor_scope_digest=predecessor_scope_digest,
        selection_decision_digest=decision.decision_digest,
        selection_record_ref=selection_record,
        source_option_set_ref=source_option_set_ref,
        source_state_ref=source_state_ref,
        active_decision_refs=profile.active_decision_refs,
        branch_search_terms=profile.branch_search_terms,
        excluded_search_terms=profile.excluded_search_terms,
        domain_allowlist=profile.domain_allowlist,
        context_refs=tuple(
            sorted(
                {
                    *context_refs,
                    *profile.evidence_refs,
                    selection_record.uri,
                    source_option_set_ref.uri,
                    source_state_ref.uri,
                }
            )
        ),
    )


@dataclass(frozen=True, slots=True)
class DecisionResearchNeed:
    decision_ref: str
    query_id: str
    question: str
    search_terms: tuple[str, ...]
    jurisdiction: str | None = None
    domain_allowlist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_logical_ref(self.decision_ref, "decision_ref")
        require_identifier(self.query_id, "query_id")
        _text(self.question, "question")
        _strings(self.search_terms, "need search_terms")
        if self.jurisdiction is not None:
            _text(self.jurisdiction, "jurisdiction")
        _domains(self.domain_allowlist)


@dataclass(frozen=True, slots=True)
class BranchPrecedentQuery:
    query_id: str
    question: str
    scope: BranchResearchScope
    decision_refs: tuple[str, ...]
    search_terms: tuple[str, ...]
    jurisdiction: str | None
    domain_allowlist: tuple[str, ...]

    SCHEMA = "BranchPrecedentQuery@1"

    def __post_init__(self) -> None:
        require_identifier(self.query_id, "query_id")
        _text(self.question, "question")
        if not isinstance(self.scope, BranchResearchScope):
            raise TypeError("scope must be BranchResearchScope")
        _strings(self.decision_refs, "query decision_refs", refs=True)
        if not set(self.decision_refs) <= set(self.scope.active_decision_refs):
            raise BranchResearchError(
                "query decision refs exceed the active branch universe"
            )
        _strings(self.search_terms, "query search_terms")
        lowered = {item.casefold() for item in self.search_terms}
        if not {item.casefold() for item in self.scope.branch_search_terms} <= lowered:
            raise BranchResearchError("query dropped branch-defining search terms")
        if any(
            excluded.casefold() in term.casefold()
            for excluded in self.scope.excluded_search_terms
            for term in self.search_terms
        ):
            raise BranchResearchError("query reintroduced a pruned branch term")
        if self.jurisdiction is not None:
            _text(self.jurisdiction, "jurisdiction")
        _domains(self.domain_allowlist)
        if not self.domain_allowlist:
            raise BranchResearchError(
                "branch query requires an explicit domain allowlist"
            )
        if self.scope.domain_allowlist and not set(self.domain_allowlist) <= set(
            self.scope.domain_allowlist
        ):
            raise BranchResearchError("query widened the branch source allowlist")

    @property
    def query_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def allows_url(self, url: str) -> bool:
        if not self.domain_allowlist:
            return True
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        return any(
            host == domain or host.endswith(f".{domain}")
            for domain in self.domain_allowlist
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "query_id": self.query_id,
            "question": self.question,
            "branch_scope": self.scope.to_dict(),
            "scope_digest": self.scope.scope_digest,
            "decision_refs": list(self.decision_refs),
            "search_terms": list(self.search_terms),
            "jurisdiction": self.jurisdiction,
            "domain_allowlist": list(self.domain_allowlist),
            "adoption_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "BranchPrecedentQuery":
        payload = _mapping(value, "branch precedent query")
        _exact(
            payload,
            {
                "schema",
                "query_id",
                "question",
                "branch_scope",
                "scope_digest",
                "decision_refs",
                "search_terms",
                "jurisdiction",
                "domain_allowlist",
                "adoption_authority",
                "canonical_write_authority",
            },
            "branch precedent query",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["adoption_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise BranchResearchError("branch query acquired authority")
        for field in ("decision_refs", "search_terms", "domain_allowlist"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        scope = BranchResearchScope.from_dict(payload["branch_scope"])
        if payload["scope_digest"] != scope.scope_digest:
            raise BranchResearchError("branch query scope digest changed")
        jurisdiction = payload["jurisdiction"]
        return cls(
            query_id=str(payload["query_id"]),
            question=str(payload["question"]),
            scope=scope,
            decision_refs=tuple(str(item) for item in payload["decision_refs"]),
            search_terms=tuple(str(item) for item in payload["search_terms"]),
            jurisdiction=(str(jurisdiction) if jurisdiction is not None else None),
            domain_allowlist=tuple(
                str(item) for item in payload["domain_allowlist"]
            ),
        )


def compile_branch_query(
    scope: BranchResearchScope,
    need: DecisionResearchNeed,
) -> BranchPrecedentQuery:
    if not isinstance(scope, BranchResearchScope):
        raise TypeError("scope must be BranchResearchScope")
    if not isinstance(need, DecisionResearchNeed):
        raise TypeError("need must be DecisionResearchNeed")
    allowlist = need.domain_allowlist or scope.domain_allowlist
    return BranchPrecedentQuery(
        query_id=need.query_id,
        question=need.question,
        scope=scope,
        decision_refs=(need.decision_ref,),
        search_terms=tuple(
            sorted(set(scope.branch_search_terms) | set(need.search_terms))
        ),
        jurisdiction=need.jurisdiction,
        domain_allowlist=allowlist,
    )


def require_branch_source_url(
    query: BranchPrecedentQuery,
    url: str,
    *,
    field: str = "source_url",
) -> str:
    """Validate a requested or final redirected URL against branch policy."""

    if not isinstance(query, BranchPrecedentQuery):
        raise TypeError("query must be BranchPrecedentQuery")
    _text(url, field)
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BranchResearchError(f"{field} must be an HTTP(S) URL")
    if not query.allows_url(url):
        raise BranchResearchError(
            f"{field} is outside the branch source allowlist"
        )
    return url


@dataclass(frozen=True, slots=True)
class BranchEvidenceSnapshot:
    """Typed web snapshot bound to one exact branch query and revision."""

    scope_digest: str
    branch_id: str
    branch_revision_digest: str
    query_id: str
    query_digest: str
    requested_url: str
    final_url: str
    retrieved_at: str
    content_sha256: str
    content_bytes: int
    text: str
    text_sha256: str

    SCHEMA = "BranchEvidenceSnapshot@1"

    def __post_init__(self) -> None:
        require_sha256(self.scope_digest, "snapshot scope_digest")
        require_identifier(self.branch_id, "snapshot branch_id")
        require_sha256(
            self.branch_revision_digest,
            "snapshot branch_revision_digest",
        )
        require_identifier(self.query_id, "snapshot query_id")
        require_sha256(self.query_digest, "snapshot query_digest")
        for value, field in (
            (self.requested_url, "requested_url"),
            (self.final_url, "final_url"),
            (self.retrieved_at, "retrieved_at"),
        ):
            _text(value, field)
        require_sha256(self.content_sha256, "snapshot content_sha256")
        require_sha256(self.text_sha256, "snapshot text_sha256")
        if (
            not isinstance(self.content_bytes, int)
            or isinstance(self.content_bytes, bool)
            or self.content_bytes < 0
        ):
            raise BranchResearchError("snapshot content_bytes must be non-negative")
        if not isinstance(self.text, str) or not self.text:
            raise BranchResearchError("snapshot text must be non-empty")
        if hashlib.sha256(self.text.encode("utf-8")).hexdigest() != self.text_sha256:
            raise BranchResearchError("snapshot text digest does not match text")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "scope_digest": self.scope_digest,
            "branch_id": self.branch_id,
            "branch_revision_digest": self.branch_revision_digest,
            "query_id": self.query_id,
            "query_digest": self.query_digest,
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "retrieved_at": self.retrieved_at,
            "content_sha256": self.content_sha256,
            "content_bytes": self.content_bytes,
            "text": self.text,
            "text_sha256": self.text_sha256,
            "adoption_authority": False,
            "prompt_injection_surface": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "BranchEvidenceSnapshot":
        payload = _mapping(value, "branch evidence snapshot")
        _exact(
            payload,
            {
                "schema",
                "scope_digest",
                "branch_id",
                "branch_revision_digest",
                "query_id",
                "query_digest",
                "requested_url",
                "final_url",
                "retrieved_at",
                "content_sha256",
                "content_bytes",
                "text",
                "text_sha256",
                "adoption_authority",
                "prompt_injection_surface",
                "canonical_write_authority",
            },
            "branch evidence snapshot",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["adoption_authority"] is not False
            or payload["prompt_injection_surface"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise BranchResearchError("branch evidence snapshot acquired authority")
        content_bytes = payload["content_bytes"]
        if not isinstance(content_bytes, int) or isinstance(content_bytes, bool):
            raise TypeError("snapshot content_bytes must be an integer")
        return cls(
            scope_digest=str(payload["scope_digest"]),
            branch_id=str(payload["branch_id"]),
            branch_revision_digest=str(payload["branch_revision_digest"]),
            query_id=str(payload["query_id"]),
            query_digest=str(payload["query_digest"]),
            requested_url=str(payload["requested_url"]),
            final_url=str(payload["final_url"]),
            retrieved_at=str(payload["retrieved_at"]),
            content_sha256=str(payload["content_sha256"]),
            content_bytes=content_bytes,
            text=str(payload["text"]),
            text_sha256=str(payload["text_sha256"]),
        )


def bind_branch_snapshot(
    query: BranchPrecedentQuery,
    *,
    requested_url: str,
    snapshot: Mapping[str, object],
) -> BranchEvidenceSnapshot:
    """Validate a WebEvidenceSnapshot and bind request plus final URL."""

    if not isinstance(query, BranchPrecedentQuery):
        raise TypeError("query must be BranchPrecedentQuery")
    payload = _mapping(snapshot, "web evidence snapshot")
    _exact(
        payload,
        {
            "schema",
            "url",
            "retrieved_at",
            "content_sha256",
            "content_bytes",
            "text",
            "text_sha256",
            "adoption_authority",
            "prompt_injection_surface",
            "canonical_write_authority",
        },
        "web evidence snapshot",
    )
    if (
        payload["schema"] != "WebEvidenceSnapshot@1"
        or payload["adoption_authority"] is not False
        or payload["prompt_injection_surface"] is not False
        or payload["canonical_write_authority"] is not False
    ):
        raise BranchResearchError("web evidence snapshot schema or authority changed")
    content_bytes = payload["content_bytes"]
    if not isinstance(content_bytes, int) or isinstance(content_bytes, bool):
        raise TypeError("web evidence content_bytes must be an integer")
    final_url = str(payload["url"])
    require_branch_source_url(query, requested_url, field="requested_url")
    require_branch_source_url(query, final_url, field="final_url")
    return BranchEvidenceSnapshot(
        scope_digest=query.scope.scope_digest,
        branch_id=query.scope.branch_id,
        branch_revision_digest=query.scope.branch_revision_digest,
        query_id=query.query_id,
        query_digest=query.query_digest,
        requested_url=requested_url,
        final_url=final_url,
        retrieved_at=str(payload["retrieved_at"]),
        content_sha256=str(payload["content_sha256"]),
        content_bytes=content_bytes,
        text=str(payload["text"]),
        text_sha256=str(payload["text_sha256"]),
    )


@dataclass(frozen=True, slots=True)
class BranchPrecedentAdoption:
    query_id: str
    query_digest: str
    scope_digest: str
    branch_id: str
    branch_revision_digest: str
    adoption: PrecedentAdoption

    SCHEMA = "BranchPrecedentAdoption@1"

    def __post_init__(self) -> None:
        require_identifier(self.query_id, "query_id")
        require_sha256(self.query_digest, "query_digest")
        require_sha256(self.scope_digest, "scope_digest")
        require_identifier(self.branch_id, "branch_id")
        require_sha256(self.branch_revision_digest, "branch_revision_digest")
        if not isinstance(self.adoption, PrecedentAdoption):
            raise TypeError("adoption must be PrecedentAdoption")

    @property
    def adoption_id(self) -> str:
        return self.adoption.adoption_id

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "query_id": self.query_id,
            "query_digest": self.query_digest,
            "scope_digest": self.scope_digest,
            "branch_id": self.branch_id,
            "branch_revision_digest": self.branch_revision_digest,
            "adoption": self.adoption.to_dict(),
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "BranchPrecedentAdoption":
        payload = _mapping(value, "branch precedent adoption")
        _exact(
            payload,
            {
                "schema",
                "query_id",
                "query_digest",
                "scope_digest",
                "branch_id",
                "branch_revision_digest",
                "adoption",
                "canonical_write_authority",
            },
            "branch precedent adoption",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["canonical_write_authority"] is not False
        ):
            raise BranchResearchError("branch adoption acquired authority")
        return cls(
            query_id=str(payload["query_id"]),
            query_digest=str(payload["query_digest"]),
            scope_digest=str(payload["scope_digest"]),
            branch_id=str(payload["branch_id"]),
            branch_revision_digest=str(payload["branch_revision_digest"]),
            adoption=PrecedentAdoption.from_dict(payload["adoption"]),
        )


def bind_branch_adoption(
    query: BranchPrecedentQuery,
    adoption: PrecedentAdoption,
) -> BranchPrecedentAdoption:
    """Bind promoted facts to the exact query and selected branch revision."""

    if not isinstance(query, BranchPrecedentQuery):
        raise TypeError("query must be BranchPrecedentQuery")
    if not isinstance(adoption, PrecedentAdoption):
        raise TypeError("adoption must be PrecedentAdoption")
    allowed = set(query.decision_refs)
    for fact in adoption.facts:
        if not fact.decision_refs or not set(fact.decision_refs) <= allowed:
            raise BranchResearchError(
                f"{fact.fact_id}: adoption exceeds its branch query"
            )
    return BranchPrecedentAdoption(
        query_id=query.query_id,
        query_digest=query.query_digest,
        scope_digest=query.scope.scope_digest,
        branch_id=query.scope.branch_id,
        branch_revision_digest=query.scope.branch_revision_digest,
        adoption=adoption,
    )
