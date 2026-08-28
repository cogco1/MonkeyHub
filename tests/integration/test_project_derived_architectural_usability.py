from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    ProjectVersionRef,
    bootstrap_raw_request_project,
)
from archflow.realization import realize_geometry
from archflow.runtime.brief_compiler import BriefObservation, compile_design_brief
from archflow.runtime.geometry_compiler import compile_geometry_program
from archflow.state import BriefClaimKind, BriefSlot, FactEpistemicStatus
from archflow.validation.architectural import (
    ArchitecturalCriterion,
    ArchitecturalObservation,
    ArchitecturalUsabilityContract,
    ArchitecturalUsabilityError,
    ArchitecturalUsabilityReceipt,
    ArchitecturalUsabilityStatus,
    AuthorizedCriterionSource,
    CriterionOperator,
    CriterionSourceKind,
    canonical_value,
    compile_architectural_usability_contract,
    evaluate_architectural_usability,
)
from tests.test_geometry_compiler import COMMITMENT, _state
from tests.test_sandbox_realization import compiled_room


PROJECT_ID = "p060-architectural-usability"
RUN_ID = "architectural-usability-001"
PROBE_ROOT = Path(__file__).resolve().parents[2] / "probes" / PROJECT_ID


def _artifact(
    *,
    project_id: str,
    run_id: str,
    base: ProjectVersionRef,
    include_asset: bool,
):
    template_state = _state()
    state = replace(
        template_state,
        selected_schematic=replace(
            template_state.selected_schematic,
            project_id=project_id,
            run_id=run_id,
            base=base,
        ),
    )
    _, template_program, payloads = compiled_room(include_asset=include_asset)
    proposal = replace(
        template_program.proposal,
        project_id=project_id,
        run_id=run_id,
        base=base,
        design_state_digest=state.state_digest,
    )
    compiled = compile_geometry_program(
        state,
        proposal,
        active_commitment_refs=(COMMITMENT,),
        available_asset_digests={
            item.asset_id: item.payload_digest for item in payloads
        },
    )
    if compiled.program is None:
        raise AssertionError(compiled.receipt.issues)
    realization = realize_geometry(
        compiled.program,
        workspace_id=f"{project_id}-sandbox",
        asset_payloads=payloads,
    )
    if realization.scene is None:
        raise AssertionError(realization.receipt.issues)
    return state, compiled.program, realization.scene, realization.receipt


def _brief(
    *,
    project_id: str,
    run_id: str,
    base: ProjectVersionRef,
    include_retrieval: bool,
    request_ref: str | None = None,
):
    request_ref = request_ref or f"project://{project_id}/input/raw-request.json"
    observations = [
        BriefObservation(
            observation_id="declared-use",
            slot=BriefSlot.USE,
            kind=BriefClaimKind.USER_FACT,
            key="declared-use",
            value="project-defined-use",
            epistemic_status=FactEpistemicStatus.DECLARED,
            authority_id="authority.user",
            source_refs=(request_ref,),
            resolves_slot=True,
        )
    ]
    if include_retrieval:
        observations.append(
            BriefObservation(
                observation_id="retrieved-rule",
                slot=BriefSlot.REGULATIONS,
                kind=BriefClaimKind.RETRIEVED_EVIDENCE,
                key="retrieved-rule",
                value={"candidate_condition": "project-defined"},
                epistemic_status=FactEpistemicStatus.HYPOTHESIS,
                authority_id="authority.retrieval-provider",
                source_refs=(
                    f"project://{project_id}/runs/{run_id}/records/retrieval.json",
                ),
                resolves_slot=False,
            )
        )
    return compile_design_brief(
        project_id=project_id,
        run_id=run_id,
        base=base,
        raw_request_ref=request_ref,
        observations=tuple(observations),
    ).brief


def _first_claim(brief, kind: BriefClaimKind):
    return next(item for item in brief.claims if item.kind is kind)


def _compile_declared_fact_contract(
    *,
    project_id: str,
    run_id: str,
    base: ProjectVersionRef,
    artifact_ref: str,
    authority_ref: str | None = None,
    request_ref: str | None = None,
    include_asset: bool = False,
):
    state, program, scene, realization = _artifact(
        project_id=project_id,
        run_id=run_id,
        base=base,
        include_asset=include_asset,
    )
    brief = _brief(
        project_id=project_id,
        run_id=run_id,
        base=base,
        include_retrieval=False,
        request_ref=request_ref,
    )
    claim = _first_claim(brief, BriefClaimKind.USER_FACT)
    source = AuthorizedCriterionSource(
        source_ref=claim.ref,
        kind=CriterionSourceKind.BRIEF_FACT,
        authority_ref=authority_ref or artifact_ref,
    )
    criterion = ArchitecturalCriterion(
        criterion_id="minimum-realized-object-count",
        measurement_key="realized-object-count",
        operator=CriterionOperator.MINIMUM,
        expected_json=canonical_value(1),
        unit="objects",
        mandatory=True,
        source_refs=(claim.ref,),
        component_ids=("building",),
        geometry_object_ids=("walls",),
        obligation_refs=(),
    )
    contract = compile_architectural_usability_contract(
        design_state=state,
        geometry_program=program,
        scene=scene,
        realization_receipt=realization,
        artifact_ref=artifact_ref,
        authorized_record_refs=(authority_ref or artifact_ref,),
        sources=(source,),
        criteria=(criterion,),
        brief=brief,
    )
    observation = ArchitecturalObservation(
        observation_id="measure-realized-object-count",
        contract_digest=contract.contract_digest,
        measurement_key=criterion.measurement_key,
        value_json=canonical_value(len(scene.objects)),
        unit="objects",
        component_ids=criterion.component_ids,
        geometry_object_ids=criterion.geometry_object_ids,
        obligation_refs=(),
        evidence_refs=(artifact_ref,),
    )
    return state, program, scene, realization, brief, contract, observation


