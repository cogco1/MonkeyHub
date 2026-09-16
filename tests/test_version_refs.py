"""Owner declarations of where a retained record keeps a project-version identity.

The table is a data statement by each record's owner. These tests cover the
table itself: that it is loaded explicitly rather than by whichever module a
caller happened to import, that every record kind carrying a version identity
is either declared or listed here by name with a reason, that each declaration
matches what its owner actually serialises, and that the one restater touches
exactly what was declared.
"""
from __future__ import annotations

import unittest

# Both owner tiers, loaded the way an entry point loads them. Relying on a
# sibling test having imported an owner first is the bug this module exists to
# prevent, so nothing here depends on import order.
import archflow.project.version_ref_owners as owners

owners.load_workflow_owners()
from archflow.project.record_kinds import RECORD_KINDS
from archflow.project.repository import undeclared_version_identities
from archflow.project.version_refs import (
    VersionRefDeclarationError,
    content_digest_of,
    covered_pointers,
    declared_pointers,
    declared_schemas,
    derived_fields,
    locations,
    recompute,
    recomputable_schemas,
    register,
    register_derived,
    register_structural,
    restate,
    structural_child,
)
from archflow.project.refs import BranchRef, ProjectRecordRef, ProjectVersionRef, RunRef
from archflow.adapters.cad_execution import RhinoCadProgramBinding
from archflow.state.spatial import (
    SchematicOption,
    SchematicOptionSet,
    SpatialOptionProposal,
)
import tempfile
from pathlib import Path

from archflow.project.repository import FilesystemProjectRepository
from archflow.state.operational_state import DesignObligation
from archflow.state.state_record import (
    RECORD_BINDING_PHASE,
    StateRecord,
    developed_design_view,
)
from tests.test_state_record import _record as state_record_fixture
from archflow.state.stage_workflow import (
    CompositeStageClosureReceipt,
    DesignPhase,
    StageClosureStatus,
    StageExitBinding,
    StageRunEnvelope,
)

LEGACY = "a" * 64
SEMANTIC = "b" * 64
OTHER = "c" * 64
MAPPING = {LEGACY: SEMANTIC}

# A registered record kind whose schema declares no version identity, and why.
# A kind that acquires one and is not declared makes the coverage test fail
# rather than making a migration quietly refuse the project that holds it.
NO_VERSION_IDENTITY = {
    "AuditEvent@1": "an actor/action record; names no canonical version",
    "CatalogConfrontationReceipt@1": "compares a catalog to a program; no base",
    "ComponentCatalog@1": "a library listing; no project version",
    "ComponentTemplate@1": "a library template; no project version",
    "CompiledGeometryProgram@3": "compiled from a state digest, not a canonical base",
    "DeliberationEpisode@1": "a conversation record; no canonical version",
    "DesignStage@1": "names its record by content ref, not by canonical version",
    "EvidenceLedger@1": "reserved; nothing writes it",
    "GeometryProgramProposalRecord@1": "a proposal keyed by program digest",
    "GeometryProposalEscalation@1": "escalation text and refs; no base",
    "GeometryProposalLineage@2": "lineage of program digests; no base",
    "GeometryProposalRoundReceipt@2": "a round of proposals; no base",
    "IntentCompilation@1": "compiles authored intent; no canonical version",
    "PartialAcceptanceDeferral@1": "defers a proposal; no base",
    "ProjectFormatMigration@1": "states the identity map itself; never restated",
    "ProjectGrids@1": "published grids; no base",
    "ProjectLevels@1": "published levels; no base",
    "ProjectStageWorkflow@1": "the workflow shape; bound per run by the envelope",
    "ProjectStageWorkflowFreezeReceipt@1": "freezes a workflow digest; no base",
    "ProtocolCompletion@1": "completion of a proposal round; no base",
    "RelationCheckReport@1": "check findings against a state digest",
    "RunnerRunFailure@1": "a failure report; no base",
    "SeatAuthoringContext@1": "seat inputs keyed by digests",
    "SeatHandover@1": "a handover between seats; no base",
    "SeatRoundReceipt@1": "a seat round; no base",
    "SeatSpec@1": "a seat definition; no project version",
    "StateRecordEquivalence@1": "compares two record digests; no base",
    "StudioBoardScene@1": "board layout; no canonical version",
    "StudioCandidateDelta@1": "a delta between candidate digests",
    "StudioDocumentAnnotations@1": "annotations on a document; no base",
    "StudioDocumentComment@1": "a comment; no base",
    "StudioDocumentModelSource@1": "links a document to a model asset",
    "StudioModelAnnotations@1": "annotations on a model; no base",
    "StudioModelAsset@1": "an asset by digest; no base",
    "StudioSourceDocument@1": "an uploaded document by digest; no base",
    "StudioWorkingCopy@1": "a work copy by digest; no base",
}

