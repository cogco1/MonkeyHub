from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectVersionRef
from archive.archflow.project.bootstrap import bootstrap_raw_request_project
from archflow.project.location import locate_project
from archive.archflow.realization.sandbox import HybridScene, SandboxRealizationReceipt, realize_geometry
from archive.archflow.runtime.family_compiler import (
    ComponentFamilyCompilationReceipt,
    ComponentFamilyLifecycleReceipt,
    ComponentFamilyRealizationReceipt,
    FamilyCompileStatus,
    FamilyInstanceDisposition,
    FamilyRealizationStatus,
    bind_component_family_realization,
    compile_component_families,
    compile_component_family_lifecycle,
)
from archflow.compilers.geometry import compile_geometry_program
from archive.archflow.runtime.semantic_geometry_lifecycle import (
    SemanticGeometryLifecycleStatus,
    compile_semantic_geometry_lifecycle,
)
from archive.archflow.state.component_family import ComponentFamilySet
from tests.test_component_family_protocol import (
    _lifecycle_family,
    _mesh_fixture,
    _parametric_fixture,
)
from tests.test_geometry_compiler import COMMITMENT
from archive.tests.test_semantic_geometry_lifecycle import (
    _design_state,
    _geometry_proposal,
)


PROJECT_ID = "p061-component-family-protocol"
PARAMETRIC_RUN = "parametric-001"
MESH_RUN = "mesh-001"
LIFECYCLE_RUN = "lifecycle-001"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROBE_ROOT = locate_project(
    PROJECT_ID,
    local_projects_root=REPOSITORY_ROOT / "probes",
).root


def _rebase_state(state, *, project_id: str, run_id: str, base: ProjectVersionRef):
    return replace(
        state,
        selected_schematic=replace(
            state.selected_schematic,
            project_id=project_id,
            run_id=run_id,
            base=base,
        ),
    )


def _rebase_case(
    *,
    project_id: str,
    run_id: str,
    base: ProjectVersionRef,
    mesh: bool,
):
    if mesh:
        template_state, template_program, payload, template_set = _mesh_fixture()
        payloads = (payload,)
    else:
        template_state, template_program, template_set = _parametric_fixture()
        payloads = ()
    state = _rebase_state(
        template_state,
        project_id=project_id,
        run_id=run_id,
        base=base,
    )
    assets = tuple(
        replace(
            item,
            uri=f"project://{project_id}/assets/{item.asset_id}",
        )
        for item in template_program.proposal.assets
    )
    proposal = replace(
        template_program.proposal,
        project_id=project_id,
        run_id=run_id,
        base=base,
        design_state_digest=state.state_digest,
        assets=assets,
    )
    compiled = compile_geometry_program(
        state,
        proposal,
        active_commitment_refs=(COMMITMENT,),
        available_asset_digests={item.asset_id: item.sha256 for item in assets},
    )
    if compiled.program is None:
        raise AssertionError(compiled.receipt.issues)
    family_set = replace(
        template_set,
        project_id=project_id,
        run_id=run_id,
        base=base,
        design_state_digest=state.state_digest,
        component_tree_digest=(
            state.selected_schematic.option.proposal.proposal_digest
        ),
        geometry_program_digest=compiled.program.program_digest,
    )
    family_receipt = compile_component_families(
        state,
        compiled.program,
        family_set,
    )
    realization = realize_geometry(
        compiled.program,
        workspace_id=f"{project_id}-{run_id}-sandbox",
        asset_payloads=payloads,
    )
    if realization.scene is None:
        raise AssertionError(realization.receipt.issues)
    family_realization = bind_component_family_realization(
        family_receipt,
        realization.scene,
        realization.receipt,
    )
    return (
        state,
        compiled.program,
        family_set,
        family_receipt,
        realization.scene,
        realization.receipt,
        family_realization,
        payloads,
    )


