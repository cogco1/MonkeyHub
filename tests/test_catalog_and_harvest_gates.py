"""P093: the catalog on the write path, the harvest on the acceptance path."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from archflow.capabilities.component_catalog import (
    CatalogEntry,
    CatalogGateError,
    FamilyDeclination,
    FamilySelection,
    close_harvest_obligation,
    confront_catalog,
    emit_harvest_obligations,
    record_catalog_confrontation,
)
from archflow.capabilities.component_library import (
    harvest_component_template,
)
from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
)
from tests.test_component_templates import _stair_template


def _entry(
    family: str = "exterior-stair",
    ref: str = "project://demo/runs/run/records/component-template-x.json",
) -> CatalogEntry:
    return CatalogEntry(
        family=family, template_id="palladian-exterior-stair",
        template_ref=ref,
    )


class ConfrontationTests(unittest.TestCase):
    def test_unanswered_family_fails_typed(self) -> None:
        with self.assertRaises(CatalogGateError):
            confront_catalog(
                intended_families=("exterior-stair", "wall-with-openings"),
                catalog=(_entry(),),
                selections=(
                    FamilySelection(
                        family="exterior-stair",
                        template_ref=_entry().template_ref,
                    ),
                ),
            )

    def test_selection_outside_catalog_fails_typed(self) -> None:
        with self.assertRaises(CatalogGateError):
            confront_catalog(
                intended_families=("exterior-stair",),
                catalog=(_entry(),),
                selections=(
                    FamilySelection(
                        family="exterior-stair",
                        template_ref="project://elsewhere/records/x.json",
                    ),
                ),
            )

    def test_double_answer_fails_typed(self) -> None:
        with self.assertRaises(CatalogGateError):
            confront_catalog(
                intended_families=("exterior-stair",),
                catalog=(_entry(),),
                selections=(
                    FamilySelection(
                        family="exterior-stair",
                        template_ref=_entry().template_ref,
                    ),
                ),
                declinations=(
                    FamilyDeclination(
                        family="exterior-stair", reason="also declining"
                    ),
                ),
            )

    def test_stray_answer_fails_typed(self) -> None:
        with self.assertRaises(CatalogGateError):
            confront_catalog(
                intended_families=("exterior-stair",),
                catalog=(_entry(),),
                selections=(
                    FamilySelection(
                        family="exterior-stair",
                        template_ref=_entry().template_ref,
                    ),
                ),
                declinations=(
                    FamilyDeclination(
                        family="wall-with-openings",
                        reason="not intended this session",
                    ),
                ),
            )

    def test_declination_requires_reason(self) -> None:
        with self.assertRaises(CatalogGateError):
            FamilyDeclination(family="exterior-stair", reason="   ")

    def test_clean_receipt(self) -> None:
        receipt = confront_catalog(
            intended_families=("exterior-stair", "wall-with-openings"),
            catalog=(_entry(),),
            selections=(
                FamilySelection(
                    family="exterior-stair",
                    template_ref=_entry().template_ref,
                ),
            ),
            declinations=(
                FamilyDeclination(
                    family="wall-with-openings",
                    reason="no template exists yet; first build is search",
                ),
            ),
        )
        self.assertEqual(
            receipt["schema"], "CatalogConfrontationReceipt@1"
        )
        self.assertEqual(
            receipt["declined_families"], ["wall-with-openings"]
        )
        answers = {row["family"]: row for row in receipt["answers"]}
        self.assertEqual(
            answers["exterior-stair"]["answer"], "selected"
        )
        self.assertEqual(
            answers["exterior-stair"]["template_id"],
            "palladian-exterior-stair",
        )
        self.assertEqual(
            answers["wall-with-openings"]["answer"], "declined"
        )
        self.assertFalse(receipt["canonical_write_authority"])


class GatePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = FilesystemProjectRepository.initialize(
            Path(self.temporary.name) / "demo",
            project_id="demo",
            initial_state={"schema": "TestState@1"},
        )
        self.run = self.repository.create_run("run")
        self.destination = PersistenceDestination(
            PersistenceArea.RUN_RECORD, run_id="run"
        )

    def _confrontation_ref(self, **kwargs):
        payload = confront_catalog(**kwargs)
        return record_catalog_confrontation(
            self.repository,
            run=self.run,
            destination=self.destination,
            payload=payload,
        )

    def test_obligations_and_waiver_emission(self) -> None:
        source = self._confrontation_ref(
            intended_families=("exterior-stair", "roof-family", "wall-family"),
            catalog=(),
            declinations=(
                FamilyDeclination(
                    family="exterior-stair", reason="searching"
                ),
                FamilyDeclination(family="roof-family", reason="searching"),
                FamilyDeclination(family="wall-family", reason="searching"),
            ),
        )
        obligations, waivers = emit_harvest_obligations(
            self.repository,
            run=self.run,
            destination=self.destination,
            source_ref=source,
            inline_families=("exterior-stair", "roof-family", "wall-family"),
            waivers={"roof-family": "one-off ceremonial form, not reusable"},
            assigned_to="P092",
        )
        self.assertEqual(len(obligations), 2)
        self.assertEqual(len(waivers), 1)
        obligation = self.repository.load_json(obligations[0])
        self.assertEqual(obligation["status"], "OPEN")
        self.assertEqual(obligation["source_ref"], source.uri)
        self.assertEqual(obligation["assigned_to"], "P092")
        waiver = self.repository.load_json(waivers[0])
        self.assertEqual(waiver["family"], "roof-family")
        self.assertIn("not reusable", waiver["reason"])

    def test_waiver_for_unlisted_family_fails(self) -> None:
        source = self._confrontation_ref(
            intended_families=("exterior-stair",),
            catalog=(),
            declinations=(
                FamilyDeclination(
                    family="exterior-stair", reason="searching"
                ),
            ),
        )
        with self.assertRaises(CatalogGateError):
            emit_harvest_obligations(
                self.repository,
                run=self.run,
                destination=self.destination,
                source_ref=source,
                inline_families=("exterior-stair",),
                waivers={"roof-family": "stray"},
            )

    def test_closure_family_must_match(self) -> None:
        source = self._confrontation_ref(
            intended_families=("wall-family",),
            catalog=(),
            declinations=(
                FamilyDeclination(family="wall-family", reason="searching"),
            ),
        )
        obligations, _ = emit_harvest_obligations(
            self.repository,
            run=self.run,
            destination=self.destination,
            source_ref=source,
            inline_families=("wall-family",),
        )
        template = replace(
            _stair_template(),
            harvested_from_project="demo",
            harvested_from_run="run",
        )
        template_ref = harvest_component_template(
            self.repository,
            run=self.run,
            destination=self.destination,
            template=template,
        )
        with self.assertRaises(CatalogGateError):
            close_harvest_obligation(
                self.repository,
                run=self.run,
                destination=self.destination,
                obligation_ref=obligations[0],
                template_ref=template_ref,
            )


class DeclineHarvestOfferCycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = FilesystemProjectRepository.initialize(
            Path(self.temporary.name) / "demo",
            project_id="demo",
            initial_state={"schema": "TestState@1"},
        )
        self.run = self.repository.create_run("run")
        self.destination = PersistenceDestination(
            PersistenceArea.RUN_RECORD, run_id="run"
        )

    def test_decline_harvest_offer_cycle(self) -> None:
        # Session 1: empty catalog, family declined with reason.
        first = confront_catalog(
            intended_families=("exterior-stair",),
            catalog=(),
            declinations=(
                FamilyDeclination(
                    family="exterior-stair",
                    reason="no template exists yet; first build is search",
                ),
            ),
        )
        first_ref = record_catalog_confrontation(
            self.repository,
            run=self.run,
            destination=self.destination,
            payload=first,
        )
        obligations, _ = emit_harvest_obligations(
            self.repository,
            run=self.run,
            destination=self.destination,
            source_ref=first_ref,
            inline_families=("exterior-stair",),
        )
        self.assertEqual(len(obligations), 1)

        # Harvest closes the obligation.
        template = replace(
            _stair_template(),
            harvested_from_project="demo",
            harvested_from_run="run",
        )
        template_ref = harvest_component_template(
            self.repository,
            run=self.run,
            destination=self.destination,
            template=template,
        )
        closure_ref = close_harvest_obligation(
            self.repository,
            run=self.run,
            destination=self.destination,
            obligation_ref=obligations[0],
            template_ref=template_ref,
        )
        closure = self.repository.load_json(closure_ref)
        self.assertEqual(closure["obligation_ref"], obligations[0].uri)
        self.assertEqual(closure["template_ref"], template_ref.uri)

        # Session 2: the catalog now offers the harvested template and the
        # confrontation records its selection.
        second = confront_catalog(
            intended_families=("exterior-stair",),
            catalog=(
                CatalogEntry(
                    family="exterior-stair",
                    template_id=template.template_id,
                    template_ref=template_ref.uri,
                ),
            ),
            selections=(
                FamilySelection(
                    family="exterior-stair", template_ref=template_ref.uri
                ),
            ),
        )
        self.assertEqual(second["declined_families"], [])
        self.assertEqual(
            second["answers"][0]["template_ref"], template_ref.uri
        )


if __name__ == "__main__":
    unittest.main()