# A kind that carries a version identity somewhere other than its own top-level
# declaration: through a nested record its owner declares, or through a
# structural shape. It is covered, so it is not in the excuse list above, and
# the round-trip test proves it.
COVERED_INDIRECTLY = {
    "BlenderExecutionReceipt@1": "identity.binding is a RhinoCadProgramBinding@1",
    "OcctExecutionReceipt@1": "identity.binding is a RhinoCadProgramBinding@1",
    "RhinoCadExecutionReceipt@4": "identity.binding is a RhinoCadProgramBinding@1",
    "CompositeStageClosureReceipt@1": "its branch is a design BranchRef",
    "DevelopedDesignState@1": "its SelectedSchematicInput@1 holds the base",
    "RunnerRunReceipt@3": "its run is a RunRef",
    "SpatialOptionProposal@2": "identified by content; the option set holds the base",
}


class OwnerLoadingTests(unittest.TestCase):
    def test_one_module_loads_every_core_owner(self) -> None:
        """Importing the loader is enough; no incidental import is relied on."""

        self.assertIn("archflow.project.issue", owners.OWNER_MODULES)
        self.assertTrue(
            all(name.startswith("archflow.") for name in owners.OWNER_MODULES),
            "the shared core may not load a workflow module",
        )
        # PromotionDecision@1 is the case that motivated this: a command that
        # never imported project.issue would refuse every promoted project.
        self.assertEqual(declared_pointers("PromotionDecision@1"), ("/checked_state",))

    def test_a_workflow_owner_declares_itself_when_it_is_loaded(self) -> None:
        """The boundary is where the declaration is loaded, not whether it exists."""

        import monkeydiagram.drawing_elevation as drawing

        self.assertEqual(
            drawing.VERSION_REF_POINTERS,
            {"DrawingProjectionReceipt@1": ("/base", "/source/base")},
        )
        self.assertEqual(
            declared_pointers("DrawingProjectionReceipt@1"), ("/base", "/source/base"),
        )

    def test_every_registered_kind_is_declared_or_listed_with_a_reason(self) -> None:
        declared = set(declared_schemas())
        missing = []
        for kind in RECORD_KINDS.values():
            schema = kind.schema
            if schema is None or schema in declared or schema in COVERED_INDIRECTLY:
                continue
            if schema not in NO_VERSION_IDENTITY:
                missing.append((kind.kind, schema))
        self.assertEqual(missing, [], "these kinds are neither declared nor excused")
        # A kind that does carry an identity may not sit in the excuse list.
        self.assertEqual(
            sorted(set(NO_VERSION_IDENTITY) & (declared | set(COVERED_INDIRECTLY))),
            [],
            "these are excused as carrying no identity but they carry one",
        )

    def test_every_kind_that_carries_an_identity_states_what_it_derives(self) -> None:
        """Item 4's blocker: silence about a self-derived field is not a pass."""

        undeclared = sorted(
            schema for schema in set(declared_schemas()) | set(COVERED_INDIRECTLY)
            # Schemas this module registers to exercise the table itself.
            if not schema.startswith("Test") and derived_fields(schema) is None
        )
        self.assertEqual(undeclared, [], "these carry an identity and derive who knows what")


class DeclarationTableTests(unittest.TestCase):
    def test_a_schema_no_owner_declares_returns_none_not_an_empty_tuple(self) -> None:
        """Silence is not absence: a caller has to be able to tell them apart."""

        self.assertIsNone(declared_pointers("NobodyDeclaresThis@9"))
        self.assertIsNone(declared_pointers(None))

    def test_registering_one_schema_twice_differently_is_refused(self) -> None:
        register({"TestDoubleDeclared@1": ("/base",)})
        register({"TestDoubleDeclared@1": ("/base",)})  # idempotent
        with self.assertRaises(VersionRefDeclarationError):
            register({"TestDoubleDeclared@1": ("/elsewhere",)})

    def test_a_pointer_that_cannot_be_applied_is_refused_at_registration(self) -> None:
        for pointer in ("base", "", "//base"):
            with self.subTest(pointer=pointer):
                with self.assertRaises(VersionRefDeclarationError):
                    register({"TestBadPointer@1": (pointer,)})

    def test_registering_a_structural_shape_twice_differently_is_refused(self) -> None:
        register_structural(("x", "y"), "y")
        register_structural(("x", "y"), "y")
        with self.assertRaises(VersionRefDeclarationError):
            register_structural(("x", "y"), "x")