def _lifecycle_case(*, project_id: str, run_id: str, base: ProjectVersionRef):
    before_state = _rebase_state(
        _design_state(0),
        project_id=project_id,
        run_id=run_id,
        base=base,
    )
    before_geometry = compile_geometry_program(
        before_state,
        _geometry_proposal(before_state, stage=0),
        active_commitment_refs=(COMMITMENT,),
    )
    if before_geometry.program is None:
        raise AssertionError(before_geometry.receipt.issues)
    before_program = before_geometry.program
    after_state = _rebase_state(
        _design_state(1),
        project_id=project_id,
        run_id=run_id,
        base=base,
    )
    semantic = compile_semantic_geometry_lifecycle(
        transaction_id="p061-family-revision",
        predecessor_state=before_state,
        current_state=after_state,
        predecessor_proposal=(
            before_state.selected_schematic.option.proposal
        ),
        current_proposal=after_state.selected_schematic.option.proposal,
        prior_program=before_program,
        geometry_proposal=_geometry_proposal(
            after_state,
            before_program,
            stage=1,
        ),
        active_commitment_refs=(COMMITMENT,),
    )
    if semantic.geometry_program is None:
        raise AssertionError(semantic.receipt.issues)
    before_set = _lifecycle_family(before_state, before_program, revision=1)
    after_set = _lifecycle_family(
        after_state,
        semantic.geometry_program,
        revision=2,
        predecessor=before_set.instances[0].instance_digest,
    )
    before_receipt = compile_component_families(
        before_state,
        before_program,
        before_set,
    )
    after_receipt = compile_component_families(
        after_state,
        semantic.geometry_program,
        after_set,
    )
    family_lifecycle = compile_component_family_lifecycle(
        before_set,
        after_set,
        before_receipt,
        after_receipt,
        semantic.receipt,
    )
    return (
        before_state,
        before_program,
        before_set,
        before_receipt,
        after_state,
        semantic.geometry_program,
        after_set,
        after_receipt,
        semantic.receipt,
        family_lifecycle,
    )


def _replacement_case(*, project_id: str, run_id: str, base: ProjectVersionRef):
    values = _lifecycle_case(
        project_id=project_id,
        run_id=run_id,
        base=base,
    )
    (
        before_state,
        before_program,
        before_set,
        before_receipt,
        after_state,
        after_program,
        after_set,
        _,
        semantic_receipt,
        _,
    ) = values
    predecessor = before_set.instances[0]
    replacement_instance = replace(
        after_set.instances[0],
        family_id="project-authored-replacement-family",
        family_revision=1,
        definition_digest="e" * 64,
        predecessor_instance_digest=predecessor.instance_digest,
    )
    replacement_set = replace(after_set, instances=(replacement_instance,))
    replacement_compilation = compile_component_families(
        after_state,
        after_program,
        replacement_set,
    )
    replacement_lifecycle = compile_component_family_lifecycle(
        before_set,
        replacement_set,
        before_receipt,
        replacement_compilation,
        semantic_receipt,
    )
    return (
        before_state,
        before_program,
        before_set,
        before_receipt,
        after_state,
        after_program,
        replacement_set,
        replacement_compilation,
        semantic_receipt,
        replacement_lifecycle,
    )


