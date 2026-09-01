"""Deterministic visual-inventory and component-function stage checks."""

from __future__ import annotations

from collections import Counter

from archflow.capabilities.visual_inventory import VisualInventoryStatus
from archflow.contracts.canonical import require_sha256
from archflow.control.function_diagnostics import FunctionStatus
from archflow.control.component_functions import ComponentFunctionId
from archflow.control.requirements import (
    RequirementBasisMode,
    RequirementTargetKind,
    StageCheckRequirement,
)
from archflow.control.stage_control_sources import (
    ComponentFunctionBaselineSource,
    VisualInventoryBaselineSource,
)
from archflow.control.stage_subjects import StageSubjectInventory
from archflow.validation.contracts import (
    CheckFinding,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)


def _visual_denominator(inventory: StageSubjectInventory) -> tuple[str, ...]:
    digest = inventory.visual_inventory_digest or "missing"
    return (f"visual-inventory:{digest}",)


def visual_inventory_stage_requirement(
    source: VisualInventoryBaselineSource,
    inventory: StageSubjectInventory,
) -> StageCheckRequirement:
    disposition = source.inventory.source_disposition
    return StageCheckRequirement(
        requirement_id=f"visual-inventory-{inventory.inventory_digest[:24]}",
        checker_id="visual-inventory-validator",
        target_kind=RequirementTargetKind.COMPONENT,
        basis_mode=RequirementBasisMode.AUTHORITY_BOUND,
        denominator_refs=_visual_denominator(inventory),
        required_source_refs=disposition.source_refs,
        required_authority_refs=disposition.authority_refs,
    )


def check_visual_inventory_baseline(
    source: VisualInventoryBaselineSource,
    inventory: StageSubjectInventory,
    *,
    scope_digest: str,
    subject_digest: str,
) -> CheckReceiptEnvelope:
    scope_digest = require_sha256(scope_digest, "scope_digest")
    subject_digest = require_sha256(subject_digest, "subject_digest")
    denominator = _visual_denominator(inventory)
    problems: list[tuple[str, str]] = []
    if (
        source.branch != inventory.branch
        or source.stage_id != inventory.stage_id
        or source.stage_subject_inventory_digest != inventory.inventory_digest
    ):
        problems.append(
            (
                "visual-stage-context-mismatch",
                "visual inventory source crossed the exact branch, stage, or subject inventory",
            )
        )
    if (
        inventory.visual_inventory_ref != source.inventory_ref
        or inventory.visual_inventory_digest != source.inventory.inventory_digest
    ):
        problems.append(
            (
                "visual-inventory-binding-mismatch",
                "stage subject inventory does not bind this exact visual inventory",
            )
        )
    if source.inventory.status is not VisualInventoryStatus.PASS:
        problems.append(
            (
                "visual-inventory-incomplete",
                "visual ROI coverage is incomplete",
            )
        )
    if source.inventory.unknown_questions:
        problems.append(
            (
                "visual-component-question-open",
                "visual component questions remain unresolved and require project input",
            )
        )
    entries = {item.component_id: item for item in inventory.entries}
    for accepted in source.inventory.accepted_component_identity_refs:
        entry = entries.get(accepted.proposal_component_id)
        if entry is None or entry.identity_ref != accepted.component_identity_ref:
            problems.append(
                (
                    "visual-component-join-mismatch",
                    "accepted visual hypothesis does not join one exact stage component",
                )
            )
            break
    findings = tuple(
        CheckFinding(
            code=code,
            severity=FindingSeverity.ERROR,
            message=message,
            subject_refs=(denominator[0],),
            evidence_refs=source.inventory.source_disposition.source_refs,
        )
        for code, message in sorted(set(problems))
    )
    passed = not findings
    disposition = source.inventory.source_disposition
    return CheckReceiptEnvelope(
        check_id=f"visual-inventory-{inventory.inventory_digest[:24]}",
        checker_id="visual-inventory-validator",
        checker_version="1.0.0",
        branch=inventory.branch,
        scope_digest=scope_digest,
        subject_refs=denominator,
        subject_digest=subject_digest,
        status=CheckStatus.PASS if passed else CheckStatus.FAIL,
        source_refs=disposition.source_refs,
        authority_refs=disposition.authority_refs,
        findings=findings,
        coverage_denominator=denominator,
        covered_refs=denominator if passed else (),
    )