class DeclarationMatchesTheSerialisationTests(unittest.TestCase):
    """A declaration is only worth anything if the owner really writes it there."""

    def envelope(self, digest: str = LEGACY) -> StageRunEnvelope:
        return StageRunEnvelope(
            project_id="p", run_id="r", base_version=0, base_state_sha256=digest,
            branch_id="main", branch_epoch=1,
            subject_ref="project://p/runs/r/records/x.json", state_digest="d" * 64,
            workflow_ref="project://p/runs/r/records/w.json", workflow_digest="e" * 64,
            stage_id="s0", stage_index=0, phase=DesignPhase.SCHEMATIC_DESIGN,
            required_roles=("architect",), required_checks=(),
            close_obligation=DesignObligation(
                obligation_id="o1", statement="close", source_ref="ref:1",
            ),
        )

    def test_the_envelope_declares_the_pointer_its_own_to_dict_writes(self) -> None:
        payload = self.envelope().to_dict()
        self.assertEqual(
            [pointer for pointer, _, _ in locations(payload)],
            list(declared_pointers("StageRunEnvelope@1")),
        )
        self.assertEqual(locations(payload)[0][2], LEGACY)

    def test_a_declared_pointer_the_owner_does_not_write_finds_nothing(self) -> None:
        """The failure mode N4 named: a declaration that reads as decoration."""

        register({"TestDecorative@1": ("/base",)})
        self.assertEqual(locations({"schema": "TestDecorative@1", "other": 1}), [])


