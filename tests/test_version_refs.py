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

# The one place that loads every owner. Importing anything else to populate the
# table is the bug this module exists to prevent.
import archflow.project.version_ref_owners as owners
from archflow.project.record_kinds import RECORD_KINDS
from archflow.project.repository import undeclared_version_identities
from archflow.project.version_refs import (
    VersionRefDeclarationError,
    content_digest_of,
    covered_pointers,
    declared_pointers,
    declared_schemas,
    locations,
    recompute,
    recomputable_schemas,
    register,
    register_structural,
    restate,
    structural_child,
)
from archflow.state.operational_state import DesignObligation
from archflow.state.stage_workflow import DesignPhase, StageRunEnvelope

LEGACY = "a" * 64
SEMANTIC = "b" * 64
OTHER = "c" * 64
MAPPING = {LEGACY: SEMANTIC}

# A registered record kind whose schema declares no version identity, and why.
# A kind that acquires one and is not declared makes the coverage test fail
# rather than making a migration quietly refuse the project that holds it.
NO_VERSION_IDENTITY = {
    "AuditEvent@1": "an actor/action record; names no canonical version",
    "BlenderExecutionReceipt@1": "declared",
    "CatalogConfrontationReceipt@1": "compares a catalog to a program; no base",
    "ComponentCatalog@1": "a library listing; no project version",
    "ComponentTemplate@1": "a library template; no project version",
    "CompiledGeometryProgram@3": "compiled from a state digest, not a canonical base",
    "CompositeStageClosureReceipt@1": "identifies its stage by branch and digests",
    "DeliberationEpisode@1": "a conversation record; no canonical version",
    "DrawingProjectionReceipt@1":
        "declared by monkeydiagram.drawing_elevation, a workflow module the shared "
        "core may not import: in a core-only process it stays undeclared and a "
        "project retaining one is refused by name rather than migrated on a guess",
    "DesignStage@1": "names its record by content ref, not by canonical version",
    "DevelopedDesignState@1": "its base lives in the SelectedSchematicInput it selected",
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
    "RunnerRunReceipt@3": "cites digests its owners restate; its own run base is a RunRef",
    "SeatAuthoringContext@1": "seat inputs keyed by digests",
    "SeatHandover@1": "a handover between seats; no base",
    "SeatRoundReceipt@1": "a seat round; no base",
    "SeatSpec@1": "a seat definition; no project version",
    "SpatialOptionProposal@2": "an option identified by content; the set holds the base",
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
    "ThreeDmInspectionSummary@4": "inspects a file by digest; no canonical base",
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
            if kind.schema is None or kind.schema in declared:
                continue
            if kind.schema not in NO_VERSION_IDENTITY:
                missing.append((kind.kind, kind.schema))
        self.assertEqual(missing, [], "these kinds are neither declared nor excused")
        # The excuse list may not drift into covering a declared schema.
        self.assertEqual(
            sorted(set(NO_VERSION_IDENTITY) & declared),
            ["BlenderExecutionReceipt@1", "DrawingProjectionReceipt@1"],
            "only the CAD receipt family and the workflow-owned drawing receipt",
        )


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

    def test_a_flat_declared_digest_uses_the_same_pointer_a_scan_produces(self) -> None:
        """N2: two spellings of one location made every CAD receipt block."""

        from archflow.project.repository import embedded_version_identities

        payload = {"schema": "OcctExecutionReceipt@1",
                   "metadata": {"base_version": "1", "base_state_sha256": LEGACY}}
        declared = covered_pointers(payload)
        scanned = {row.json_pointer for row in embedded_version_identities(payload)}
        self.assertIn("/metadata/base_state_sha256", declared)
        self.assertTrue(scanned <= declared, scanned - declared)
        self.assertEqual(undeclared_version_identities(payload, {LEGACY: 1}), [])

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

    def test_an_owner_rebuilds_a_payload_that_embeds_its_own_derivation(self) -> None:
        self.assertIn("StageRunEnvelope@1", recomputable_schemas())
        payload = self.envelope(LEGACY)
        self.assertEqual(dict(recompute(payload)), payload)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
