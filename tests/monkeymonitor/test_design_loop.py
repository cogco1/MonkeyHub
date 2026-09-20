"""The design-loop benchmark requires retained intent and same-source evidence."""
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest
from urllib.parse import urlencode

from archflow.adapters.occt_backend import occt_available
from tests.monkeymonitor import run_design_loop as design_loop


class DesignLoopChecksTests(unittest.TestCase):
    def setUp(self):
        self.tool_index = 0
        self.record = design_loop.bench.StateRecord.from_dict(design_loop.bench.project_fixture().RECORD_PAYLOAD).to_dict()
        self.record["entities"].extend([
            {"entity_id": "court-condition", "schema": "Reading@1", "fields": {
                "note": "Keep a 12 m by 12 m courtyard open to the south.",
                "subject_refs": ["entity:west-wing", "entity:east-wing", "entity:north-wing"],
            }},
            {"entity_id": "north-upper", "schema": "Element@1", "fields": {
                "references": {"base": {"datum": "north-wing-top"}},
            }},
        ])
        self.source = deepcopy(self.record)
        self.final = {"source_run": "candidate-final", "state_digest": "b" * 64,
                      "exact_shapes_ok": True, "court_intrusion_m3": 0.0}
        self.report = {"step": "setback", "record": self.record, "spatial": [self.final], "source": "candidate-source",
                       "operations": [{"candidateId": "candidate-final", "sourceRunId": "candidate-source", "proposalId": "proposal-final"}]}

    def view(self, source=None, **query):
        source = source or self.final
        self.tool_index += 1
        return {"id": f"turn:view-{self.tool_index}", "role": "tool", "status": "complete",
                "content": "studio_request completed\nGET /api/drawings/model-view?" + urlencode({
                    "runId": source["source_run"], "stateDigest": source["state_digest"], "view": "top", **query})}

    def checks(self, messages=None):
        return design_loop.check_turn(self.report, [self.view(), self.view(view="front")] if messages is None else messages,
                                      source_record=self.source)

    def trial_report(self):
        self.report["step"] = "trial-repair"
        trial = {**self.final, "source_run": "candidate-trial", "state_digest": "a" * 64, "court_intrusion_m3": 100.0}
        self.report["spatial"] = [trial, self.final]
        self.report["operations"] = [
            {"candidateId": "candidate-trial", "sourceRunId": "candidate-source", "proposalId": "proposal-trial"},
            {"candidateId": "candidate-final", "sourceRunId": "candidate-trial", "proposalId": "proposal-final"},
        ]
        proposal = {"id": "turn:proposal-final", "role": "tool", "status": "complete", "content": "studio_request completed\nproposalId: proposal-final"}
        return trial, proposal

    def successful_span(self, message, start, end):
        return {"event_id": "hub:tool:" + message["id"], "phase": "tool_call", "status": "succeeded",
                "started_at": f"2026-09-20T12:00:{start:02d}+00:00", "ended_at": f"2026-09-20T12:00:{end:02d}+00:00"}

    def test_complete_matching_view_and_retained_condition_are_accepted(self):
        self.assertTrue(all(value for key, value in self.checks().items()
                            if key != "actual_model_view_calls" and value is not None))

    def test_missing_wrong_source_or_failed_view_is_not_observation(self):
        invalid = [[], [self.view(runId="another-candidate")], [self.view(stateDigest="c" * 64)],
                   [self.view(runId="")], [self.view(stateDigest="")], [self.view(view="front")],
                   [{**self.view(), "status": "failed"}],
                   [{**self.view(), "content": self.view()["content"].replace("completed", "failed")}],
                   [{**self.view(), "role": "assistant"}]]
        for messages in invalid:
            with self.subTest(messages=messages):
                self.assertFalse(self.checks(messages)["final_same_source_top_view"])

    def test_empty_tool_content_is_not_observation(self):
        for content in ("", None, "\n"):
            with self.subTest(content=content):
                result = self.checks([{**self.view(), "content": content}])
                self.assertFalse(result["final_same_source_top_view"])
                self.assertFalse(result["setback_front_view"])
                self.assertEqual(result["actual_model_view_calls"], 0)
        self.assertFalse(self.checks([{"role": "tool", "status": "complete"}])["final_same_source_top_view"])

    def test_setback_and_wall_need_a_successful_same_source_front_view(self):
        for step in ("setback", "wall"):
            self.report["step"] = step
            self.assertTrue(self.checks()["setback_front_view"])
            for front in (None, self.view(view="front", runId="other-run"), self.view(view="front", stateDigest="c" * 64),
                          {**self.view(view="front"), "status": "failed"}):
                with self.subTest(step=step, front=front):
                    result = self.checks([self.view(), *([front] if front else [])])
                    self.assertTrue(result["final_same_source_top_view"])
                    self.assertFalse(result["setback_front_view"])
        for step in ("courtyard", "lower", "trial-repair", "reopen"):
            self.report["step"] = step
            self.assertIsNone(self.checks([self.view()])["setback_front_view"])

    def test_missing_or_rewritten_retained_reading_is_not_preserved(self):
        for change in ("remove", "rewrite"):
            with self.subTest(change=change):
                self.record["entities"] = deepcopy(self.source["entities"])
                if change == "remove":
                    self.record["entities"] = [row for row in self.record["entities"] if row["schema"] != "Reading@1"]
                else:
                    next(row for row in self.record["entities"] if row["schema"] == "Reading@1")["fields"]["note"] = "An 8 m court is enough."
                self.assertFalse(self.checks()["retained_condition_preserved"])

    def test_first_courtyard_reading_needs_dimension_direction_and_all_subjects(self):
        self.report["step"] = "courtyard"
        reading = next(row for row in self.record["entities"] if row["schema"] == "Reading@1")
        original = deepcopy(reading["fields"])
        for field, value in (("note", "A courtyard open south."), ("note", "Keep a 12 m by 12 m courtyard."),
                             ("subject_refs", ["entity:west-wing", "entity:north-wing"])):
            with self.subTest(field=field, value=value):
                reading["fields"] = {**original, field: value}
                self.assertFalse(self.checks()["retained_condition_preserved"])

    def test_absolute_or_different_support_does_not_preserve_the_reference(self):
        upper = next(row for row in self.record["entities"] if row["entity_id"] == "north-upper")
        for reference in ({"level": "level-ground"}, {"elevation": 8}, {"datum": "east-wing-top"}):
            with self.subTest(reference=reference):
                upper["fields"]["references"]["base"] = reference
                self.assertFalse(self.checks()["upper_support_reference_preserved"])
        self.record["entities"].remove(upper)
        self.assertFalse(self.checks()["upper_support_reference_preserved"])

    def test_changed_fixture_or_removed_parameter_and_relation_is_not_preserved(self):
        for category in ("entities", "parameters", "relations"):
            with self.subTest(category=category):
                original = deepcopy(self.record[category])
                self.record[category].pop(0)
                self.assertFalse(self.checks()["original_state_preserved"])
                self.record[category] = original

    def test_trial_needs_distinct_observed_intrusion_and_observed_clear_final(self):
        trial, proposal = self.trial_report()
        self.assertTrue(self.checks([self.view(trial), proposal, self.view()])["trial_observed_and_corrected"])
        trial["exact_shapes_ok"] = False
        self.assertFalse(self.checks([self.view(trial), proposal, self.view()])["trial_observed_and_corrected"])
        trial["exact_shapes_ok"] = True
        for messages in ([self.view()], [self.view(trial)], [self.view(trial, stateDigest="wrong"), self.view()]):
            with self.subTest(messages=messages):
                self.assertFalse(self.checks(messages)["trial_observed_and_corrected"])
        trial["court_intrusion_m3"] = 0.0
        self.assertFalse(self.checks([self.view(trial), self.view()])["trial_observed_and_corrected"])
        self.report["spatial"] = [self.final]
        self.assertFalse(self.checks()["trial_observed_and_corrected"])

    def test_candidate_operations_must_chain_from_input_through_each_trial(self):
        trial, proposal = self.trial_report()
        messages = [self.view(trial), proposal, self.view()]
        self.assertTrue(self.checks(messages)["exact_candidate_source_chain"])
        operations = deepcopy(self.report["operations"])
        for rows in ([], operations[:1], operations[1:],
                     [{**operations[0], "sourceRunId": "unrelated-source"}, operations[1]],
                     [operations[0], {**operations[1], "sourceRunId": "candidate-source"}],
                     [operations[0], {**operations[1], "candidateId": "other-candidate"}]):
            with self.subTest(operations=rows):
                self.report["operations"] = rows
                self.assertFalse(self.checks(messages)["exact_candidate_source_chain"])
        self.report["operations"] = operations
        self.report["source"] = None
        self.assertFalse(self.checks(messages)["exact_candidate_source_chain"])
        self.report["source"] = "candidate-source"
        self.report["spatial"].reverse()
        self.assertFalse(self.checks(messages)["exact_candidate_source_chain"])

    def test_correct_trial_view_must_precede_the_exact_repair_proposal(self):
        trial, proposal = self.trial_report()
        before = self.view(trial)
        final = self.view()
        self.report["trace"] = {"spans": [self.successful_span(before, 1, 2), self.successful_span(proposal, 3, 4)]}
        self.assertTrue(self.checks([before, proposal, final])["trial_observed_before_repair"])
        invalid = [
            [proposal, before, final],
            [before, final],
            [{**before, "status": "failed"}, proposal, before, final],
            [self.view(trial, stateDigest="wrong"), proposal, before, final],
            [self.view(trial, runId="candidate-trial-other"), proposal, before, final],
            [self.view(trial, view="front"), proposal, before, final],
            [before, {**proposal, "content": "studio_request completed\nproposalId: proposal-final-other"}, final],
        ]
        for messages in invalid:
            with self.subTest(messages=messages):
                self.assertFalse(self.checks(messages)["trial_observed_before_repair"])

    def test_trial_image_must_finish_before_repair_starts_on_the_exact_tool_spans(self):
        trial, proposal = self.trial_report()
        before = self.view(trial)
        messages = [before, proposal, self.view()]
        view_span, proposal_span = self.successful_span(before, 1, 2), self.successful_span(proposal, 3, 4)
        self.report["trace"] = {"spans": [view_span, proposal_span]}
        self.assertTrue(self.checks(messages)["trial_observed_before_repair"])
        invalid = [[], [view_span], [proposal_span],
                   [self.successful_span(before, 1, 5), proposal_span],
                   [{**view_span, "ended_at": None}, proposal_span],
                   [view_span, {**proposal_span, "ended_at": None}],
                   [{**view_span, "event_id": "hub:tool:other-call"}, proposal_span],
                   [{**view_span, "status": "failed"}, proposal_span],
                   [{**view_span, "started_at": "invalid"}, proposal_span]]
        for spans in invalid:
            with self.subTest(spans=spans):
                self.report["trace"] = {"spans": spans}
                self.assertFalse(self.checks(messages)["trial_observed_before_repair"])
        self.report["trace"] = None
        self.assertFalse(self.checks(messages)["trial_observed_before_repair"])
        self.assertFalse(self.checks([{"role": "tool", "status": "complete", "content": ""}])["trial_observed_before_repair"])

    def test_courtyard_requires_one_batched_candidate_but_other_steps_do_not_add_a_count_gate(self):
        self.report["step"] = "courtyard"
        self.assertTrue(self.checks()["courtyard_batched"])
        self.report["spatial"].insert(0, {**self.final, "source_run": "candidate-partial", "exact_shapes_ok": False})
        self.assertFalse(self.checks()["courtyard_batched"])
        for step in ("lower", "setback", "trial-repair", "reopen", "wall"):
            self.report["step"] = step
            self.assertIsNone(self.checks()["courtyard_batched"])

    def test_each_turn_preserves_source_fields_parameters_and_relations_outside_its_change(self):
        for name in ("west-wing", "east-wing", "north-wing"):
            self.record["entities"].append({"entity_id": name, "schema": "Element@1", "parent_id": "portico", "fields": {
                "producer": "prism", "component_id": "portico", "references": {"base": {"level": "level-ground"}},
                "params": {"profile": [[20, 0], [24, 0], [24, 16], [20, 16]], "height": 12},
            }})
        self.record["parameters"].append({"key": "retained-control", "value": 2, "lock_authority": "user"})
        self.record["relations"].append({"relation_id": "retained-relation", "kind": "support", "subject": "north-wing", "object": "north-upper"})
        self.source = deepcopy(self.record)
        for step, target in (("lower", "east-wing"), ("setback", "north-wing"), ("trial-repair", "east-wing"),
                             ("reopen", "west-wing"), ("wall", None)):
            self.report["step"] = step
            self.record.clear()
            self.record.update(deepcopy(self.source))
            current = {row["entity_id"]: row for row in self.record["entities"]}
            if target:
                params = current[target]["fields"]["params"]
                if step in {"lower", "setback"}:
                    params["height"] -= 3
                else:
                    params["profile"] = [[x - 2, y] for x, y in params["profile"]]
            self.assertTrue(self.checks()["source_fields_preserved"], step)
            valid = deepcopy(self.record)
            for mutation in ("target-base", "other-base", "producer", "parameter", "relation"):
                with self.subTest(step=step, mutation=mutation):
                    self.record.clear()
                    self.record.update(deepcopy(valid))
                    current = {row["entity_id"]: row for row in self.record["entities"]}
                    if mutation in {"target-base", "other-base"}:
                        entity = target or "west-wing" if mutation == "target-base" else "north-upper"
                        current[entity]["fields"]["references"]["base"] = {"elevation": 0}
                    elif mutation == "producer":
                        current[target or "west-wing"]["fields"]["producer"] = "loft"
                    elif mutation == "parameter":
                        self.record["parameters"][-1]["value"] = 3
                    else:
                        self.record["relations"].pop()
                    self.assertFalse(self.checks()["source_fields_preserved"])

    def test_move_can_keep_bound_profile_and_shift_only_work_plane_x(self):
        row = {"entity_id": "east-wing", "schema": "Element@1", "fields": {
            "producer": "prism", "references": {"base": {"level": "level-ground"}},
            "params": {"profile": [["@left", 0], ["@right", 0], ["@right", 16], ["@left", 16]], "height": 9},
        }}
        self.source["entities"].append(deepcopy(row))
        self.record["entities"].append(row)
        self.report["step"] = "trial-repair"
        params = row["fields"]["params"]
        params["work_plane"] = {"origin": [-2, 0, 0], "xAxis": [1, 0, 0], "yAxis": [0, 0, 1], "normal": [0, 1, 0]}
        self.assertTrue(self.checks()["source_fields_preserved"])
        params["work_plane"]["origin"][1] = 1
        self.assertFalse(self.checks()["source_fields_preserved"])
        params["work_plane"]["origin"][1] = 0
        params["profile"][0][0] = 36
        self.assertFalse(self.checks()["source_fields_preserved"])

    @unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
    def test_exact_readback_rejects_a_notched_wing_with_the_same_bbox_and_clear_court(self):
        from fastapi.testclient import TestClient
        from archflow_studio_api.main import create_app
        from archflow_studio_api.settings import StudioSettings

        with tempfile.TemporaryDirectory() as temporary:
            fixture = design_loop.bench.project_fixture()
            repository, _ = fixture.make_project(Path(temporary))
            project = repository.layout.root
            head = repository.layout.head.read_bytes()
            with TestClient(create_app(StudioSettings(project_dir=project, cad_export="occt"))) as client:
                def candidate(entities, source):
                    digest = client.get("/api/state", params={"run": source}).json()["stateDigest"]
                    proposed = client.post("/api/proposals", json={
                        "sourceRunId": source, "stateDigest": digest,
                        "semanticEdit": {"summary": "Exact-geometry acceptance regression.", "entities": entities},
                    })
                    self.assertEqual(proposed.status_code, 201, proposed.text)
                    submitted = client.post(f"/api/proposals/{proposed.json()['proposalId']}/candidate")
                    self.assertEqual(submitted.status_code, 202, submitted.text)
                    job = submitted.json()
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        status = client.get(f"/api/jobs/{job['jobId']}").json()
                        if status["status"] not in ("queued", "running"):
                            break
                        time.sleep(0.05)
                    self.assertEqual(status["status"], "succeeded", status)
                    return client.get(f"/api/candidates/{job['candidateId']}").json(), job["candidateId"]

                entities = []
                for name, profile in (("west-wing", [[20, 0], [24, 0], [24, 16], [20, 16]]),
                                      ("north-wing", [[24, 12], [38, 12], [38, 16], [24, 16]]),
                                      ("east-wing", [[38, 0], [42, 0], [42, 16], [38, 16]])):
                    entities.append({"entity_id": name, "schema": "Element@1", "parent_id": "portico", "fields": {
                        "component_id": "portico", "producer": "prism", "references": {"base": {"level": "level-ground"}},
                        "params": {"profile": profile, "height": 12},
                    }})
                correct, run = candidate(entities, fixture.REFERENCE_RUN_ID)
                certified = design_loop.spatial_readback(project, run, correct["stateDigest"], step="courtyard")
                self.assertTrue(certified["exact_shapes_ok"], certified)
                with self.assertRaisesRegex(ValueError, "exact complete model"):
                    design_loop.spatial_readback(project, run, "0" * 64, step="courtyard")
                notched = deepcopy(entities[0])
                notched["fields"]["params"]["profile"] = [[22, 0], [24, 0], [24, 16], [20, 16], [20, 2], [22, 2]]
                wrong, changed = candidate([notched], run)
                def west_bounds(readback):
                    return next(row["bbox"] for row in readback["objects"] if row["producerOp"] == "west-wing")
                self.assertEqual(west_bounds(correct), west_bounds(wrong))
                result = design_loop.spatial_readback(project, changed, wrong["stateDigest"], step="courtyard")
                self.assertFalse(result["exact_shapes_ok"])
                self.assertAlmostEqual(result["court_intrusion_m3"], 0.0)
                self.assertAlmostEqual(result["exact_shape_differences_m3"]["west-wing"], 48.0, places=6)
                self.assertEqual(result["unexpected_objects"], [])
                self.assertEqual(repository.layout.head.read_bytes(), head)


if __name__ == "__main__":
    unittest.main()