class EveryDeclaredKindIsBuiltByItsOwnerTests(unittest.TestCase):
    """Item 7: a declaration is worth nothing unless the owner writes it there.

    Each declared schema is serialised by the code that owns it - never by a
    literal written here - and ``locations()`` has to find exactly the pointers
    that owner declared. A declaration naming a field the owner does not write
    reads as a covered record while covering nothing, which is how a CAD
    receipt came to block every migration and a closure receipt came to pass
    one with a stale identity.
    """

    BASE = "f" * 64

    def version_ref(self) -> ProjectVersionRef:
        return ProjectVersionRef("round-trip", 3, self.BASE)

    def selected_schematic(self, base: ProjectVersionRef) -> dict:
        """A real SelectedSchematicInput@1, projected by its own owner.

        ``developed_design_view`` is the only thing that builds this graph, so
        the payload comes from there rather than from a literal written here.
        """

        record = state_record_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            repository = FilesystemProjectRepository.initialize(
                Path(temporary) / record.project_id, project_id=record.project_id,
                initial_state={"schema": "TestState@1"},
            )
            head = repository.read_head()
            repository.create_run("r")
            state = developed_design_view(
                record, run=RunRef(record.project_id, "r", head),
                evidence_ref="reading:detail-review", phase=RECORD_BINDING_PHASE,
            )
        payload = state.selected_schematic.to_dict()
        # Projected against that throwaway project's head; restate it onto the
        # base this test maps, through the owner's own declaration.
        return restate(payload, {head.require_digest(): self.BASE})

    def built(self) -> dict[str, dict]:
        """One payload per declared schema, produced the way the project does.

        Most come from the owner's own writer. Four have no constructor of
        their own - ``ProjectRun@1`` is written by
        ``FilesystemProjectRepository.create_run``, ``PromotionDecision@1`` by
        ``archflow.project.issue``, ``DrawingProjectionReceipt@1`` by
        ``monkeydiagram`` and ``ThreeDmInspectionSummary@4`` by a Rhino
        readback - so those four are the literal each of those writers emits,
        copied from it. The round-trip test in
        ``tests/test_project_format_migration.py`` puts all four through the
        real writer inside a real project.
        """

        base = self.version_ref()
        run = RunRef("round-trip", "r", base)
        obligation = DesignObligation(
            obligation_id="o1", statement="close", source_ref="ref:1",
        )
        envelope = StageRunEnvelope(
            project_id="round-trip", run_id="r", base_version=base.version,
            base_state_sha256=base.require_digest(), branch_id="main",
            branch_epoch=1, subject_ref="project://round-trip/runs/r/records/s.json",
            state_digest="d" * 64,
            workflow_ref="project://round-trip/runs/r/records/w.json",
            workflow_digest="e" * 64, stage_id="s0", stage_index=0,
            phase=DesignPhase.SCHEMATIC_DESIGN, required_roles=("architect",),
            required_checks=(), close_obligation=obligation,
        )
        exit_binding = StageExitBinding.bind(
            envelope, envelope_ref="project://round-trip/runs/r/records/e.json",
            closure_ref="project://round-trip/runs/r/reviews/c.json",
            closure_digest="a" * 64,
        )
        binding = RhinoCadProgramBinding(
            program_ref=ProjectRecordRef(
                project_id="round-trip",
                relative_path=(
                    "runs/r/branches/main/records/s0-geometry-program-"
                    + "b" * 64 + ".json"
                ),
                sha256="b" * 64,
            ),
            branch=BranchRef(run=run, branch_id="main", epoch=1),
            stage_id="s0", program_digest="c" * 64,
            design_state_digest="d" * 64, predecessor_program_digest=None,
        )
        selected = self.selected_schematic(base)
        return {
            "StateRecord@1": StateRecord(
                project_id="round-trip", run_id="r", entities=(), base=base,
            ).to_dict(),
            "ProjectRun@1": {
                "schema": "ProjectRun@1", "project_id": run.project_id,
                "run_id": run.run_id, "base": run.base.to_dict(),
            },
            "PromotionDecision@1": {
                "schema": "PromotionDecision@1", "status": "accepted",
                "project_id": "round-trip", "run_id": "r",
                "checked_state": base.to_dict(), "candidate_ref": None,
            },
            "StageRunEnvelope@1": envelope.to_dict(),
            "StageExitBinding@1": exit_binding.to_dict(),
            "RhinoCadProgramBinding@1": binding.to_dict(),
            "SelectedSchematicInput@1": selected,
            "SchematicOptionSet@1": SchematicOptionSet(
                project_id="round-trip", run_id="r", base=base,
                branch=BranchRef(run=run, branch_id="main", epoch=1),
                operational_state_digest="1" * 64,
                input_phase=DesignPhase.SITE_RESOURCE_COORDINATION,
                output_phase=DesignPhase.SCHEMATIC_DESIGN,
                program_digest="2" * 64, site_context_digest="3" * 64,
                build_policy_digest="4" * 64,
                phase_gate_receipt_ref="receipt:gate",
                phase_gate_receipt_digest="5" * 64,
                compiler_id="test-compiler", compiler_version="1",
                # Its owner requires alternatives, in id order.
                options=(
                    SchematicOption(
                        proposal=SpatialOptionProposal.from_dict({
                            **selected["option"]["proposal"],
                            "option_id": "alternative",
                        }),
                        footprint_area=selected["option"]["footprint_area"],
                        topology_signature="6" * 64,
                    ),
                    SchematicOption.from_dict(selected["option"]),
                ),
            ).to_dict(),
            "ThreeDmInspectionSummary@4": {
                "schema": "ThreeDmInspectionSummary@4",
                "document_user_strings": [
                    {"key": "archflow:base_state_sha256", "value": self.BASE},
                    {"key": "archflow:stage", "value": "s0"},
                ],
            },
            "DrawingProjectionReceipt@1": {
                "schema": "DrawingProjectionReceipt@1",
                "project_id": "round-trip", "run_id": "r",
                "base": base.to_dict(),
                "source": {"run_id": "r", "base": base.to_dict()},
            },
        }

    def test_every_declared_schema_is_found_where_its_owner_writes_it(self) -> None:
        built = self.built()
        for schema in declared_schemas():
            if schema.startswith("Test"):
                continue
            with self.subTest(schema=schema):
                self.assertIn(schema, built, "no owner-built payload for this schema")
                payload = built[schema]
                # A keyed-row declaration resolves to the concrete pointer of
                # the row it selected, so the two spellings are compared by
                # what they found rather than letter for letter.
                # A keyed-row declaration resolves to the pointer of the row
                # it selected, and a payload may also be covered structurally,
                # so the two spellings are compared by what they find: every
                # declared location has to exist, hold this base, and move.
                found = locations(payload)
                self.assertGreaterEqual(
                    len(found), len(declared_pointers(schema)),
                    f"{schema} declares a pointer its own writer does not produce: "
                    f"declared {declared_pointers(schema)}, found "
                    f"{[pointer for pointer, _, _ in found]}",
                )
                self.assertEqual([digest for _, _, digest in found],
                                 [self.BASE] * len(found))

                restated = restate(payload, {self.BASE: SEMANTIC})

                self.assertNotEqual(restated, payload)
                self.assertEqual(
                    [digest for _, _, digest in locations(restated)],
                    [SEMANTIC] * len(found),
                    f"{schema} left a declared location behind",
                )

    def test_the_closure_receipt_restates_its_own_identity(self) -> None:
        """Blocking item 1: a one-way migration used to leave this stale."""

        base = self.version_ref()
        closure = CompositeStageClosureReceipt(
            profile_id="p0", profile_digest="a" * 64, stage_id="s0",
            branch=BranchRef(run=RunRef("round-trip", "r", base), branch_id="main", epoch=1),
            stage_subject_ref="project://round-trip/runs/r/records/s.json",
            subject_digest="b" * 64, check_receipt_digests=(), findings=(),
            status=StageClosureStatus.SATISFIED,
        )
        payload = closure.to_dict()
        self.assertEqual(
            [pointer for pointer, _, _ in locations(payload)], ["/branch/base"],
        )

        restated = restate(payload, {self.BASE: SEMANTIC})

        self.assertEqual(restated["branch"]["base"]["state_sha256"], SEMANTIC)
        self.assertNotEqual(restated["receipt_digest"], payload["receipt_digest"])
        # Its own reader accepts it, which is what a later stage depends on.
        reread = CompositeStageClosureReceipt.from_dict(restated)
        self.assertEqual(reread.receipt_digest, restated["receipt_digest"])
        self.assertEqual(content_digest_of(restated), restated["receipt_digest"])


