"""P036 readback of public production massing options, with exact caller binding."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STATE_RECORD
from archflow.project.refs import ProjectVersionRef, RunRef
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import Entity, StateRecord

from .evaluator import MassingEvaluator, dominates
from .retained import RetainedEvaluationUnavailable, evaluate_retained
from .retained_fixture import CONTEXT, ENVELOPE, EVIDENCE, PROJECT_ID, create_public_massing_fixture


class RetainedEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name) / PROJECT_ID
        cls.candidates = create_public_massing_fixture(cls.root)
        cls.repository = FilesystemProjectRepository.open(cls.root)

    def setUp(self):
        self.candidate = self.candidates[0]
        self.evaluator = MassingEvaluator(ENVELOPE)

    def evaluate(self, *, candidate=None, **overrides):
        candidate = candidate or self.candidate
        kwargs = dict(expected_run=candidate.request.expected_run,
                      expected_content_digest=candidate.request.expected_content_digest,
                      context_refs=candidate.request.context_refs, evaluator=self.evaluator)
        kwargs.update(overrides)
        return evaluate_retained(self.repository, candidate.record_ref, **kwargs)

    def retain(self, record, *, payload=None):
        run = self.repository.create_run(self.id().split(".")[-1])
        bound = record.bound_to(run)
        ref = self.repository.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
            record_kind=STATE_RECORD, payload=bound.to_dict() if payload is None else payload,
        )
        return run, bound, ref

    def test_24_production_options_reopen_with_hand_calculated_vectors(self):
        self.assertEqual(len(self.candidates), 24)
        self.assertEqual(len({item.request.expected_content_digest for item in self.candidates}), 24)
        for candidate in self.candidates:
            width, depth, floors = (int(part[1:]) for part in candidate.name.split("-"))
            with self.subTest(candidate=candidate.name):
                self.assertEqual(candidate.result.validity, "valid")
                self.assertEqual([item.value for item in candidate.result.objectives],
                                 [width * depth, width * depth * floors, floors, floors * 3])
                self.assertEqual(self.evaluate(candidate=candidate), candidate.result)
                self.assertIn(candidate.record_ref.uri, candidate.result.evidence_refs)
                self.assertEqual(candidate.request.expected_run, candidate.request.record.run_ref)
                self.assertEqual(candidate.transforms[0]["transform"], "scale_volume")
                if floors == 2:
                    self.assertEqual(candidate.transforms[1]["transform"], "add_floor")

    def test_read_only_replay_changes_no_project_file(self):
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with patch.object(self.repository, "put_json", side_effect=AssertionError("read attempted write")), \
             patch.object(self.repository, "create_run", side_effect=AssertionError("read attempted run creation")), \
             patch.object(self.repository, "compare_and_swap", side_effect=AssertionError("read attempted promotion")):
            self.assertEqual(self.evaluate(), self.candidate.result)
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_fixed_candidate_generation_replays_identical_saved_bindings(self):
        with TemporaryDirectory() as temp:
            repeated = create_public_massing_fixture(Path(temp) / PROJECT_ID)
        self.assertEqual(repeated, self.candidates)

    def test_stale_expected_base_is_invalid_and_keeps_observed_source(self):
        expected = self.candidate.request.expected_run
        wrong_base = ProjectVersionRef(PROJECT_ID, expected.base.version + 1, "f" * 64)
        result = self.evaluate(expected_run=RunRef(PROJECT_ID, expected.run_id, wrong_base))
        self.assertEqual(result.validity, "invalid")
        self.assertEqual(result.run.base, wrong_base)
        self.assertEqual(result.observed_run, expected)
        self.assertTrue(all(item.value is None for item in result.objectives))
        with self.assertRaisesRegex(ValueError, "valid candidates"):
            dominates(result, self.candidate.result)

    def test_wrong_expected_content_and_run_are_never_replaced_from_record(self):
        overrides = ({"expected_content_digest": "0" * 64},
                     {"expected_run": self.candidates[1].request.expected_run})
        for kwargs in overrides:
            with self.subTest(kwargs=kwargs):
                result = self.evaluate(**kwargs)
                self.assertEqual(result.validity, "invalid")
                self.assertEqual(result.observed_candidate_digest, self.candidate.request.record.digest)
                self.assertTrue(all(item.value is None for item in result.objectives))

    def test_context_is_required_and_preserved_from_caller(self):
        context = ("fixture:another-explicit-context",)
        self.assertEqual(self.evaluate(context_refs=context).context_refs, context)
        with self.assertRaisesRegex(ValueError, "explicit context"):
            self.evaluate(context_refs=())

    def test_missing_record_is_unavailable_not_zero_or_invalid(self):
        ref = replace(self.candidate.record_ref,
                      relative_path=f"runs/{self.candidate.request.expected_run.run_id}/records/state-record-{'0' * 64}.json",
                      sha256="0" * 64)
        with self.assertRaisesRegex(RetainedEvaluationUnavailable, "unavailable"):
            evaluate_retained(self.repository, ref, expected_run=self.candidate.request.expected_run,
                              expected_content_digest=self.candidate.request.expected_content_digest,
                              context_refs=CONTEXT, evaluator=self.evaluator)

    def test_corrupt_bytes_are_rejected_by_p036(self):
        run, record, ref = self.retain(self.candidate.request.record)
        self.repository.layout.resolve_record(ref).write_bytes(b'{"corrupt":true}\n')
        with self.assertRaisesRegex(RetainedEvaluationUnavailable, "digest mismatch"):
            evaluate_retained(self.repository, ref, expected_run=run, expected_content_digest=record.digest,
                              context_refs=CONTEXT, evaluator=self.evaluator)

    def test_unsupported_retained_schema_is_explicit(self):
        run, record, ref = self.retain(self.candidate.request.record, payload={"schema": "ForeignCAD@1", "bbox": [0, 1]})
        with self.assertRaisesRegex(RetainedEvaluationUnavailable, "unavailable"):
            evaluate_retained(self.repository, ref, expected_run=run, expected_content_digest=record.digest,
                              context_refs=CONTEXT, evaluator=self.evaluator)

    def test_record_from_wrong_retained_area_is_not_a_run_candidate(self):
        original = self.candidate
        ref = self.repository.put_json(
            run=original.request.expected_run, destination=PersistenceDestination(
                PersistenceArea.RUN_CANDIDATE, run_id=original.request.expected_run.run_id),
            record_kind=STATE_RECORD, payload=original.request.record.to_dict(),
        )
        with self.assertRaisesRegex(RetainedEvaluationUnavailable, "does not belong"):
            evaluate_retained(self.repository, ref, expected_run=original.request.expected_run,
                              expected_content_digest=original.request.expected_content_digest,
                              context_refs=CONTEXT, evaluator=self.evaluator)

    def test_retained_record_manifest_disagreement_is_unavailable(self):
        wrong = replace(self.candidate.request.record, base=ProjectVersionRef(PROJECT_ID, 1, "f" * 64))
        ref = self.repository.put_json(
            run=self.candidate.request.expected_run,
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=wrong.run_id),
            record_kind=STATE_RECORD, payload=wrong.to_dict(),
        )
        with self.assertRaisesRegex(RetainedEvaluationUnavailable, "run manifest"):
            evaluate_retained(self.repository, ref, expected_run=wrong.run_ref, expected_content_digest=wrong.digest,
                              context_refs=CONTEXT, evaluator=self.evaluator)

    def test_missing_volume_value_remains_unavailable_after_retention(self):
        original = self.candidate.request.record
        payload = original.to_dict()
        for entity in payload["entities"]:
            if entity["schema"] == "Volume@1":
                del entity["fields"]["min"]
        run, record, ref = self.retain(original, payload=payload)
        with self.assertRaisesRegex(RetainedEvaluationUnavailable, "missing required fields"):
            evaluate_retained(self.repository, ref, expected_run=run, expected_content_digest=record.digest,
                              context_refs=CONTEXT, evaluator=self.evaluator)

    def test_element_bounding_box_does_not_become_declared_massing_cells(self):
        authored = StateRecord(PROJECT_ID, "cad-shaped-input", (
            Entity("mesh", "Element@1", {"producer": "box", "bbox": {"min": [0, 0, 0], "max": [5, 2, 3]}}),
        ), evidence_refs=(EVIDENCE,))
        run, record, ref = self.retain(authored)
        result = evaluate_retained(self.repository, ref, expected_run=run, expected_content_digest=record.digest,
                                   context_refs=CONTEXT, evaluator=self.evaluator)
        self.assertEqual(result.validity, "unavailable")
        self.assertTrue(all(item.value is None for item in result.objectives))


if __name__ == "__main__":
    unittest.main()