def _function_denominator(
    inventory: StageSubjectInventory,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            f"function-evaluation:{entry.identity_ref}/{function_id.value}"
            for entry in inventory.entries
            for function_id in ComponentFunctionId
        )
    )


def _component_function_basis_refs(
    source: ComponentFunctionBaselineSource,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the exact project evidence and authority consumed by the check."""

    requirement_set = source.relation_requirements
    source_refs = tuple(
        sorted(
            {
                source.ledger_ref.uri,
                *(
                    ()
                    if source.relation_requirements_ref is None
                    else (source.relation_requirements_ref.uri,)
                ),
                *(
                    ()
                    if requirement_set is None
                    else tuple(
                        ref
                        for requirement in requirement_set.requirements
                        for ref in (
                            requirement.source_envelope_ref,
                            *requirement.rule.evidence_refs,
                        )
                    )
                ),
                *(
                    ref
                    for row in source.ledger.rows
                    for evaluation in row.evaluations
                    for ref in evaluation.evidence_refs
                ),
            }
        )
    )
    authority_refs = tuple(
        sorted(
            {
                ref
                for row in source.ledger.rows
                for evaluation in row.evaluations
                for ref in evaluation.authority_refs
            }
            | (
                set()
                if requirement_set is None
                else {
                    ref
                    for requirement in requirement_set.requirements
                    for ref in requirement.rule.authority_refs
                }
            )
        )
    )
    return source_refs, authority_refs


def component_function_stage_requirement(
    source: ComponentFunctionBaselineSource,
    inventory: StageSubjectInventory,
) -> StageCheckRequirement:
    source_refs, authority_refs = _component_function_basis_refs(source)
    return StageCheckRequirement(
        requirement_id=f"component-functions-{inventory.inventory_digest[:24]}",
        checker_id="component-function-validator",
        target_kind=RequirementTargetKind.RELATION,
        basis_mode=RequirementBasisMode.AUTHORITY_BOUND,
        denominator_refs=_function_denominator(inventory),
        required_source_refs=source_refs,
        required_authority_refs=authority_refs,
    )


def check_component_function_baseline(
    source: ComponentFunctionBaselineSource,
    inventory: StageSubjectInventory,
    *,
    scope_digest: str,
    subject_digest: str,
) -> CheckReceiptEnvelope:
    scope_digest = require_sha256(scope_digest, "scope_digest")
    subject_digest = require_sha256(subject_digest, "subject_digest")
    denominator = _function_denominator(inventory)
    problems: list[tuple[str, str, str]] = []
    ledger = source.ledger
    if (
        ledger.branch != inventory.branch
        or ledger.stage_id != inventory.stage_id
        or ledger.subject_inventory_digest != inventory.inventory_digest
    ):
        problems.append(
            (
                "function-stage-context-mismatch",
                "functional ledger crossed the exact branch, stage, or inventory",
                denominator[0],
            )
        )
    entries = {item.identity_ref: item for item in inventory.entries}
    rows = {item.component_ref: item for item in ledger.rows}
    if set(rows) != set(entries):
        problems.append(
            (
                "function-denominator-mismatch",
                "functional ledger does not exhaust the stage component denominator",
                denominator[0],
            )
        )
    for component_ref, entry in entries.items():
        row = rows.get(component_ref)
        subject_ref = (
            f"function-evaluation:{component_ref}/"
            f"{next(iter(ComponentFunctionId)).value}"
        )
        if row is None or row.component_digest != entry.component_digest:
            problems.append(
                (
                    "function-component-binding-mismatch",
                    "functional ledger component binding is missing or stale",
                    subject_ref,
                )
            )
        elif row.status is not FunctionStatus.SATISFIED:
            problems.append(
                (
                    f"function-{row.status.value.casefold().replace('_', '-')}",
                    "component functional obligations are not fully satisfied",
                    subject_ref,
                )
            )
    requirement_set = source.relation_requirements
    if requirement_set is None:
        problems.append(
            (
                "function-relation-requirements-missing",
                "satisfied function rows do not prove that relation requirements exist",
                denominator[0],
            )
        )
    else:
        if (
            requirement_set.branch != ledger.branch
            or requirement_set.stage_id != ledger.stage_id
            or requirement_set.subject_inventory_digest
            != ledger.subject_inventory_digest
        ):
            problems.append(
                (
                    "function-relation-stage-context-mismatch",
                    "function relation requirements crossed the exact branch, stage, or inventory",
                    denominator[0],
                )
            )
        if (
            requirement_set.function_ledger_ref != ledger.ledger_ref
            or requirement_set.function_ledger_digest != ledger.ledger_digest
        ):
            problems.append(
                (
                    "function-relation-ledger-binding-mismatch",
                    "function relation requirements are stale against the exact ledger",
                    denominator[0],
                )
            )
        expected = {
            (row.component_ref, evaluation.obligation_ref): (
                row.component_digest,
                evaluation.function_id,
            )
            for row in ledger.rows
            for evaluation in row.evaluations
        }
        actual_keys = tuple(
            (
                requirement.component_ref,
                requirement.functional_obligation_ref,
            )
            for requirement in requirement_set.requirements
        )
        actual_counts = Counter(actual_keys)
        for key, (component_digest, function_id) in expected.items():
            count = actual_counts.get(key, 0)
            ref = f"function-evaluation:{key[0]}/{function_id.value}"
            if count == 0:
                problems.append(
                    (
                        "function-relation-obligation-missing",
                        "applicable functional obligation has no relation requirement",
                        ref,
                    )
                )
            elif count != 1:
                problems.append(
                    (
                        "function-relation-obligation-duplicate",
                        "applicable functional obligation has duplicate relation requirements",
                        ref,
                    )
                )
            else:
                requirement = next(
                    item
                    for item in requirement_set.requirements
                    if (
                        item.component_ref,
                        item.functional_obligation_ref,
                    )
                    == key
                )
                if requirement.component_digest != component_digest:
                    problems.append(
                        (
                            "function-relation-component-binding-mismatch",
                            "function relation requirement carries a stale component digest",
                            ref,
                        )
                    )
        for key in sorted(set(actual_counts) - set(expected)):
            problems.append(
                (
                    "function-relation-obligation-foreign",
                    "function relation requirement is outside the applicable obligation denominator",
                    denominator[0],
                )
            )
    findings = tuple(
        CheckFinding(
            code=code,
            severity=FindingSeverity.ERROR,
            message=message,
            subject_refs=(ref,),
            evidence_refs=(),
        )
        for code, message, ref in sorted(set(problems))
    )
    passed = not findings
    source_refs, authority_refs = _component_function_basis_refs(source)
    return CheckReceiptEnvelope(
        check_id=f"component-functions-{inventory.inventory_digest[:24]}",
        checker_id="component-function-validator",
        checker_version="1.0.0",
        branch=inventory.branch,
        scope_digest=scope_digest,
        subject_refs=denominator,
        subject_digest=subject_digest,
        status=CheckStatus.PASS if passed else CheckStatus.FAIL,
        source_refs=source_refs,
        authority_refs=authority_refs,
        findings=findings,
        coverage_denominator=denominator,
        covered_refs=denominator if passed else (),
    )


__all__ = [
    "check_component_function_baseline",
    "check_visual_inventory_baseline",
    "component_function_stage_requirement",
    "visual_inventory_stage_requirement",
]