class NestedAndCanonicalPointerTests(unittest.TestCase):
    def test_a_nested_record_is_covered_by_its_own_owners_declaration(self) -> None:
        """DevelopedDesignState@1 holds no base; the input it selected does."""

        payload = {
            "schema": "DevelopedDesignState@1",
            "selected_schematic": {
                "schema": "SelectedSchematicInput@1",
                "base": {"project_id": "p", "version": 1, "state_sha256": LEGACY},
            },
        }
        self.assertEqual(
            locations(payload), [("/selected_schematic/base", "version_ref", LEGACY)],
        )
        restated = restate(payload, MAPPING)
        self.assertEqual(
            restated["selected_schematic"]["base"]["state_sha256"], SEMANTIC,
        )

    def test_a_cad_receipt_is_covered_where_its_binding_really_sits(self) -> None:
        """A declaration read off the wrong field covers nothing at all."""

        from archflow.project.repository import embedded_version_identities

        payload = {"schema": "OcctExecutionReceipt@1", "identity": {
            "schema": "OcctCadExportIdentity@1",
            "binding": {"schema": "RhinoCadProgramBinding@1",
                        "base": {"project_id": "p", "version": 1,
                                 "state_sha256": LEGACY}},
        }}
        declared = covered_pointers(payload)
        scanned = {row.json_pointer for row in embedded_version_identities(payload)}
        self.assertIn("/identity/binding/base", declared)
        self.assertTrue(scanned <= declared, scanned - declared)
        self.assertEqual(undeclared_version_identities(payload, {LEGACY: 1}), [])
        self.assertEqual(
            restate(payload, MAPPING)["identity"]["binding"]["base"]["state_sha256"],
            SEMANTIC,
        )

    def test_a_run_ref_and_a_branch_ref_carry_their_base_anywhere(self) -> None:
        self.assertEqual(structural_child({"project_id": "p", "run_id": "r", "base": {}}), "base")
        payload = {"schema": "RetiredLaneNote@1", "run": {
            "project_id": "p", "run_id": "r",
            "base": {"project_id": "p", "version": 1, "state_sha256": LEGACY},
        }, "branch": {
            "project_id": "p", "run_id": "r", "branch_id": "main", "epoch": 1,
            "base": {"version": 1, "state_sha256": LEGACY},
        }}
        self.assertEqual(
            sorted(pointer for pointer, _, _ in locations(payload)),
            ["/branch/base", "/run/base"],
        )
        restated = restate(payload, MAPPING)
        self.assertEqual(restated["run"]["base"]["state_sha256"], SEMANTIC)
        self.assertEqual(restated["branch"]["base"]["state_sha256"], SEMANTIC)