def _compile_adopted_retrieval_contract() -> tuple[
    ArchitecturalUsabilityContract,
    ArchitecturalObservation,
]:
    project_id = "p060-retrieval-fixture"
    run_id = "relational-001"
    base = ProjectVersionRef(project_id, 2, "b" * 64)
    artifact_ref = f"project://{project_id}/runs/{run_id}/records/artifact.json"
    adoption_ref = (
        f"project://{project_id}/runs/{run_id}/records/adoption.json"
    )
    state, program, scene, realization = _artifact(
        project_id=project_id,
        run_id=run_id,
        base=base,
        include_asset=True,
    )
    brief = _brief(
        project_id=project_id,
        run_id=run_id,
        base=base,
        include_retrieval=True,
    )
    claim = _first_claim(brief, BriefClaimKind.RETRIEVED_EVIDENCE)
    criterion = ArchitecturalCriterion(
        criterion_id="adopted-opening-relation",
        measurement_key="opening-object-realized",
        operator=CriterionOperator.EQUAL,
        expected_json=canonical_value(False),
        unit=None,
        mandatory=True,
        source_refs=(claim.ref,),
        component_ids=("building",),
        geometry_object_ids=("opening",),
        obligation_refs=(),
    )
    contract = compile_architectural_usability_contract(
        design_state=state,
        geometry_program=program,
        scene=scene,
        realization_receipt=realization,
        artifact_ref=artifact_ref,
        authorized_record_refs=(adoption_ref,),
        sources=(
            AuthorizedCriterionSource(
                source_ref=claim.ref,
                kind=CriterionSourceKind.ADOPTED_RETRIEVAL,
                authority_ref=adoption_ref,
                adoption_ref=adoption_ref,
            ),
        ),
        criteria=(criterion,),
        brief=brief,
    )
    observation = ArchitecturalObservation(
        observation_id="measure-opening-object",
        contract_digest=contract.contract_digest,
        measurement_key=criterion.measurement_key,
        value_json=canonical_value("opening" in scene.opening_object_ids),
        unit=None,
        component_ids=criterion.component_ids,
        geometry_object_ids=criterion.geometry_object_ids,
        obligation_refs=(),
        evidence_refs=(artifact_ref,),
    )
    return contract, observation


def _write_probe(root: Path) -> dict[str, object]:
    if root.exists():
        raise FileExistsError(root)
    bootstrapped = bootstrap_raw_request_project(
        root,
        project_id=PROJECT_ID,
        prompt="Evaluate this artifact only against authorized project criteria.",
        run_id=RUN_ID,
        synthetic_test=False,
    )
    repository = FilesystemProjectRepository.open(root)
    run = bootstrapped.run
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )
    state, program, scene, realization = _artifact(
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        base=run.base,
        include_asset=False,
    )
    brief = _brief(
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        base=run.base,
        include_retrieval=False,
        request_ref=bootstrapped.request.uri,
    )
    input_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="architectural-usability-inputs",
        payload={
            "schema": "P060ArchitecturalUsabilityInputs@1",
            "design_state": state.to_dict(),
            "neutral_geometry_program": program.to_dict(),
            "sandbox_scene": scene.to_dict(),
            "sandbox_realization": realization.to_dict(),
            "design_brief": brief.to_dict(),
            "framework_building_answers": False,
        },
    )
    _, _, _, _, _, contract, observation = _compile_declared_fact_contract(
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        base=run.base,
        artifact_ref=input_ref.uri,
        authority_ref=bootstrapped.request.uri,
        request_ref=bootstrapped.request.uri,
    )
    receipt = evaluate_architectural_usability(contract, (observation,))
    contract_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="architectural-usability-contract",
        payload=contract.to_dict(),
    )
    observation_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="architectural-usability-observations",
        payload={
            "schema": "P060ArchitecturalObservations@1",
            "contract_digest": contract.contract_digest,
            "observations": [observation.to_dict()],
        },
    )
    receipt_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="architectural-usability-receipt",
        payload=receipt.to_dict(),
    )
    boundary = {
        "schema": "P060ArchitecturalUsabilityClaimBoundary@1",
        "project_id": PROJECT_ID,
        "run_id": RUN_ID,
        "base": {
            "project_id": run.base.project_id,
            "version": run.base.version,
            "state_sha256": run.base.require_digest(),
        },
        "input_ref": input_ref.uri,
        "contract_ref": contract_ref.uri,
        "observation_ref": observation_ref.uri,
        "receipt_ref": receipt_ref.uri,
        "artifact_presence": True,
        "architectural_usability": receipt.accepted,
        "criteria_project_derived": True,
        "retrieval_requires_adoption": True,
        "p058_scope_unchanged": "artifact-presence-only",
        "model_self_certification_authority": False,
        "canonical_write_authority": False,
    }
    boundary_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="architectural-usability-claim-boundary",
        payload=boundary,
    )
    return {**boundary, "boundary_ref": boundary_ref.uri}


