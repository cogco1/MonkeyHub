"""Owner declarations of where a retained record keeps a project-version identity.

The table is a data statement by each record's owner. These tests cover the
table itself and the one generic restater: that it touches exactly what an
owner declared, that it reports nothing for a schema nobody declares, and that
two owners cannot quietly disagree about one contract.
"""
from __future__ import annotations

import unittest

# Importing an owner is what registers its declaration; nothing else does.
import archflow.adapters.cad_execution  # noqa: F401
import archflow.project.issue  # noqa: F401
import archflow.project.refs  # noqa: F401
import archflow.project.repository  # noqa: F401
import archflow.state.developed_design  # noqa: F401
import archflow.state.spatial  # noqa: F401
import archflow.state.stage_workflow  # noqa: F401
import archflow.state.state_record  # noqa: F401
from archflow.project.version_refs import (
    VersionRefDeclarationError,
    declared_pointers,
    declared_schemas,
    locations,
    register,
    register_structural,
    restate,
    structural_child,
    structural_locations,
)

LEGACY = "a" * 64
SEMANTIC = "b" * 64
OTHER = "c" * 64
MAPPING = {LEGACY: SEMANTIC}


class DeclarationTableTests(unittest.TestCase):
    def test_a_schema_no_owner_declares_returns_none_not_an_empty_tuple(self) -> None:
        """Silence is not absence: the caller has to be able to tell them apart."""

        self.assertIsNone(declared_pointers("NobodyDeclaresThis@9"))
        self.assertIsNone(declared_pointers(None))
        self.assertIsNone(declared_pointers(42))

    def test_an_owner_registers_its_declaration_by_being_imported(self) -> None:
        """No registry file lists them; the owner module states its own."""

        import archflow.state.state_record as owner

        self.assertEqual(owner.VERSION_REF_POINTERS, {"StateRecord@1": ("/base",)})
        self.assertEqual(
            declared_pointers("StateRecord@1"), owner.VERSION_REF_POINTERS["StateRecord@1"],
        )

    def test_the_owners_in_this_build_declare_what_they_write(self) -> None:
        for schema, pointers in (
            ("StateRecord@1", ("/base",)),
            ("ProjectRun@1", ("/base",)),
            ("PromotionDecision@1", ("/checked_state",)),
            ("DevelopedDesignState@1", ("/base",)),
            ("StageRunEnvelope@1", ("/base",)),
            ("StageExitBinding@1", ("/base",)),
            ("SpatialOptionProposal@2", ("/base", "/branch/base")),
            ("OcctExecutionReceipt@1",
             ("/metadata/base_version+base_state_sha256", "/binding/base")),
        ):
            with self.subTest(schema=schema):
                self.assertEqual(declared_pointers(schema), pointers)
        self.assertIn("StateRecord@1", declared_schemas())

    def test_registering_one_schema_twice_differently_is_refused(self) -> None:
        register({"TestDoubleDeclared@1": ("/base",)})
        register({"TestDoubleDeclared@1": ("/base",)})  # idempotent
        with self.assertRaises(VersionRefDeclarationError):
            register({"TestDoubleDeclared@1": ("/elsewhere",)})

    def test_a_pointer_that_cannot_be_applied_is_refused_at_registration(self) -> None:
        for pointer in ("base", "", "/a+b+c"):
            with self.subTest(pointer=pointer):
                with self.assertRaises(VersionRefDeclarationError):
                    register({"TestBadPointer@1": (pointer,)})


class LocationTests(unittest.TestCase):
    def test_locations_reads_each_declared_spelling(self) -> None:
        self.assertEqual(
            locations({"schema": "StateRecord@1",
                       "base": {"project_id": "p", "version": 1, "state_sha256": LEGACY}}),
            [("/base", "version_ref", LEGACY)],
        )
        self.assertEqual(
            locations({"schema": "SpatialOptionProposal@2",
                       "branch": {"base": {"version": 1, "state_sha256": LEGACY}}}),
            [("/branch/base", "version_digest", LEGACY)],
        )
        self.assertEqual(
            locations({"schema": "OcctExecutionReceipt@1",
                       "metadata": {"base_version": "1", "base_state_sha256": LEGACY}}),
            [("/metadata/base_version+base_state_sha256", "split_scalar", LEGACY)],
        )

    def test_an_absent_or_null_declared_location_states_nothing(self) -> None:
        for payload in (
            {"schema": "StateRecord@1"},
            {"schema": "StateRecord@1", "base": None},
            {"schema": "StateRecord@1", "base": {"version": 1}},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(locations(payload), [])

    def test_an_undeclared_schema_reports_no_location_even_when_it_has_one(self) -> None:
        self.assertEqual(
            locations({"schema": "RetiredLaneNote@1",
                       "base": {"project_id": "p", "version": 1, "state_sha256": LEGACY}}),
            [],
        )


class StructuralShapeTests(unittest.TestCase):
    def test_a_run_ref_carries_its_base_wherever_it_is_embedded(self) -> None:
        self.assertEqual(structural_child({"project_id": "p", "run_id": "r", "base": {}}), "base")
        self.assertIsNone(structural_child({"project_id": "p", "run_id": "r"}))
        payload = {"schema": "Anything@1", "run": {
            "project_id": "p", "run_id": "r",
            "base": {"project_id": "p", "version": 1, "state_sha256": LEGACY},
        }}
        self.assertEqual(structural_locations(payload), ["/run/base"])

    def test_registering_a_structural_shape_twice_differently_is_refused(self) -> None:
        register_structural(("x", "y"), "y")
        register_structural(("x", "y"), "y")
        with self.assertRaises(VersionRefDeclarationError):
            register_structural(("x", "y"), "x")


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

    def test_restate_rewrites_a_split_scalar_pair_and_a_two_key_mapping(self) -> None:
        receipt = restate(
            {"schema": "OcctExecutionReceipt@1",
             "metadata": {"base_version": "1", "base_state_sha256": LEGACY}},
            MAPPING,
        )
        self.assertEqual(receipt["metadata"]["base_state_sha256"], SEMANTIC)
        self.assertEqual(receipt["metadata"]["base_version"], "1")
        proposal = restate(
            {"schema": "SpatialOptionProposal@2",
             "branch": {"base": {"version": 1, "state_sha256": LEGACY}}},
            MAPPING,
        )
        self.assertEqual(proposal["branch"]["base"]["state_sha256"], SEMANTIC)

    def test_restate_follows_an_embedded_run_ref_in_an_undeclared_schema(self) -> None:
        """The shape's owner declared it once; no record has to repeat that."""

        payload = {"schema": "RetiredLaneNote@1", "run": {
            "project_id": "p", "run_id": "r",
            "base": {"project_id": "p", "version": 1, "state_sha256": LEGACY},
        }}
        self.assertEqual(
            restate(payload, MAPPING)["run"]["base"]["state_sha256"], SEMANTIC,
        )

    def test_restate_returns_a_copy_that_shares_nothing_with_its_input(self) -> None:
        payload = {"schema": "StateRecord@1",
                   "base": {"project_id": "p", "version": 1, "state_sha256": LEGACY},
                   "nested": [{"deep": {"value": 1}}]}
        restated = restate(payload, MAPPING)
        restated["nested"][0]["deep"]["value"] = 2
        self.assertEqual(payload["nested"][0]["deep"]["value"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
