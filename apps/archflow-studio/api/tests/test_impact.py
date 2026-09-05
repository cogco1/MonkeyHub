"""Impact is the kernel's closure and the record's own blindness, nothing else.

Every number asserted here comes from ``StateRecord.closure`` over
``dependency_edges``. There is no second propagation rule in the API to test,
which is the property these tests are really protecting: if this file ever needs
a case the kernel does not already answer, something has grown a mind of its own.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from archflow_studio_api.application.binding import ProjectBinding
from archflow_studio_api.application.impact import impact
from archflow_studio_api.application.projection import project_state
from archflow_studio_api.settings import StudioSettings

from .support import (
    PROJECT_ID,
    STRIPPED_RECORD_PAYLOAD,
    make_project,
    write_runner_record,
)

NO_EDGES = "0 dependency edges: impact closure is direct-only"


class ImpactTestCase(unittest.TestCase):
    """One real project, projected exactly as a request would project it."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.settings = StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID)
        self.projection = project_state(ProjectBinding.open(self.settings))

    def reproject(self) -> None:
        self.projection = project_state(ProjectBinding.open(self.settings))


class ParameterChainTests(ImpactTestCase):
    """``module → bay → span``: the chain the fixture's expressions declare."""

    def test_the_head_of_the_chain_propagates_to_everything_below_it(
        self,
    ) -> None:
        answer = impact(self.projection, "parameter:module", ())

        self.assertEqual(answer.direct, ("parameter:module",))
        self.assertEqual(
            answer.propagated, ("parameter:bay", "parameter:span")
        )
        self.assertEqual(answer.conflicts, ())

    def test_the_middle_of_the_chain_propagates_downstream_only(self) -> None:
        answer = impact(self.projection, "parameter:bay", ())

        self.assertEqual(answer.direct, ("parameter:bay",))
        self.assertEqual(answer.propagated, ("parameter:span",))

    def test_the_end_of_the_chain_propagates_to_nothing(self) -> None:
        answer = impact(self.projection, "parameter:span", ())

        self.assertEqual(answer.propagated, ())

    def test_an_unprefixed_ref_reaches_nothing_at_all(self) -> None:
        # The kernel's edges carry prefixed refs; ``module`` is not
        # ``parameter:module`` and must not silently behave like it.
        answer = impact(self.projection, "module", ())

        self.assertEqual(answer.direct, ("module",))
        self.assertEqual(answer.propagated, ())


class RelationTests(ImpactTestCase):
    """``rel-cornice-on-base``: what the record says holds up what."""

    def test_a_supporting_element_propagates_to_what_it_supports(self) -> None:
        answer = impact(self.projection, "entity:portico-base", ())

        self.assertEqual(answer.direct, ("entity:portico-base",))
        self.assertEqual(answer.propagated, ("entity:portico-cornice",))

    def test_the_supported_element_propagates_to_nothing(self) -> None:
        answer = impact(self.projection, "entity:portico-cornice", ())

        self.assertEqual(answer.propagated, ())


class ProtectionTests(ImpactTestCase):
    def test_protecting_something_downstream_is_a_conflict(self) -> None:
        answer = impact(
            self.projection, "parameter:module", ("parameter:span",)
        )

        self.assertEqual(answer.protected, ("parameter:span",))
        self.assertEqual(answer.conflicts, ("parameter:span",))

    def test_protecting_something_the_change_cannot_reach_is_not(self) -> None:
        answer = impact(
            self.projection, "parameter:span", ("parameter:module",)
        )

        self.assertEqual(answer.protected, ("parameter:module",))
        self.assertEqual(answer.conflicts, ())

    def test_protecting_the_target_itself_is_a_conflict_naming_the_target(
        self,
    ) -> None:
        # The closure includes what changed, so a protection on the target
        # collides as squarely as one downstream. One definition, not two.
        answer = impact(
            self.projection, "parameter:module", ("parameter:module",)
        )

        self.assertEqual(answer.conflicts, ("parameter:module",))

    def test_a_target_with_nothing_downstream_still_conflicts_with_itself(
        self,
    ) -> None:
        # ``portico-cornice`` propagates to nothing at all: if conflicts were read
        # off the propagation alone, this protection would vanish silently.
        answer = impact(
            self.projection,
            "entity:portico-cornice",
            ("entity:portico-cornice",),
        )

        self.assertEqual(answer.propagated, ())
        self.assertEqual(answer.conflicts, ("entity:portico-cornice",))

    def test_the_target_and_something_downstream_are_both_named(self) -> None:
        answer = impact(
            self.projection,
            "parameter:module",
            ("parameter:module", "parameter:span"),
        )

        self.assertEqual(
            answer.conflicts, ("parameter:module", "parameter:span")
        )


class LockTests(ImpactTestCase):
    def test_a_locked_parameter_in_the_closure_is_reported_with_its_authority(
        self,
    ) -> None:
        answer = impact(self.projection, "parameter:plinth", ())

        self.assertEqual(
            [(lock.ref, lock.authority) for lock in answer.locks],
            [("parameter:plinth", "client")],
        )

    def test_a_closure_that_does_not_reach_the_lock_reports_none(self) -> None:
        # the whole module -> bay -> span chain is unlocked; the lock sits on
        # plinth, which nothing in the chain reaches
        for target in ("parameter:module", "parameter:bay"):
            with self.subTest(target=target):
                answer = impact(self.projection, target, ())

                self.assertEqual(answer.locks, ())


class UnknownCoverageTests(ImpactTestCase):
    def test_components_that_appear_in_no_edge_are_named_as_unknown(
        self,
    ) -> None:
        # The fixture's edges are between elements and between parameters. No
        # edge touches a ``Component@1`` at all, so both components are
        # unknown-coverage: unknown is not the same as unaffected.
        answer = impact(self.projection, "parameter:module", ())

        self.assertEqual(answer.unknown_coverage, ("building", "portico"))
        self.assertIn(
            "2 components appear in no dependency edge; their impact is "
            "unknown, not zero",
            answer.honesty,
        )

    def test_a_record_with_edges_does_not_claim_to_be_direct_only(
        self,
    ) -> None:
        answer = impact(self.projection, "parameter:module", ())

        self.assertNotIn(NO_EDGES, answer.honesty)


class NoEdgesTests(ImpactTestCase):
    """The villa's shape today: entities, and nothing said about how they relate."""

    def setUp(self) -> None:
        super().setUp()
        write_runner_record(self.repository, STRIPPED_RECORD_PAYLOAD)
        self.reproject()

    def test_a_record_with_no_edges_says_the_closure_is_direct_only(
        self,
    ) -> None:
        answer = impact(self.projection, "entity:portico-cornice", ())

        self.assertEqual(answer.direct, ("entity:portico-cornice",))
        self.assertEqual(answer.propagated, ())
        # the elements still reference level-ground, so the kernel derives two edges and the
        # record is not edge-free; what it says instead is that the components sit in none
        self.assertNotIn(NO_EDGES, answer.honesty)
        self.assertTrue(any("appear in no dependency edge" in line for line in answer.honesty), answer.honesty)

    def test_every_component_is_unknown_coverage_when_nothing_is_declared(
        self,
    ) -> None:
        answer = impact(self.projection, "entity:portico-cornice", ())

        self.assertEqual(answer.unknown_coverage, ("building", "portico"))


if __name__ == "__main__":
    unittest.main()
