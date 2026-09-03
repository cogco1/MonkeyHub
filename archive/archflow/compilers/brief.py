"""Compile interpreted request/evidence observations into DesignBrief@1.

Language and document interpretation remain model/tool work. This deterministic
boundary preserves the supplied meanings, authority, uncertainty, and exact
project base without selecting rooms, area, topology, or geometry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from archflow.project.refs import ProjectVersionRef, require_identifier
from archive.archflow.compilers.commitments import (
    IntentCompilationStatus,
    IntentObservation,
    compile_intent,
)
from archive.archflow.state.design_brief import (
    BriefClaim,
    BriefClaimKind,
    BriefConstraintOperator,
    BriefConstraintProposal,
    BriefSlot,
    BriefSlotState,
    BriefSlotStatus,
    DesignBrief,
)
from archflow.state.operational_state import (
    DesignObligation,
    FactEpistemicStatus,
    StateDomain,
    StateFact,
    require_local_id,
    require_logical_ref,
)
from archflow.contracts.canonical import canonical_digest


_COMPILER_ID = "archflow.design-brief-compiler"
_COMPILER_VERSION = "1"
_MAX_ITEMS = 256
_MAX_TEXT = 1_000


@dataclass(frozen=True, slots=True)
class BriefObservation:
    observation_id: str
    slot: BriefSlot
    kind: BriefClaimKind
    key: str
    value: object
    epistemic_status: FactEpistemicStatus
    authority_id: str
    source_refs: tuple[str, ...]
    resolves_slot: bool
    qualification: str | None = None

    def __post_init__(self) -> None:
        require_local_id(self.observation_id, "observation_id")
        if not isinstance(self.slot, BriefSlot):
            raise TypeError("slot must be BriefSlot")
        if not isinstance(self.kind, BriefClaimKind):
            raise TypeError("kind must be BriefClaimKind")
        require_local_id(self.key, "key")
        if not isinstance(self.epistemic_status, FactEpistemicStatus):
            raise TypeError(
                "epistemic_status must be FactEpistemicStatus"
            )
        _text(self.authority_id, "authority_id")
        _refs(self.source_refs, "source_refs")
        if not isinstance(self.resolves_slot, bool):
            raise TypeError("resolves_slot must be boolean")
        if self.qualification is not None:
            _text(self.qualification, "qualification")
        _bounded_value(self.value)


@dataclass(frozen=True, slots=True)
class BriefIntentObservation:
    slot: BriefSlot
    observation: IntentObservation

    def __post_init__(self) -> None:
        if not isinstance(self.slot, BriefSlot):
            raise TypeError("slot must be BriefSlot")
        if not isinstance(self.observation, IntentObservation):
            raise TypeError("observation must be IntentObservation")


@dataclass(frozen=True, slots=True)
class BriefCompilationReceipt:
    compilation_id: str
    project_id: str
    run_id: str
    base_state_sha256: str
    compiler_id: str
    compiler_version: str
    raw_request_ref: str
    observation_ids: tuple[str, ...]
    intent_observation_ids: tuple[str, ...]
    brief_digest: str
    unknown_slots: tuple[str, ...]
    ambiguous_slots: tuple[str, ...]

    SCHEMA = "BriefCompilationReceipt@1"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "compilation_id": self.compilation_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base_state_sha256": self.base_state_sha256,
            "compiler_id": self.compiler_id,
            "compiler_version": self.compiler_version,
            "raw_request_ref": self.raw_request_ref,
            "observation_ids": list(self.observation_ids),
            "intent_observation_ids": list(
                self.intent_observation_ids
            ),
            "brief_digest": self.brief_digest,
            "unknown_slots": list(self.unknown_slots),
            "ambiguous_slots": list(self.ambiguous_slots),
            "generation_authority": False,
        }


@dataclass(frozen=True, slots=True)
class CompiledDesignBrief:
    brief: DesignBrief
    receipt: BriefCompilationReceipt


def compile_design_brief(
    *,
    project_id: str,
    run_id: str,
    base: ProjectVersionRef,
    raw_request_ref: str,
    observations: tuple[BriefObservation, ...] = (),
    intent_observations: tuple[BriefIntentObservation, ...] = (),
    compiler_id: str = _COMPILER_ID,
    compiler_version: str = _COMPILER_VERSION,
) -> CompiledDesignBrief:
    """Compile a compact brief without interpreting language or choosing form."""

    require_identifier(project_id, "project_id")
    require_identifier(run_id, "run_id")
    if not isinstance(base, ProjectVersionRef):
        raise TypeError("base must be ProjectVersionRef")
    if base.project_id != project_id:
        raise ValueError("brief and base belong to different projects")
    base_digest = base.require_digest()
    require_logical_ref(raw_request_ref, "raw_request_ref")
    _typed_tuple(observations, BriefObservation, "observations")
    _typed_tuple(
        intent_observations,
        BriefIntentObservation,
        "intent_observations",
    )
    _text(compiler_id, "compiler_id")
    _text(compiler_version, "compiler_version")
    _unique(
        tuple(item.observation_id for item in observations),
        "observation ids",
    )
    _unique(
        tuple(
            item.observation.observation_id
            for item in intent_observations
        ),
        "intent observation ids",
    )
    for ref in (
        raw_request_ref,
        *(ref for item in observations for ref in item.source_refs),
        *(
            ref
            for item in intent_observations
            for ref in item.observation.evidence_refs
        ),
    ):
        if ref.startswith("project://") and not ref.startswith(
            f"project://{project_id}/"
        ):
            raise ValueError(
                "brief evidence reference belongs to another project"
            )

    claims = tuple(
        sorted(
            (
                _compile_claim(
                    item,
                    compiler_id=compiler_id,
                    base_state_sha256=base_digest,
                )
                for item in observations
            ),
            key=lambda item: item.claim_id,
        )
    )
    observation_by_id = {
        item.observation_id: item for item in observations
    }
    constraints: list[BriefConstraintProposal] = []
    commitments = []
    slot_intent_statuses: dict[BriefSlot, list[IntentCompilationStatus]] = {}
    for wrapped in intent_observations:
        observation = wrapped.observation
        project_scope = f"project://{project_id}"
        if (
            observation.scope_ref != project_scope
            and not observation.scope_ref.startswith(f"{project_scope}/")
        ):
            raise ValueError(
                "intent observation belongs to another project scope"
            )
        compilation = compile_intent(observation)
        slot_intent_statuses.setdefault(wrapped.slot, []).append(
            compilation.status
        )
        for proposal in compilation.proposals:
            constraint = BriefConstraintProposal(
                proposal_id=proposal.proposal_id,
                slot=wrapped.slot,
                parameter_key=proposal.term.parameter_key,
                operator=BriefConstraintOperator(
                    proposal.term.operator.value
                ),
                value=proposal.term.value,
                unit=proposal.term.unit,
                commitment_id=proposal.commitment.commitment_id,
                source_refs=observation.evidence_refs,
                compiler_id=compiler_id,
                base_state_sha256=base_digest,
            )
            constraints.append(constraint)
            commitments.append(proposal.commitment)

    obligations: list[DesignObligation] = []
    slot_states: list[BriefSlotState] = []
    for slot in BriefSlot:
        slot_claims = tuple(
            item
            for item in claims
            if item.slot is slot
            and observation_by_id[
                item.claim_id.removeprefix("claim.")
            ].resolves_slot
        )
        slot_constraints = tuple(
            item for item in constraints if item.slot is slot
        )
        statuses = tuple(slot_intent_statuses.get(slot, ()))
        obligation: DesignObligation | None = None
        if slot_constraints:
            ambiguous = (
                IntentCompilationStatus.AMBIGUOUS in statuses
                or len(slot_constraints) > 1
            )
            status = (
                BriefSlotStatus.AMBIGUOUS
                if ambiguous
                else BriefSlotStatus.PROPOSED
            )
            obligation = _slot_obligation(
                slot,
                raw_request_ref,
                (
                    "Confirm one explicit interpretation."
                    if ambiguous
                    else "Authorize or decline the proposed constraint."
                ),
                tuple(
                    f"brief-constraint:{item.proposal_id}"
                    for item in slot_constraints
                ),
            )
        elif slot_claims:
            values = {
                item.fact.value.canonical_json for item in slot_claims
            }
            status = (
                BriefSlotStatus.SUPPORTED
                if len(values) == 1
                else BriefSlotStatus.AMBIGUOUS
            )
            if status is BriefSlotStatus.AMBIGUOUS:
                obligation = _slot_obligation(
                    slot,
                    raw_request_ref,
                    "Resolve the conflicting supported observations.",
                    tuple(item.ref for item in slot_claims),
                )
        else:
            status = BriefSlotStatus.UNKNOWN
            obligation = _slot_obligation(
                slot,
                raw_request_ref,
                "Supply or derive this missing brief input.",
                (f"brief-slot:{slot.value}",),
            )
        if obligation is not None:
            obligations.append(obligation)
        slot_states.append(
            BriefSlotState(
                slot=slot,
                status=status,
                claim_refs=tuple(item.ref for item in slot_claims),
                constraint_proposal_ids=tuple(
                    item.proposal_id for item in slot_constraints
                ),
                obligation_ids=(
                    (obligation.obligation_id,)
                    if obligation is not None
                    else ()
                ),
            )
        )

    evidence_refs = tuple(
        sorted(
            {
                raw_request_ref,
                *(
                    ref
                    for item in observations
                    for ref in item.source_refs
                ),
                *(
                    ref
                    for item in intent_observations
                    for ref in item.observation.evidence_refs
                ),
            }
        )
    )
    brief = DesignBrief(
        project_id=project_id,
        run_id=run_id,
        base=base,
        compiler_id=compiler_id,
        compiler_version=compiler_version,
        raw_request_ref=raw_request_ref,
        claims=claims,
        constraint_proposals=tuple(
            sorted(constraints, key=lambda item: item.proposal_id)
        ),
        proposed_commitments=tuple(
            sorted(
                commitments,
                key=lambda item: item.commitment_id,
            )
        ),
        obligations=tuple(
            sorted(
                obligations,
                key=lambda item: item.obligation_id,
            )
        ),
        slots=tuple(
            sorted(slot_states, key=lambda item: item.slot.value)
        ),
        evidence_refs=evidence_refs,
    )
    identity = {
        "project_id": project_id,
        "run_id": run_id,
        "base_state_sha256": base_digest,
        "compiler_id": compiler_id,
        "compiler_version": compiler_version,
        "brief_digest": brief.brief_digest,
    }
    compilation_id = f"brief-{canonical_digest(identity)[:20]}"
    receipt = BriefCompilationReceipt(
        compilation_id=compilation_id,
        project_id=project_id,
        run_id=run_id,
        base_state_sha256=base_digest,
        compiler_id=compiler_id,
        compiler_version=compiler_version,
        raw_request_ref=raw_request_ref,
        observation_ids=tuple(
            sorted(item.observation_id for item in observations)
        ),
        intent_observation_ids=tuple(
            sorted(
                item.observation.observation_id
                for item in intent_observations
            )
        ),
        brief_digest=brief.brief_digest,
        unknown_slots=tuple(
            sorted(
                item.slot.value
                for item in brief.slots
                if item.status is BriefSlotStatus.UNKNOWN
            )
        ),
        ambiguous_slots=tuple(
            sorted(
                item.slot.value
                for item in brief.slots
                if item.status is BriefSlotStatus.AMBIGUOUS
            )
        ),
    )
    return CompiledDesignBrief(brief=brief, receipt=receipt)


def _compile_claim(
    observation: BriefObservation,
    *,
    compiler_id: str,
    base_state_sha256: str,
) -> BriefClaim:
    fact = StateFact(
        domain=StateDomain.BRIEF,
        key=observation.key,
        value=observation.value,
        source_ref=observation.source_refs[0],
        epistemic_status=observation.epistemic_status,
        qualification=observation.qualification,
    )
    return BriefClaim(
        claim_id=f"claim.{observation.observation_id}",
        slot=observation.slot,
        kind=observation.kind,
        fact=fact,
        authority_id=observation.authority_id,
        source_refs=observation.source_refs,
        compiler_id=compiler_id,
        base_state_sha256=base_state_sha256,
    )


def _slot_obligation(
    slot: BriefSlot,
    source_ref: str,
    reason: str,
    subject_refs: tuple[str, ...],
) -> DesignObligation:
    return DesignObligation(
        obligation_id=f"resolve-brief-{slot.value.replace('_', '-')}",
        statement=f"{reason} Slot: {slot.value}.",
        source_ref=source_ref,
        subject_refs=subject_refs,
        validator_ref="validator:brief-slot-resolution",
    )


def _bounded_value(value: object) -> None:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("observation value must be finite JSON") from exc
    if len(encoded.encode("utf-8")) > 8_000:
        raise ValueError(
            "observation value is too large for operational brief state"
        )
    forbidden = {
        "document",
        "document_text",
        "full_text",
        "prompt_history",
        "raw_text",
        "transcript",
    }

    def inspect(item: object) -> None:
        if isinstance(item, dict):
            if any(str(key).lower() in forbidden for key in item):
                raise ValueError(
                    "source documents and transcripts cannot enter brief state"
                )
            for nested in item.values():
                inspect(nested)
        elif isinstance(item, list):
            for nested in item:
                inspect(nested)

    inspect(value)


def _text(value: object, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_TEXT
    ):
        raise ValueError(f"{field} must be bounded non-empty text")


def _refs(value: object, field: str) -> None:
    if not isinstance(value, tuple) or not value:
        raise ValueError(f"{field} must be a non-empty tuple")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds bounded item count")
    for item in value:
        require_logical_ref(item, f"{field} item")
    _unique(value, field)


def _typed_tuple(
    value: object,
    expected: type[object],
    field: str,
) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds bounded item count")
    if any(not isinstance(item, expected) for item in value):
        raise TypeError(f"{field} must contain {expected.__name__}")


def _unique(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains duplicates")


__all__ = [
    "BriefObservation",
    "BriefIntentObservation",
    "BriefCompilationReceipt",
    "CompiledDesignBrief",
    "compile_design_brief",
]