class ProjectDerivedArchitecturalUsabilityTests(unittest.TestCase):
    def test_two_non_isomorphic_projects_compile_different_contracts(self) -> None:
        base = ProjectVersionRef("p060-count-fixture", 1, "a" * 64)
        _, _, _, _, _, first, first_observation = (
            _compile_declared_fact_contract(
                project_id="p060-count-fixture",
                run_id="count-001",
                base=base,
                artifact_ref=(
                    "project://p060-count-fixture/runs/count-001/records/artifact.json"
                ),
            )
        )
        second, second_observation = _compile_adopted_retrieval_contract()

        self.assertNotEqual(first.context.project_id, second.context.project_id)
        self.assertNotEqual(
            first.context.geometry_object_ids,
            second.context.geometry_object_ids,
        )
        self.assertNotEqual(
            first.criteria[0].operator,
            second.criteria[0].operator,
        )
        self.assertTrue(
            evaluate_architectural_usability(first, (first_observation,)).accepted
        )
        second_receipt = evaluate_architectural_usability(
            second,
            (second_observation,),
        )
        self.assertIs(second_receipt.status, ArchitecturalUsabilityStatus.FAILED)
        self.assertFalse(second_receipt.accepted)

    def test_stale_geometry_cannot_compile_against_current_state(self) -> None:
        project_id = "p060-stale-fixture"
        run_id = "stale-001"
        base = ProjectVersionRef(project_id, 4, "c" * 64)
        state, program, scene, realization = _artifact(
            project_id=project_id,
            run_id=run_id,
            base=base,
            include_asset=False,
        )
        changed_state = replace(
            state,
            assumption_refs=("project-record:changed-state",),
        )
        brief = _brief(
            project_id=project_id,
            run_id=run_id,
            base=base,
            include_retrieval=False,
        )
        claim = _first_claim(brief, BriefClaimKind.USER_FACT)
        artifact_ref = (
            f"project://{project_id}/runs/{run_id}/records/artifact.json"
        )
        with self.assertRaises(ArchitecturalUsabilityError):
            compile_architectural_usability_contract(
                design_state=changed_state,
                geometry_program=program,
                scene=scene,
                realization_receipt=realization,
                artifact_ref=artifact_ref,
                authorized_record_refs=(artifact_ref,),
                sources=(
                    AuthorizedCriterionSource(
                        source_ref=claim.ref,
                        kind=CriterionSourceKind.BRIEF_FACT,
                        authority_ref=artifact_ref,
                    ),
                ),
                criteria=(
                    ArchitecturalCriterion(
                        criterion_id="project-condition",
                        measurement_key="project-condition",
                        operator=CriterionOperator.EQUAL,
                        expected_json=canonical_value(True),
                        unit=None,
                        mandatory=True,
                        source_refs=(claim.ref,),
                    ),
                ),
                brief=brief,
            )

    def test_fresh_p036_probe_reloads_exact_contract_and_receipt(self) -> None:
        repository = FilesystemProjectRepository.open(PROBE_ROOT)
        run = repository.load_run(RUN_ID)
        records = repository.list_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run.run_id,
            ),
        )
        payloads = [repository.load_json(ref) for ref in records]
        contract_payload = next(
            item
            for item in payloads
            if item.get("schema") == ArchitecturalUsabilityContract.SCHEMA
        )
        receipt_payload = next(
            item
            for item in payloads
            if item.get("schema") == ArchitecturalUsabilityReceipt.SCHEMA
        )
        boundary = next(
            item
            for item in payloads
            if item.get("schema")
            == "P060ArchitecturalUsabilityClaimBoundary@1"
        )
        contract = ArchitecturalUsabilityContract.from_dict(contract_payload)
        receipt = ArchitecturalUsabilityReceipt.from_dict(receipt_payload)

        self.assertEqual(contract.contract_digest, receipt.contract_digest)
        self.assertIs(receipt.status, ArchitecturalUsabilityStatus.PASSED)
        self.assertTrue(boundary["architectural_usability"])
        self.assertEqual(
            boundary["p058_scope_unchanged"],
            "artifact-presence-only",
        )
        self.assertFalse(boundary["model_self_certification_authority"])
        self.assertFalse(boundary["canonical_write_authority"])


if __name__ == "__main__":
    unittest.main()