def _persist_case(repository, run, *, mesh: bool) -> dict[str, object]:
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )
    (
        state,
        program,
        family_set,
        family_receipt,
        scene,
        realization,
        family_realization,
        payloads,
    ) = _rebase_case(
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
        mesh=mesh,
    )
    inputs = repository.put_json(
        run=run,
        destination=destination,
        record_kind="component-family-inputs",
        payload={
            "schema": "P061ComponentFamilyInputs@1",
            "design_state": state.to_dict(),
            "geometry_program": program.to_dict(),
            "family_set": family_set.to_dict(),
            "asset_payloads": [item.to_dict() for item in payloads],
            "component_tree_authority": False,
        },
    )
    compiled = repository.put_json(
        run=run,
        destination=destination,
        record_kind="component-family-compilation",
        payload=family_receipt.to_dict(),
    )
    sandbox = repository.put_json(
        run=run,
        destination=destination,
        record_kind="component-family-sandbox",
        payload={
            "schema": "P061ComponentFamilySandboxEvidence@1",
            "scene": scene.to_dict(),
            "realization": realization.to_dict(),
        },
    )
    family_realization_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="component-family-realization-receipt",
        payload=family_realization.to_dict(),
    )
    realization_boundary = {
        "schema": "P061ComponentFamilyRealizationClaimBoundary@1",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "family_compilation_receipt_digest": family_receipt.receipt_digest,
        "sandbox_realization_receipt_digest": realization.receipt_digest,
        "family_realization_receipt_digest": family_realization.receipt_digest,
        "family_realization_receipt_ref": family_realization_ref.uri,
        "scene_digest": scene.scene_digest,
        "exact_scene_binding": (
            family_realization.status is FamilyRealizationStatus.REALIZED
        ),
        "external_platform_required": False,
        "geometry_mutation_authority": False,
        "canonical_write_authority": False,
    }
    realization_boundary_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="component-family-realization-claim-boundary",
        payload=realization_boundary,
    )
    boundary = {
        "schema": "P061ComponentFamilyClaimBoundary@1",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "family_kind": family_set.instances[0].kind.value,
        "input_ref": inputs.uri,
        "compilation_ref": compiled.uri,
        "sandbox_ref": sandbox.uri,
        "family_set_digest": family_set.family_set_digest,
        "geometry_program_digest": program.program_digest,
        "scene_digest": scene.scene_digest,
        "family_compiled": family_receipt.status is FamilyCompileStatus.COMPILED,
        "sandbox_realized": True,
        "external_mesh_internally_parametric": False,
        "component_tree_authority": False,
        "platform_exporter_claimed": False,
        "canonical_write_authority": False,
    }
    boundary_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="component-family-claim-boundary",
        payload=boundary,
    )
    return {
        **boundary,
        "boundary_ref": boundary_ref.uri,
        "realization_boundary_ref": realization_boundary_ref.uri,
    }


def _persist_lifecycle(repository, run) -> dict[str, object]:
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )
    values = _lifecycle_case(
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
    )
    (
        before_state,
        before_program,
        before_set,
        before_receipt,
        after_state,
        after_program,
        after_set,
        after_receipt,
        semantic_receipt,
        family_lifecycle,
    ) = values
    inputs = repository.put_json(
        run=run,
        destination=destination,
        record_kind="component-family-lifecycle-inputs",
        payload={
            "schema": "P061ComponentFamilyLifecycleInputs@1",
            "predecessor_design_state": before_state.to_dict(),
            "predecessor_geometry_program": before_program.to_dict(),
            "predecessor_family_set": before_set.to_dict(),
            "predecessor_family_compilation": before_receipt.to_dict(),
            "current_design_state": after_state.to_dict(),
            "current_geometry_program": after_program.to_dict(),
            "current_family_set": after_set.to_dict(),
            "current_family_compilation": after_receipt.to_dict(),
            "semantic_geometry_lifecycle": semantic_receipt.to_dict(),
        },
    )
    receipt_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="component-family-lifecycle-receipt",
        payload=family_lifecycle.to_dict(),
    )
    boundary = {
        "schema": "P061ComponentFamilyLifecycleClaimBoundary@1",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "inputs_ref": inputs.uri,
        "receipt_ref": receipt_ref.uri,
        "semantic_lifecycle_receipt_digest": semantic_receipt.receipt_digest,
        "family_lifecycle_receipt_digest": family_lifecycle.receipt_digest,
        "disposition": family_lifecycle.transitions[0].disposition.value,
        "local_exact_predecessor": True,
        "successor_authority": False,
        "canonical_write_authority": False,
    }
    boundary_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="component-family-lifecycle-claim-boundary",
        payload=boundary,
    )
    return {**boundary, "boundary_ref": boundary_ref.uri}


