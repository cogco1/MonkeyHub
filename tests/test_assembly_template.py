"""P096: building assembly templates — schema, binding, harvest, library."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from archflow.control.stage_closure import (
    CompositeStageClosureReceipt,
    StageClosureStatus,
)
from archflow.capabilities.assembly_library import (
    harvest_assembly_template,
    promote_assembly_template,
    record_assembly_binding,
    record_assembly_candidate,
    relations_from_datum_bindings,
    roles_from_design_state,
)
from archflow.project import (
    BranchRef,
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.relations.contracts import ArchitecturalRelationKind
from archflow.state.assembly_template import (
    _reject_coordinates,
    AssemblyRelation,
    AssemblyRole,
    AssemblyTemplateError,
    BuildingAssemblyTemplate,
    Cardinality,
    CheckKind,
    RequiredCheck,
    RequiredDatum,
    RoleBinding,
    bind_assembly_template,
)
from archflow.state.component_template import (
    CaseVote,
    CaseVoteKind,
    ComponentTemplateError,
    ParameterForm,
    TemplateParameter,
)
from archflow.state.design_maturity import (
    PHASE_DELIVERABLE_ROLES,
    DesignMaturityState,
    DesignPhase,
    PhaseDeliverable,
    PhaseGateRequest,
    StageEntryProof,
    evaluate_forward_phase_gate,
)
from archflow.state.geometry_program import (
    DatumBinding,
    InterfaceDatum,
    InterfaceDatumKind,
    LengthUnit,
)
from archflow.state.operational_state import DesignObligation, ObligationStatus
from archflow.state.stage_workflow import (
    ProjectStage,
    ProjectStageWorkflow,
    StageExitBinding,
    StageRunEnvelope,
)
from tests.test_sandbox_realization import compiled_room

BASIS = ("evidence:treatise-page", "project://demo/runs/run/records/receipt-aa.json")
VOTE = CaseVote(project_id="demo", run_id="run", receipt_ref="project://demo/runs/run/records/receipt-aa.json")
PHASE = DesignPhase.SCHEMATIC_DESIGN


def _hash(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _stage_records(
    project_id: str,
    run_id: str,
    *,
    branch_id: str = "main",
) -> tuple[DesignMaturityState, StageEntryProof]:
    predecessor_branch = BranchRef(
        run=RunRef(
            project_id=project_id,
            run_id=run_id,
            base=ProjectVersionRef(
                project_id=project_id,
                version=0,
                state_sha256=_hash(f"{project_id}-base"),
            ),
        ),
        branch_id=branch_id,
        epoch=0,
    )
    operational_digest = _hash(f"{project_id}-{run_id}-schematic")
    deliverables = tuple(
        PhaseDeliverable(
            deliverable_id=f"{project_id}-{role.value}",
            role=role,
            produced_phase=DesignPhase.SCHEMATIC_DESIGN,
            branch=predecessor_branch,
            base_state_digest=operational_digest,
            artifact_ref=f"artifact:{project_id}-{role.value}",
        )
        for role in sorted(
            PHASE_DELIVERABLE_ROLES[DesignPhase.SCHEMATIC_DESIGN],
            key=lambda item: item.value,
        )
    )
    predecessor = DesignMaturityState(
        branch=predecessor_branch,
        operational_state_digest=operational_digest,
        phase=DesignPhase.SCHEMATIC_DESIGN,
        deliverables=deliverables,
    )
    receipt = evaluate_forward_phase_gate(
        predecessor,
        PhaseGateRequest(
            request_id=f"{project_id}-enter-dd",
            branch=predecessor_branch,
            base_state_digest=operational_digest,
            from_phase=DesignPhase.SCHEMATIC_DESIGN,
            to_phase=DesignPhase.DESIGN_DEVELOPMENT,
            deliverable_refs=predecessor.deliverable_refs,
        ),
    )
    successor_branch = replace(predecessor_branch, epoch=1)
    proof = StageEntryProof(
        phase_gate=receipt,
        stage_exit_checkpoint_ref=ProjectRecordRef(
            project_id=project_id,
            relative_path=(
                f"runs/{run_id}/branches/{branch_id}/records/"
                "stage-entry.json"
            ),
            sha256=_hash(f"{project_id}-{run_id}-stage-entry-record"),
        ),
        stage_exit_proof_digest=_hash(
            f"{project_id}-{run_id}-stage-exit"
        ),
        predecessor_checkpoint_digest=_hash(
            f"{project_id}-{run_id}-predecessor"
        ),
        successor_checkpoint_digest=_hash(
            f"{project_id}-{run_id}-successor"
        ),
        successor_branch=successor_branch,
    )
    state = DesignMaturityState(
        branch=successor_branch,
        operational_state_digest=_hash(f"{project_id}-{run_id}-developed"),
        phase=DesignPhase.DESIGN_DEVELOPMENT,
    )
    return state, proof


def _roles():
    return (
        AssemblyRole("building", None, "whole-building", Cardinality(value=1), PHASE),
        AssemblyRole("portico", "building", "arrival-and-buttress", Cardinality(parameter="portico-count"), PHASE, indexing=()),
        AssemblyRole("portico-columns", "portico", "vertical-support", Cardinality(value=1), PHASE, required_datum_roles=("landing-top",)),
        AssemblyRole("portico-entablature", "portico", "horizontal-load-transfer", Cardinality(value=1), PHASE, required_datum_roles=("column-top",)),
        AssemblyRole("portico-landings", "portico", "stair-to-floor-interface", Cardinality(value=1), PHASE),
    )


def _template(**overrides):
    fields = dict(
        template_id="centralized-test-villa", typology="centralized-villa-with-cardinal-porticos", edition=1,
        roles=_roles(),
        relations=(
            AssemblyRelation("columns-support-entablature", "portico-columns", ArchitecturalRelationKind.SUPPORT, "portico-entablature", "column-top", BASIS),
            AssemblyRelation("landing-supports-columns", "portico-landings", ArchitecturalRelationKind.SUPPORT, "portico-columns", "landing-top", BASIS),
        ),
        datums=(
            RequiredDatum("column-top", InterfaceDatumKind.LEVEL, "portico-columns"),
            RequiredDatum("landing-top", InterfaceDatumKind.LEVEL, "portico-landings"),
        ),
        checks=(RequiredCheck("entablature-bearing", CheckKind.SUPPORT_CONTACT, ("portico-columns", "portico-entablature")),),
        parameters=(TemplateParameter.create(name="portico-count", form=ParameterForm.COUNT, value={"min": 4, "max": 4, "adopted": 4}, basis_refs=BASIS),),
        applicability=("centralized plan with cardinal porticos",), basis_refs=BASIS, case_votes=(VOTE,),
        harvested_from_project="demo", harvested_from_run="run",
    )
    fields.update(overrides)
    return BuildingAssemblyTemplate(**fields)


class SchemaTests(unittest.TestCase):
    def test_round_trip_and_digest(self) -> None:
        t = _template()
        self.assertEqual(BuildingAssemblyTemplate.from_dict(t.to_dict()).digest, t.digest)
        self.assertEqual(t.distinct_vote_projects(), ("demo",))
        self.assertEqual(t.distinct_stage_qualified_vote_projects(), ())

    def test_legacy_vote_migrates_without_promotion_weight(self) -> None:
        vote = CaseVote.from_dict(
            {
                "schema": "CaseVote@1",
                "project_id": "legacy-project",
                "run_id": "legacy-run",
                "receipt_ref": "record:legacy-binding",
            }
        )
        self.assertEqual(vote.vote_kind, CaseVoteKind.ORGANISATION_ONLY)
        self.assertFalse(vote.stage_qualified)
        self.assertEqual(vote.to_dict()["schema"], "CaseVote@2")
        with self.assertRaises(ComponentTemplateError):
            CaseVote(
                project_id="partial-project",
                run_id="partial-run",
                receipt_ref="record:partial-binding",
                vote_kind=CaseVoteKind.STAGE_QUALIFIED,
            )

    def test_refuses_coordinates(self) -> None:
        # the guard the schema runs over its own payload: no floats, no numeric vectors
        with self.assertRaises(AssemblyTemplateError):
            _reject_coordinates({"origin": [-10.71, 3.57, 0.0]}, "t", allow_float=False)
        with self.assertRaises(AssemblyTemplateError):
            _reject_coordinates({"level": 3.57}, "t", allow_float=False)
        _reject_coordinates({"band": {"min": 0.18, "max": 0.22, "adopted": 0.2}}, "t", allow_float=True)
        # a dimensionless ratio band is not a coordinate
        _template(parameters=(
            TemplateParameter.create(name="portico-count", form=ParameterForm.COUNT, value={"min": 4, "max": 4, "adopted": 4}, basis_refs=BASIS),
            TemplateParameter.create(name="entablature-to-column", form=ParameterForm.MODULE_RATIO, value={"min": 0.18, "max": 0.22, "adopted": 0.2}, basis_refs=BASIS),
        ))
        payload = _template().to_dict()
        payload["roles"][0]["function"] = 3.57
        with self.assertRaises(AssemblyTemplateError):
            _reject_coordinates(payload, "t", allow_float=False)

    def test_structural_invariants(self) -> None:
        with self.assertRaises(AssemblyTemplateError):
            _template(roles=_roles() + (AssemblyRole("orphan", "nowhere", "x", Cardinality(value=1), PHASE),))
        with self.assertRaises(AssemblyTemplateError):
            _template(relations=(AssemblyRelation("bad", "portico-columns", ArchitecturalRelationKind.SUPPORT, "ghost", None, BASIS),))
        with self.assertRaises(AssemblyTemplateError):
            _template(relations=(AssemblyRelation("bad", "portico-columns", ArchitecturalRelationKind.SUPPORT, "portico-entablature", "no-such-datum", BASIS),))
        with self.assertRaises(AssemblyTemplateError):
            _template(parameters=())  # project-derived portico count without a COUNT parameter
        with self.assertRaises(AssemblyTemplateError):
            _template(datums=(RequiredDatum("column-top", InterfaceDatumKind.LEVEL, "ghost"), RequiredDatum("landing-top", InterfaceDatumKind.LEVEL, "portico-landings")))
        with self.assertRaises(AssemblyTemplateError):
            AssemblyRole("portico", "building", "x", Cardinality(value=4), PHASE, indexing=("north", "east"))


class BindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state, _, _ = compiled_room()
        self.components = self.state.selected_schematic.option.proposal.components
        self.template = _template()
        self.count = TemplateParameter.create(name="portico-count", form=ParameterForm.COUNT, value={"min": 1, "max": 1, "adopted": 1}, basis_refs=BASIS)

    def _bindings(self, **override):
        rows = {
            "building": RoleBinding("building", component_ids=("building",)),
            "portico": RoleBinding("portico", component_ids=("building",)),
            "portico-columns": RoleBinding("portico-columns", component_ids=("primary-support",)),
            "portico-entablature": RoleBinding("portico-entablature", component_ids=("primary-surface",)),
            "portico-landings": RoleBinding("portico-landings", declined_reason="no exterior stair in this project"),
        }
        rows.update(override)
        return tuple(rows.values())

    def test_binds_with_declination_and_project_count(self) -> None:
        binding = bind_assembly_template(
            self.template, template_ref="project://lib/runs/c/records/t.json", project_id="demo", run_id="run",
            components=self.components, role_bindings=self._bindings(),
            datum_bindings=(("column-top", "room-column-top"),), parameters=(self.count,),
        )
        self.assertEqual(binding.template_digest, self.template.digest)
        self.assertEqual(len(binding.role_bindings), 5)

    def test_unbound_role_unmet_count_and_unbound_datum_fail_typed(self) -> None:
        with self.assertRaises(AssemblyTemplateError):
            bind_assembly_template(
                self.template, template_ref="t", project_id="demo", run_id="run", components=self.components,
                role_bindings=self._bindings()[:-1], datum_bindings=(("column-top", "d"),), parameters=(self.count,),
            )
        with self.assertRaises(AssemblyTemplateError):
            bind_assembly_template(
                self.template, template_ref="t", project_id="demo", run_id="run", components=self.components,
                role_bindings=self._bindings(portico=RoleBinding("portico", component_ids=("building", "primary-support"))),
                datum_bindings=(("column-top", "d"),), parameters=(self.count,),
            )
        with self.assertRaises(AssemblyTemplateError):
            bind_assembly_template(
                self.template, template_ref="t", project_id="demo", run_id="run", components=self.components,
                role_bindings=self._bindings(), datum_bindings=(), parameters=(self.count,),
            )

    def test_project_derived_indexed_role_accepts_a_subset(self) -> None:
        indexed = replace(self.template, roles=tuple(
            replace(r, indexing=("east", "north", "south", "west")) if r.role_id == "portico" else r for r in self.template.roles
        ))
        one = TemplateParameter.create(name="portico-count", form=ParameterForm.COUNT, value={"min": 1, "max": 4, "adopted": 1}, basis_refs=BASIS)
        binding = bind_assembly_template(
            indexed, template_ref="t", project_id="demo", run_id="run", components=self.components,
            role_bindings=self._bindings(portico=RoleBinding("portico", component_ids=("building",), indices=("south",))),
            datum_bindings=(("column-top", "d"),), parameters=(one,),
        )
        self.assertEqual(binding.role_bindings[1].indices, ("south",))
        with self.assertRaises(AssemblyTemplateError):
            bind_assembly_template(
                indexed, template_ref="t", project_id="demo", run_id="run", components=self.components,
                role_bindings=self._bindings(portico=RoleBinding("portico", component_ids=("building",), indices=("up",))),
                datum_bindings=(("column-top", "d"),), parameters=(one,),
            )


class HarvestAndLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state, self.program, _ = compiled_room()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.library = FilesystemProjectRepository.initialize(
            Path(self.temporary.name) / "lib", project_id="component-library", initial_state={"schema": "TestState@1"}
        )
        self.run = self.library.create_run("catalog")
        self.destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=self.run.run_id)

    def _harvest(self):
        roles = roles_from_design_state(self.state, required_datum_roles={"primary-surface": ("bearing-level",)})
        datum = InterfaceDatum.create(datum_id="room-bearing-level", kind=InterfaceDatumKind.LEVEL, published_by="floor", value=3.57, unit=LengthUnit.METER)
        relations = relations_from_datum_bindings(
            datums=(datum,), bindings=(DatumBinding("b", "room-bearing-level", "outer", "base_level"),),
            object_role={"floor": "primary-support"}, op_role={"outer": "primary-surface"},
            datum_role_of={"room-bearing-level": "bearing-level"}, basis_refs=BASIS,
        )
        return harvest_assembly_template(
            template_id="room-assembly", typology="single-room-shell", design_state=self.state, roles=roles,
            relations=relations, datums=(RequiredDatum("bearing-level", InterfaceDatumKind.LEVEL, "primary-support"),),
            checks=(RequiredCheck("shell-bearing", CheckKind.SUPPORT_CONTACT, ("primary-support", "primary-surface")),),
            parameters=(), applicability=("one-room shell",), basis_refs=BASIS,
            case_vote=CaseVote(project_id=self.state.project_id, run_id=self.state.run_id, receipt_ref=BASIS[1]),
            harvested_from_run=self.state.run_id,
        )

    def _qualified_vote(
        self,
        template: BuildingAssemblyTemplate,
        *,
        project_id: str,
        run_id: str,
    ) -> tuple[CaseVote, dict[str, dict[str, object]]]:
        state, proof = _stage_records(project_id, run_id)
        prefix = f"project://{project_id}/runs/{run_id}"
        workflow_ref = f"project://{project_id}/records/stage-workflow.json"
        receipt_ref = f"{prefix}/records/assembly-binding.json"
        state_ref = (
            f"{prefix}/branches/main/records/design-maturity.json"
        )
        proof_ref = f"{prefix}/branches/main/records/stage-entry.json"
        envelope_ref = (
            f"{prefix}/branches/main/records/stage-envelope.json"
        )
        exit_ref = f"{prefix}/branches/main/records/stage-exit.json"
        closure_ref = (
            f"{prefix}/branches/main/records/stage-closure.json"
        )
        stage = ProjectStage(
            stage_id="template-survival",
            stage_index=0,
            phase=state.phase,
            required_roles=("assembly-binding",),
            required_checks=("assembly-survival",),
            close_obligation_id="close-template-survival",
        )
        workflow = ProjectStageWorkflow(
            project_id=project_id,
            workflow_id="template-qualification",
            stages=(stage,),
            basis_refs=("decision:template-qualification",),
        )
        envelope = StageRunEnvelope(
            project_id=project_id,
            run_id=run_id,
            base_version=state.branch.run.base.version,
            base_state_sha256=state.branch.run.base.require_digest(),
            branch_id=state.branch.branch_id,
            branch_epoch=state.branch.epoch,
            subject_ref=state_ref,
            state_digest=state.state_digest,
            workflow_ref=workflow_ref,
            workflow_digest=workflow.workflow_digest,
            stage_id=stage.stage_id,
            stage_index=stage.stage_index,
            phase=stage.phase,
            required_roles=stage.required_roles,
            required_checks=stage.required_checks,
            close_obligation=DesignObligation(
                obligation_id=stage.close_obligation_id,
                statement="Close only after the assembly survives this Stage.",
                source_ref="workflow:template-qualification",
                status=ObligationStatus.OPEN,
                subject_refs=(state_ref,),
                validator_ref="validator:assembly-survival",
            ),
        )
        closure = CompositeStageClosureReceipt(
            profile_id="template-survival-profile",
            profile_digest=_hash(f"{project_id}-{run_id}-profile"),
            stage_id=stage.stage_id,
            branch=state.branch,
            stage_subject_ref=state_ref,
            subject_digest=state.state_digest,
            check_receipt_digests=(),
            findings=(),
            status=StageClosureStatus.SATISFIED,
        )
        exit_binding = StageExitBinding.bind(
            envelope,
            envelope_ref=envelope_ref,
            closure_ref=closure_ref,
            closure_digest=closure.receipt_digest,
        )
        binding = bind_assembly_template(
            template,
            template_ref="project://component-library/runs/catalog/records/template.json",
            project_id=project_id,
            run_id=run_id,
            components=self.state.selected_schematic.option.proposal.components,
            role_bindings=(
                RoleBinding("building", component_ids=("building",)),
                RoleBinding(
                    "primary-support",
                    component_ids=("primary-support",),
                ),
                RoleBinding(
                    "primary-surface",
                    component_ids=("primary-surface",),
                ),
            ),
            datum_bindings=(("bearing-level", "room-bearing-level"),),
        )
        vote = CaseVote(
            project_id=project_id,
            run_id=run_id,
            receipt_ref=receipt_ref,
            vote_kind=CaseVoteKind.STAGE_QUALIFIED,
            branch_id=state.branch.branch_id,
            branch_epoch=state.branch.epoch,
            phase=state.phase,
            workflow_ref=workflow_ref,
            workflow_digest=workflow.workflow_digest,
            stage_id=stage.stage_id,
            stage_index=stage.stage_index,
            state_ref=state_ref,
            state_digest=state.state_digest,
            stage_proof_ref=proof_ref,
            stage_proof_digest=proof.proof_digest,
            stage_envelope_ref=envelope_ref,
            envelope_digest=envelope.envelope_digest,
            stage_exit_ref=exit_ref,
            stage_exit_digest=exit_binding.exit_digest,
            stage_closure_ref=closure_ref,
            closure_digest=closure.receipt_digest,
        )
        return vote, {
            workflow_ref: workflow.to_dict(),
            receipt_ref: binding.to_dict(),
            state_ref: state.to_dict(),
            proof_ref: proof.to_dict(),
            envelope_ref: envelope.to_dict(),
            exit_ref: exit_binding.to_dict(),
            closure_ref: closure.to_dict(),
        }

    def test_harvest_is_deterministic_and_coordinate_free(self) -> None:
        first, second = self._harvest(), self._harvest()
        self.assertEqual(first.digest, second.digest)
        self.assertEqual([r.role_id for r in first.roles], ["building", "primary-support", "primary-surface"])
        self.assertEqual(first.relations[0].predicate, ArchitecturalRelationKind.SUPPORT)
        self.assertEqual(first.relations[0].datum_role, "bearing-level")
        self.assertNotIn("3.57", first.to_dict().__repr__())

    def test_candidate_carries_open_obligation_and_promotion_needs_two_votes(self) -> None:
        template = self._harvest()
        source = self.library.put_json(run=self.run, destination=self.destination, record_kind="source", payload={"x": 1})
        candidate_ref, obligation_ref = record_assembly_candidate(
            self.library, library_run=self.run, library_destination=self.destination, template=template, source_ref=source
        )
        obligation = self.library.load_json(obligation_ref)
        self.assertEqual(obligation["status"], "OPEN")
        self.assertEqual(obligation["candidate_ref"], candidate_ref.uri)
        with self.assertRaises(AssemblyTemplateError):
            promote_assembly_template(
                self.library, library_run=self.run, library_destination=self.destination, template=template, candidate_ref=candidate_ref
            )
        two_organisation_only = replace(
            template,
            case_votes=template.case_votes
            + (
                CaseVote(
                    project_id="other",
                    run_id="r",
                    receipt_ref=BASIS[1],
                ),
            ),
        )
        _, organisation_obligation_ref = record_assembly_candidate(
            self.library,
            library_run=self.run,
            library_destination=self.destination,
            template=two_organisation_only,
            source_ref=source,
        )
        self.assertEqual(
            self.library.load_json(organisation_obligation_ref)["status"],
            "OPEN",
        )
        with self.assertRaises(AssemblyTemplateError):
            promote_assembly_template(
                self.library,
                library_run=self.run,
                library_destination=self.destination,
                template=two_organisation_only,
                candidate_ref=candidate_ref,
            )

        first_vote, first_records = self._qualified_vote(
            template,
            project_id=self.state.project_id,
            run_id=self.state.run_id,
        )
        second_vote, second_records = self._qualified_vote(
            template,
            project_id="other",
            run_id="r",
        )
        qualified = replace(
            template,
            edition=2,
            case_votes=(first_vote, second_vote),
        )
        records = {**first_records, **second_records}
        with self.assertRaises(AssemblyTemplateError):
            promote_assembly_template(
                self.library,
                library_run=self.run,
                library_destination=self.destination,
                template=qualified,
                candidate_ref=candidate_ref,
            )
        stale_records = dict(records)
        stale_closure = dict(stale_records[first_vote.stage_closure_ref])
        stale_closure["status"] = "OPEN"
        stale_records[first_vote.stage_closure_ref] = stale_closure
        with self.assertRaises(AssemblyTemplateError):
            promote_assembly_template(
                self.library,
                library_run=self.run,
                library_destination=self.destination,
                template=qualified,
                candidate_ref=candidate_ref,
                record_loader=stale_records.__getitem__,
            )
        stale_exit_records = dict(records)
        stale_exit = dict(stale_exit_records[first_vote.stage_exit_ref])
        stale_exit["closure_digest"] = "f" * 64
        stale_exit_records[first_vote.stage_exit_ref] = stale_exit
        with self.assertRaises(AssemblyTemplateError):
            promote_assembly_template(
                self.library,
                library_run=self.run,
                library_destination=self.destination,
                template=qualified,
                candidate_ref=candidate_ref,
                record_loader=stale_exit_records.__getitem__,
            )
        template_ref, receipt_ref = promote_assembly_template(
            self.library,
            library_run=self.run,
            library_destination=self.destination,
            template=qualified,
            candidate_ref=candidate_ref,
            record_loader=records.__getitem__,
        )
        receipt = self.library.load_json(receipt_ref)
        self.assertEqual(
            receipt["vote_projects"],
            sorted(["other", self.state.project_id]),
        )
        self.assertEqual(receipt["schema"], "AssemblyPromotionReceipt@2")
        self.assertEqual(len(receipt["verified_votes"]), 2)
        self.assertEqual(
            self.library.load_json(template_ref)["case_votes"][0][
                "vote_kind"
            ],
            "stage_qualified",
        )

    def test_binding_record_stays_in_its_project(self) -> None:
        template = self._harvest()
        binding = bind_assembly_template(
            template, template_ref="project://lib/runs/c/records/t.json", project_id="demo", run_id="run",
            components=self.state.selected_schematic.option.proposal.components,
            role_bindings=(
                RoleBinding("building", component_ids=("building",)),
                RoleBinding("primary-support", component_ids=("primary-support",)),
                RoleBinding("primary-surface", component_ids=("primary-surface",)),
            ),
            datum_bindings=(("bearing-level", "room-bearing-level"),),
        )
        project = FilesystemProjectRepository.initialize(Path(self.temporary.name) / "demo", project_id="demo", initial_state={"schema": "TestState@1"})
        run = project.create_run("run")
        ref = record_assembly_binding(project, run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id="run"), binding=binding)
        self.assertEqual(project.load_json(ref)["template_digest"], template.digest)


if __name__ == "__main__":
    unittest.main()