class RestateTests(unittest.TestCase):
    def test_restate_rewrites_a_declared_location_and_nothing_else(self) -> None:
        payload = {
            "schema": "StateRecord@1",
            "base": {"project_id": "p", "version": 1, "state_sha256": LEGACY},
            "undeclared": {"project_id": "p", "version": 1, "state_sha256": LEGACY},
        }
        restated = restate(payload, MAPPING)
        self.assertEqual(restated["base"]["state_sha256"], SEMANTIC)
        self.assertEqual(restated["undeclared"]["state_sha256"], LEGACY)
        self.assertEqual(payload["base"]["state_sha256"], LEGACY, "input is not mutated")

    def test_restate_leaves_a_digest_the_mapping_does_not_know(self) -> None:
        payload = {"schema": "StateRecord@1",
                   "base": {"project_id": "p", "version": 1, "state_sha256": OTHER}}
        self.assertEqual(restate(payload, MAPPING), payload)

    def test_restate_returns_a_copy_that_shares_nothing_with_its_input(self) -> None:
        payload = {"schema": "StateRecord@1",
                   "base": {"project_id": "p", "version": 1, "state_sha256": LEGACY},
                   "nested": [{"deep": {"value": 1}}]}
        restated = restate(payload, MAPPING)
        restated["nested"][0]["deep"]["value"] = 2
        self.assertEqual(payload["nested"][0]["deep"]["value"], 1)


class DerivedDigestTests(unittest.TestCase):
    """N3: a digest derived from a base is stale the moment the base moves."""

    def envelope(self, digest: str) -> dict:
        return StageRunEnvelope(
            project_id="p", run_id="r", base_version=0, base_state_sha256=digest,
            branch_id="main", branch_epoch=1,
            subject_ref="project://p/runs/r/records/x.json", state_digest="d" * 64,
            workflow_ref="project://p/runs/r/records/w.json", workflow_digest="e" * 64,
            stage_id="s0", stage_index=0, phase=DesignPhase.SCHEMATIC_DESIGN,
            required_roles=("architect",), required_checks=(),
            close_obligation=DesignObligation(
                obligation_id="o1", statement="close", source_ref="ref:1",
            ),
        ).to_dict()

    def test_an_owner_states_its_own_content_digest(self) -> None:
        payload = self.envelope(LEGACY)
        self.assertEqual(
            content_digest_of(payload),
            StageRunEnvelope.from_dict(payload).envelope_digest,
        )
        self.assertIsNone(content_digest_of({"schema": "NobodyDeclares@1"}))

    def test_restating_the_base_moves_the_owners_derived_digest(self) -> None:
        before = self.envelope(LEGACY)
        after = restate(before, MAPPING)
        self.assertEqual(after["base"]["state_sha256"], SEMANTIC)
        self.assertNotEqual(content_digest_of(after), content_digest_of(before))
        self.assertEqual(
            content_digest_of(after), content_digest_of(self.envelope(SEMANTIC)),
            "the migrated envelope digests exactly as one written at that base",
        )

    def test_only_a_kind_with_self_derived_fields_is_recomputable(self) -> None:
        self.assertEqual(
            recomputable_schemas(), ("CompositeStageClosureReceipt@1",),
        )
        self.assertEqual(derived_fields("StageRunEnvelope@1"), ())
        self.assertEqual(
            derived_fields("CompositeStageClosureReceipt@1"),
            ("receipt_id", "receipt_digest"),
        )
        payload = self.envelope(LEGACY)
        self.assertEqual(dict(recompute(payload)), payload)

    def test_naming_a_self_derived_field_without_a_rebuild_is_refused(self) -> None:
        with self.assertRaises(VersionRefDeclarationError):
            register_derived("TestDerivedNoRebuild@1", ("digest",))

    def test_a_payload_its_own_owner_cannot_read_is_not_a_missing_digest(self) -> None:
        """Item 5: swallowing this left every citation stale and unreported."""

        with self.assertRaises(VersionRefDeclarationError) as raised:
            content_digest_of({"schema": "StageRunEnvelope@1", "base": None})
        self.assertIn("StageRunEnvelope@1", str(raised.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