def _persist_replacement(repository, run) -> dict[str, object]:
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )
    (
        before_state,
        before_program,
        before_set,
        before_receipt,
        after_state,
        after_program,
        replacement_set,
        replacement_compilation,
        semantic_receipt,
        replacement_lifecycle,
    ) = _replacement_case(
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
    )
    inputs = repository.put_json(
        run=run,
        destination=destination,
        record_kind="component-family-replacement-inputs",
        payload={
            "schema": "P061ComponentFamilyReplacementInputs@1",
            "predecessor_design_state": before_state.to_dict(),
            "predecessor_geometry_program": before_program.to_dict(),
            "predecessor_family_set": before_set.to_dict(),
            "predecessor_family_compilation": before_receipt.to_dict(),
            "current_design_state": after_state.to_dict(),
            "current_geometry_program": after_program.to_dict(),
            "current_family_set": replacement_set.to_dict(),
            "current_family_compilation": replacement_compilation.to_dict(),
            "semantic_geometry_lifecycle": semantic_receipt.to_dict(),
        },
    )
    receipt_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="component-family-replacement-receipt",
        payload=replacement_lifecycle.to_dict(),
    )
    boundary = {
        "schema": "P061ComponentFamilyReplacementClaimBoundary@1",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "inputs_ref": inputs.uri,
        "receipt_ref": receipt_ref.uri,
        "semantic_lifecycle_receipt_digest": semantic_receipt.receipt_digest,
        "family_lifecycle_receipt_digest": replacement_lifecycle.receipt_digest,
        "disposition": replacement_lifecycle.transitions[0].disposition.value,
        "exact_predecessor": True,
        "successor_authority": False,
        "canonical_write_authority": False,
    }
    boundary_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="component-family-replacement-claim-boundary",
        payload=boundary,
    )
    return {**boundary, "boundary_ref": boundary_ref.uri}


def _write_probe(root: Path) -> dict[str, object]:
    if root.exists():
        raise FileExistsError(root)
    bootstrap = bootstrap_raw_request_project(
        root,
        project_id=PROJECT_ID,
        prompt=(
            "Validate project-authored parametric and immutable mesh families "
            "against exact semantic components and neutral geometry."
        ),
        run_id=PARAMETRIC_RUN,
        synthetic_test=False,
    )
    repository = FilesystemProjectRepository.open(root)
    mesh_run = repository.create_run(MESH_RUN, base=bootstrap.run.base)
    lifecycle_run = repository.create_run(LIFECYCLE_RUN, base=bootstrap.run.base)
    parametric = _persist_case(repository, bootstrap.run, mesh=False)
    mesh = _persist_case(repository, mesh_run, mesh=True)
    lifecycle = _persist_lifecycle(repository, lifecycle_run)
    replacement = _persist_replacement(repository, lifecycle_run)
    repository.verify()
    return {
        "parametric": parametric,
        "mesh": mesh,
        "lifecycle": lifecycle,
        "replacement": replacement,
    }


