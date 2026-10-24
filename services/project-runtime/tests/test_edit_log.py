"""The edit log: each run's retained change read against its source record, step by step along a line.

The records are the support fixture's portico (two prism elements, a declared
``module`` with ``bay`` and ``span`` derived from it). A line of runs is
retained the way a candidate keeps its change (``StudioCandidateDelta@1``),
here in memory: what is read is exactly the operator and its source record.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from archflow.project.refs import ProjectVersionRef, RunRef
from archflow.state.state_record import (
    Entity, StateRecord, StateRecordEditKind, StateRecordOperator, apply_state_record_operator,
)

from project_runtime.application.edit_log import (
    ADD, EDIT, REMOVE, SET, by_target, edit_log, edits_between, step_of,
)
from project_runtime.api.routes import edit_log as edit_log_routes
from project_runtime.errors import StudioError

from . import support


def _record(run_id: str = "run-001") -> StateRecord:
    run = RunRef(support.PROJECT_ID, run_id, ProjectVersionRef(support.PROJECT_ID, 0, None))
    return StateRecord.from_dict(support.RECORD_PAYLOAD).bound_to(run)


def _scalar(record: StateRecord, target_ref: str, key: str, value: float) -> StateRecordOperator:
    return StateRecordOperator(kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest,
                               base_state_digest=record.state_digest, target_ref=target_ref, key=key, value=value)


def _components(record: StateRecord, **parts) -> StateRecordOperator:
    return StateRecordOperator(kind=StateRecordEditKind.EDIT_COMPONENTS, base_record_digest=record.digest,
                               base_state_digest=record.state_digest, **parts)


def _entity(record: StateRecord, entity_id: str) -> Entity:
    return next(entity for entity in record.entities if entity.entity_id == entity_id)


class _Line:
    """Runs retained one after another, each continuing the previous: what ``candidate_delta`` answers."""

    def __init__(self) -> None:
        self.deltas: dict[str, dict] = {}
        self.record = _record()
        self.head = "run-001"

    def add(self, run_id: str, operator: StateRecordOperator, request: str | None = None) -> None:
        self.deltas[run_id] = {"schema": "StudioCandidateDelta@1", "project_id": support.PROJECT_ID, "run_id": run_id,
                               "source_run_ref": {"project_id": support.PROJECT_ID, "run_id": self.head},
                               "source_record_ref": None, "source_record": self.record.to_dict(),
                               "operator": operator.to_dict(), "request": request}
        after = apply_state_record_operator(self.record, operator)
        self.record = after.bound_to(RunRef(support.PROJECT_ID, run_id, after.base))
        self.head = run_id

    def thinner(self, run_id: str, height: float, request: str | None = None) -> None:
        self.add(run_id, _scalar(self.record, "entity:portico-cornice", "height", height), request)

    def run_ids(self) -> frozenset[str]:
        return frozenset({"run-001", *self.deltas})

    def candidate_delta(self, run_id: str):
        return self.deltas.get(run_id)

    @property
    def repository(self):
        raise AssertionError("an inline source record is read without the repository")


class EditsBetweenTests(unittest.TestCase):
    def test_an_element_field_set_names_its_path_values_and_direction(self) -> None:
        record = _record()
        after = apply_state_record_operator(record, _scalar(record, "entity:portico-base", "height", 0.5))
        [edit] = edits_between(record, after)
        self.assertEqual((edit.target_ref, edit.component_id, edit.op, edit.field), ("entity:portico-base", "portico",
                                                                                    SET, "params.height"))
        self.assertEqual((edit.before, edit.after, edit.direction), (0.6, 0.5, -1))
        self.assertAlmostEqual(edit.delta, -0.1)
        self.assertEqual(edit.to_dict()["direction"], -1)

    def test_a_parameter_and_what_is_derived_from_it_each_move(self) -> None:
        record = _record()
        after = apply_state_record_operator(record, _scalar(record, "parameter:module", "module", 1.3))
        moved = {edit.target_ref: (edit.before, edit.after, edit.direction) for edit in edits_between(record, after)}
        self.assertEqual(moved["parameter:module"], (1.2, 1.3, 1))
        self.assertEqual(set(moved), {"parameter:module", "parameter:bay", "parameter:span"})
        self.assertTrue(all(direction == 1 for *_, direction in moved.values()))

    def test_added_removed_and_stretched_elements(self) -> None:
        record = _record()
        base = _entity(record, "portico-base")
        step = Entity("portico-step", "Element@1", {**base.fields, "params": {**base.fields["params"], "height": 0.2}},
                      "portico", base.basis_refs)
        wider = replace(base, fields={**base.fields, "params": {**base.fields["params"],
                                                                "profile": [[0, 0], [4.5, 0], [4.5, 2], [0, 2]]}})
        after = apply_state_record_operator(record, _components(record, entities=(step, wider)))
        edits = {(edit.target_ref, edit.op, edit.field): edit for edit in edits_between(record, after)}
        self.assertIn(("entity:portico-step", ADD, None), edits)
        stretched = [edit for (ref, op, _), edit in edits.items() if ref == "entity:portico-base"]
        self.assertEqual({edit.field for edit in stretched}, {"params.profile[1][0]", "params.profile[2][0]"})
        self.assertTrue(all(edit.direction == 1 for edit in stretched))
        removed = apply_state_record_operator(record, _components(
            record, remove_entity_ids=("portico-cornice",), remove_relation_ids=("rel-cornice-on-base",)))
        self.assertEqual([(edit.target_ref, edit.op) for edit in edits_between(record, removed)],
                         [("entity:portico-cornice", REMOVE)], "the base keeps its fields; only its relation went")

    def test_a_change_that_is_not_a_number_is_named_without_numbers(self) -> None:
        record = _record()
        base = _entity(record, "portico-base")
        retyped = replace(base, fields={**base.fields, "producer": "extrusion"})
        after = apply_state_record_operator(record, _components(record, entities=(retyped,)))
        [edit] = edits_between(record, after)
        self.assertEqual((edit.op, edit.field, edit.delta, edit.direction), (EDIT, None, None, 0))


class EditLogTests(unittest.TestCase):
    def line(self) -> _Line:
        line = _Line()
        line.thinner("run-002", 0.25, "the cornice thinner")
        line.add("run-003", _scalar(line.record, "parameter:module", "module", 1.25))
        line.thinner("run-004", 0.2)
        line.thinner("run-005", 0.15, "thinner again")
        return line

    def test_the_log_walks_the_line_back_nearest_first_and_stops_where_no_change_was_retained(self) -> None:
        steps = edit_log(self.line(), "run-005")
        self.assertEqual([step.run_id for step in steps], ["run-005", "run-004", "run-003", "run-002"])
        self.assertEqual([step.source_run_id for step in steps], ["run-004", "run-003", "run-002", "run-001"])
        self.assertEqual((steps[0].kind, steps[0].request, steps[1].request), ("set_scalar", "thinner again", None))
        [edit] = steps[0].edits
        self.assertEqual((edit.field, edit.before, edit.after), ("params.height", 0.2, 0.15))
        self.assertEqual(steps[0].to_dict()["edits"][0]["direction"], -1)

    def test_a_combined_run_is_one_step_and_the_log_goes_on_along_its_source(self) -> None:
        line = self.line()
        # A combination retains its change like any candidate, naming the results it folded in.
        line.deltas["run-004"]["combined_candidate_ids"] = ["run-side"]
        steps = edit_log(line, "run-005")
        self.assertEqual([step.run_id for step in steps], ["run-005", "run-004", "run-003", "run-002"])
        self.assertEqual(steps[1].source_run_id, "run-003", "the combined result is its own line, not this one")

    def test_the_limit_counts_steps_and_is_bounded(self) -> None:
        self.assertEqual(len(edit_log(self.line(), "run-005", limit=2)), 2)
        for limit in (0, 501, True, 2.5):
            with self.subTest(limit=limit), self.assertRaises(StudioError) as caught:
                edit_log(self.line(), "run-005", limit=limit)
            self.assertEqual(caught.exception.code, "EDIT_LOG_LIMIT_INVALID")

    def test_a_change_that_cannot_be_read_again_is_refused_not_skipped(self) -> None:
        line = self.line()
        line.deltas["run-004"]["operator"]["kind"] = "paint"
        with self.assertRaises(StudioError) as caught:
            edit_log(line, "run-005")
        self.assertEqual(caught.exception.code, "EDIT_LOG_UNREADABLE")
        self.assertIn("run-004", caught.exception.detail)

    def test_by_target_gathers_each_field_with_its_steps_directions_and_net_change(self) -> None:
        histories = {(row.target_ref, row.field): row for row in by_target(edit_log(self.line(), "run-005"))}
        cornice = histories[("entity:portico-cornice", "params.height")]
        self.assertEqual((cornice.component_id, cornice.steps, cornice.directions), ("portico", (0, 1, 3), (-1, -1, -1)))
        self.assertAlmostEqual(cornice.net, -0.15)
        self.assertEqual(histories[("parameter:module", "value")].steps, (2,))
        self.assertEqual(cornice.to_dict()["directions"], [-1, -1, -1])

    def test_one_step_reads_its_operator_against_the_record_it_names(self) -> None:
        line = self.line()
        delta = line.deltas["run-002"]
        step = step_of("run-002", delta, StateRecord.from_dict(delta["source_record"]))
        self.assertEqual([(edit.before, edit.after) for edit in step.edits], [(0.3, 0.25)])


class EditLogRouteTests(unittest.TestCase):
    """GET /api/edit-log on an app whose project is the in-memory line: no project folder is written."""

    def setUp(self) -> None:
        line = EditLogTests().line()
        app = FastAPI()
        app.include_router(edit_log_routes.router, prefix="/api")
        app.state.binding = line
        head = SimpleNamespace(head=SimpleNamespace(run_id="run-005"))
        patcher = mock.patch.object(edit_log_routes, "resolve_working_source", lambda binding: head)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_the_working_heads_line_by_default_with_each_field_gathered(self) -> None:
        response = self.client.get("/api/edit-log", params={"limit": 3})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["runId"], "run-005")
        self.assertEqual([step["runId"] for step in body["steps"]], ["run-005", "run-004", "run-003"])
        self.assertEqual(body["steps"][0]["edits"], [{
            "targetRef": "entity:portico-cornice", "componentId": "portico", "op": "set", "field": "params.height",
            "before": 0.2, "after": 0.15, "delta": body["steps"][0]["edits"][0]["delta"], "direction": -1}])
        self.assertEqual(body["steps"][0]["request"], "thinner again")
        cornice = next(row for row in body["targets"] if row["targetRef"] == "entity:portico-cornice")
        self.assertEqual((cornice["steps"], cornice["directions"]), ([0, 1], [-1, -1]))

    def test_another_run_names_its_own_line_and_an_unknown_one_is_refused(self) -> None:
        body = self.client.get("/api/edit-log", params={"run": "run-003"}).json()
        self.assertEqual([step["runId"] for step in body["steps"]], ["run-003", "run-002"])
        with self.assertRaises(StudioError) as caught:
            self.client.get("/api/edit-log", params={"run": "run-404"})
        self.assertEqual((caught.exception.status, caught.exception.code), (404, "RUN_NOT_FOUND"))
        for params in ({"limit": 0}, {"limit": 501}, {"run": "../x"}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get("/api/edit-log", params=params).status_code, 422)

    def test_a_project_without_a_working_head_has_no_steps(self) -> None:
        with mock.patch.object(edit_log_routes, "resolve_working_source", lambda binding: SimpleNamespace(head=None)):
            body = self.client.get("/api/edit-log").json()
        self.assertEqual(body, {"runId": None, "steps": [], "targets": []})


if __name__ == "__main__":
    unittest.main()
