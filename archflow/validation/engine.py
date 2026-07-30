"""Read-only hard gates and obligation checks."""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

from archflow.state import CanonicalState
from archflow.submission import CandidateSubmission
from archflow.validation.model import Finding, Severity, ValidationReceipt


class Validator(Protocol):
    name: str

    def validate(
        self, state: CanonicalState, submission: CandidateSubmission
    ) -> tuple[Finding, ...]: ...


class ArtifactPresentValidator:
    name = "artifact-present"

    def validate(
        self, state: CanonicalState, submission: CandidateSubmission
    ) -> tuple[Finding, ...]:
        del state
        if not submission.delta.artifacts_add:
            return (
                Finding(
                    code="artifact.missing",
                    message="submission must add at least one artifact reference",
                ),
            )
        missing_evidence = [
            artifact.artifact_id
            for artifact in submission.delta.artifacts_add
            if artifact.artifact_id not in submission.evidence_refs
        ]
        if missing_evidence:
            return (
                Finding(
                    code="artifact.evidence_missing",
                    message=f"artifact ids absent from evidence_refs: {missing_evidence}",
                ),
            )
        return ()


class RequiredClaimsValidator:
    """Compatibility/test-only gate for the original walking skeleton."""

    name = "required-claims"

    def validate(
        self, state: CanonicalState, submission: CandidateSubmission
    ) -> tuple[Finding, ...]:
        if state.goal is None:
            return (
                Finding(
                    code="compatibility.goal_contract_missing",
                    message=(
                        "RequiredClaimsValidator is compatibility-only and "
                        "cannot gate production state"
                    ),
                ),
            )
        claims = {claim.key: claim for claim in submission.claims}
        findings = []
        for required in state.goal.must:
            claim = claims.get(required)
            if claim is None:
                findings.append(
                    Finding(
                        code="goal.required_claim_missing",
                        message=f"missing required claim: {required}",
                    )
                )
            elif not claim.evidence_refs:
                findings.append(
                    Finding(
                        code="goal.claim_evidence_missing",
                        message=f"claim has no evidence: {required}",
                    )
                )
        forbidden = set(state.goal.forbid) & set(claims)
        for key in sorted(forbidden):
            findings.append(
                Finding(
                    code="goal.forbidden_claim",
                    message=f"submission asserts forbidden claim: {key}",
                )
            )
        return tuple(findings)


class AuthorizedCommitmentClaimsValidator:
    """Production claim gate compiled only from authorized hard commitments."""

    name = "authorized-commitment-claims"

    def validate(
        self, state: CanonicalState, submission: CandidateSubmission
    ) -> tuple[Finding, ...]:
        claims = {claim.key: claim for claim in submission.claims}
        findings = []
        authoritative = tuple(
            item
            for item in state.commitments
            if item.has_hard_gate_authority
        )
        for commitment in authoritative:
            criterion_id = commitment.satisfaction_criterion.criterion_id
            claim = claims.get(criterion_id)
            if claim is None:
                findings.append(
                    Finding(
                        code="commitment.required_claim_missing",
                        message=(
                            f"authorized hard commitment "
                            f"{commitment.commitment_id} requires claim "
                            f"{criterion_id}"
                        ),
                    )
                )
            elif not claim.evidence_refs:
                findings.append(
                    Finding(
                        code="commitment.claim_evidence_missing",
                        message=(
                            f"claim has no evidence for authorized hard "
                            f"commitment {commitment.commitment_id}"
                        ),
                    )
                )
        return tuple(findings)


class ObligationDischargeValidator:
    name = "obligation-discharge"

    def validate(
        self, state: CanonicalState, submission: CandidateSubmission
    ) -> tuple[Finding, ...]:
        open_ids = {item.obligation_id for item in state.open_obligations}
        unknown = set(submission.delta.obligations_discharge) - open_ids
        if not unknown:
            return ()
        return (
            Finding(
                code="obligation.unknown_discharge",
                message=f"cannot discharge unknown obligations: {sorted(unknown)}",
            ),
        )


def _receipt_id(
    state: CanonicalState,
    submission: CandidateSubmission,
    findings: tuple[Finding, ...],
) -> str:
    payload = {
        "project_id": state.ref.project_id,
        "version": state.ref.version,
        "submission_id": submission.submission_id,
        "findings": [
            [item.code, item.message, item.severity.value] for item in findings
        ],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"validation-{digest[:20]}"


def validate_submission(
    state: CanonicalState,
    submission: CandidateSubmission,
    validators: tuple[Validator, ...],
) -> ValidationReceipt:
    findings: list[Finding] = []
    if submission.base != state.ref:
        findings.append(
            Finding(
                code="state.base_mismatch",
                message=(
                    f"submission base {submission.base!r} does not match "
                    f"checked state {state.ref!r}"
                ),
                severity=Severity.ERROR,
            )
        )
    for validator in validators:
        try:
            findings.extend(validator.validate(state, submission))
        except Exception as exc:  # deterministic gate failures must fail closed
            findings.append(
                Finding(
                    code="validator.exception",
                    message=f"{validator.name}: {type(exc).__name__}: {exc}",
                )
            )
    frozen = tuple(findings)
    return ValidationReceipt(
        receipt_id=_receipt_id(state, submission, frozen),
        submission_id=submission.submission_id,
        checked_state=state.ref,
        passed=not any(item.severity is Severity.ERROR for item in frozen),
        findings=frozen,
    )