class ComponentFamilyIntegrationTests(unittest.TestCase):
    def test_two_non_isomorphic_projects_realize_without_platform_export(self) -> None:
        first = _rebase_case(
            project_id="p061-parametric-project",
            run_id="parametric-001",
            base=ProjectVersionRef("p061-parametric-project", 1, "a" * 64),
            mesh=False,
        )
        second = _rebase_case(
            project_id="p061-mesh-project",
            run_id="mesh-001",
            base=ProjectVersionRef("p061-mesh-project", 7, "b" * 64),
            mesh=True,
        )

        self.assertIs(first[3].status, FamilyCompileStatus.COMPILED)
        self.assertIs(second[3].status, FamilyCompileStatus.COMPILED)
        self.assertIs(first[6].status, FamilyRealizationStatus.REALIZED)
        self.assertIs(second[6].status, FamilyRealizationStatus.REALIZED)
        self.assertNotEqual(first[0].project_id, second[0].project_id)
        self.assertNotEqual(len(first[1].objects), len(second[1].objects))
        self.assertFalse(first[2].to_dict()["component_tree_authority"])
        self.assertFalse(second[2].to_dict()["component_tree_authority"])

    def test_family_revision_uses_real_p055_lifecycle(self) -> None:
        values = _lifecycle_case(
            project_id="p061-lifecycle-project",
            run_id="lifecycle-001",
            base=ProjectVersionRef("p061-lifecycle-project", 2, "c" * 64),
        )
        semantic_receipt = values[-2]
        family_receipt = values[-1]

        self.assertIs(
            semantic_receipt.status,
            SemanticGeometryLifecycleStatus.COMPILED,
        )
        self.assertIs(family_receipt.status, FamilyCompileStatus.COMPILED)
        self.assertIs(
            family_receipt.transitions[0].disposition,
            FamilyInstanceDisposition.REVISED,
        )
        self.assertEqual(
            family_receipt.semantic_lifecycle_receipt_digest,
            semantic_receipt.receipt_digest,
        )

    def test_promoted_p036_probe_reloads_all_family_evidence(self) -> None:
        repository = FilesystemProjectRepository.open(PROBE_ROOT)
        boundaries: list[dict[str, object]] = []
        realization_boundaries: list[dict[str, object]] = []
        replacement_boundaries: list[dict[str, object]] = []
        for run_id in (PARAMETRIC_RUN, MESH_RUN, LIFECYCLE_RUN):
            run = repository.load_run(run_id)
            records = repository.list_json(
                run=run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=run.run_id,
                ),
            )
            payloads = [repository.load_json(item) for item in records]
            boundaries.extend(
                item
                for item in payloads
                if item.get("schema")
                in {
                    "P061ComponentFamilyClaimBoundary@1",
                    "P061ComponentFamilyLifecycleClaimBoundary@1",
                }
            )
            realization_boundaries.extend(
                item
                for item in payloads
                if item.get("schema")
                == "P061ComponentFamilyRealizationClaimBoundary@1"
            )
            replacement_boundaries.extend(
                item
                for item in payloads
                if item.get("schema")
                == "P061ComponentFamilyReplacementClaimBoundary@1"
            )
            for item in payloads:
                if item.get("schema") == "P061ComponentFamilyInputs@1":
                    ComponentFamilySet.from_dict(item["family_set"])
                elif item.get("schema") == ComponentFamilyCompilationReceipt.SCHEMA:
                    ComponentFamilyCompilationReceipt.from_dict(item)
                elif item.get("schema") == ComponentFamilyLifecycleReceipt.SCHEMA:
                    ComponentFamilyLifecycleReceipt.from_dict(item)
                elif item.get("schema") == ComponentFamilyRealizationReceipt.SCHEMA:
                    ComponentFamilyRealizationReceipt.from_dict(item)
                elif item.get("schema") == "P061ComponentFamilySandboxEvidence@1":
                    HybridScene.from_dict(item["scene"])
                    SandboxRealizationReceipt.from_dict(item["realization"])
                elif item.get("schema") == "P061ComponentFamilyLifecycleInputs@1":
                    ComponentFamilySet.from_dict(item["predecessor_family_set"])
                    ComponentFamilySet.from_dict(item["current_family_set"])
                    ComponentFamilyCompilationReceipt.from_dict(
                        item["predecessor_family_compilation"]
                    )
                    ComponentFamilyCompilationReceipt.from_dict(
                        item["current_family_compilation"]
                    )
                elif item.get("schema") == "P061ComponentFamilyReplacementInputs@1":
                    ComponentFamilySet.from_dict(item["predecessor_family_set"])
                    ComponentFamilySet.from_dict(item["current_family_set"])
                    ComponentFamilyCompilationReceipt.from_dict(
                        item["predecessor_family_compilation"]
                    )
                    ComponentFamilyCompilationReceipt.from_dict(
                        item["current_family_compilation"]
                    )
        repository.verify()

        self.assertEqual(3, len(boundaries))
        self.assertEqual(2, len(realization_boundaries))
        self.assertEqual(1, len(replacement_boundaries))
        self.assertTrue(
            all(item["exact_scene_binding"] is True for item in realization_boundaries)
        )
        self.assertTrue(
            all(item["canonical_write_authority"] is False for item in boundaries)
        )
        family_modes = {
            item["family_kind"] for item in boundaries if "family_kind" in item
        }
        self.assertEqual(
            {"parametric_assembly", "external_mesh"},
            family_modes,
        )
        lifecycle = next(item for item in boundaries if "disposition" in item)
        self.assertEqual("revised", lifecycle["disposition"])
        self.assertEqual("replaced", replacement_boundaries[0]["disposition"])

    def test_framework_has_no_family_instance_or_platform_answer(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source = "\n".join(
            (root / relative).read_text(encoding="utf-8").lower()
            for relative in (
                "archive/archflow/state/component_family.py",
                "archive/archflow/runtime/family_compiler.py",
            )
        )
        forbidden = ("pantheon", "minecraft", "rhino", "revit")
        self.assertFalse(any(item in source for item in forbidden))


if __name__ == "__main__":
    unittest.main()
