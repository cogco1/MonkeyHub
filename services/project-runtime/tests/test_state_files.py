"""A 3DM that carries its design state, decisions and seal (#657), written and read by a real Runtime.

The model is exported from a change that landed through POST /api/revisions on an OCCT-building Runtime;
the file is then changed outside MonkeyHub - an object moved, a row of its state edited - and read back.
"""

from __future__ import annotations

from dataclasses import replace
import base64
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from monkeycad.backends.occt.kernel import occt_available
from archflow.state.state_record import StateRecord, check_obligations, obligations_in_scope
from monkeycad.formats.state_file import OutOfBand
from project_runtime.application.state_files import decision_request, outside_conditions, outside_rows
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

from .support import PROJECT_ID, make_project



@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class StateFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        make_project(self.root)
        self.client = TestClient(create_app(StudioSettings(cad_export="occt", project_dir=self.root / PROJECT_ID, design_loop="enforced",
                                                           cache_dir=self.root / "cache")))
        self.addCleanup(self.client.close)

    def head(self) -> dict:
        return self.client.get("/api/working-source").json()["head"]

    def landed(self, height: float) -> dict:
        head = self.head()
        answer = self.client.post("/api/revisions", json={
            "projectId": PROJECT_ID, "sourceRunId": head["runId"], "stateDigest": head["stateDigest"], "kind": "proposal",
            "summary": f"base height {height}", "proposal": {"utterance": f"set height to {height}",
                                                             "targetComponentId": "portico", "elementId": "portico-base"}}).json()
        self.assertEqual(answer["outcome"], "landed", answer)
        return answer

    def exported(self, **extra) -> bytes:
        head = self.head()
        started = self.client.post("/api/state-files/export", json={
            "projectRevision": {"runId": head["runId"], "stateDigest": head["stateDigest"]}, **extra})
        self.assertEqual(started.status_code, 202, started.text)
        export_id = started.json()["exportId"]
        deadline = time.monotonic() + 120
        while (report := self.client.get(f"/api/exports/{export_id}").json())["status"] in ("queued", "running"):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)
        self.assertEqual(report["status"], "succeeded", report)
        self.assertTrue(report["withState"])
        return self.client.get(f"/api/exports/{export_id}/bytes").content

    def inspect(self, data: bytes) -> dict:
        response = self.client.post("/api/state-files/inspect", json={
            "fileName": "model.3dm", "contentBase64": base64.b64encode(data).decode("ascii")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_the_file_carries_the_record_and_the_decisions_and_reads_back_clean(self) -> None:
        self.landed(2.2)
        head = self.head()
        saved = self.client.post("/api/decisions", json=decision_request(
            {"disposition": "keep", "targetRef": "entity:portico-base", "rawLanguage": "基座别动", "strength": "hard"},
            project_id=PROJECT_ID, run_id=head["runId"], state_digest=head["stateDigest"]))
        self.assertEqual(saved.status_code, 201, saved.text)

        found = self.inspect(self.exported())
        self.assertTrue(found["sealed"])
        self.assertEqual((found["outOfBand"], found["conditions"]), ([], []))
        self.assertEqual(found["record"]["run_id"], head["runId"])
        self.assertEqual([(row["disposition"], row["targetRef"], row["rawLanguage"]) for row in found["decisions"]],
                         [("keep", "entity:portico-base", "基座别动")])

    def test_a_resealed_file_names_the_file_it_was_made_from(self) -> None:
        self.landed(2.2)
        parent = self.inspect(self.exported())["fileSha256"]
        self.assertEqual(self.inspect(self.exported(parentFileSha256=parent))["parentFileSha256"], parent)

    def test_state_files_are_the_design_loops_and_the_export_route_is_as_it_was(self) -> None:
        self.landed(2.2)
        head = self.head()
        revision = {"runId": head["runId"], "stateDigest": head["stateDigest"]}
        refused = self.client.post("/api/exports", json={"targetFormat": "3dm", "withState": True, "projectRevision": revision})
        self.assertEqual(refused.status_code, 422, "POST /api/exports takes no state field, as before")
        self.assertIn("withState", refused.text)
        for tier in ("legacy", "advisory"):
            with self.subTest(tier=tier):
                app = create_app(StudioSettings(cad_export="occt", project_dir=self.root / PROJECT_ID, design_loop=tier))
                with TestClient(app) as other:
                    for path, body in (("/api/state-files/export", {"projectRevision": revision}),
                                       ("/api/state-files/inspect", {"fileName": "m.3dm", "contentBase64": "AA=="})):
                        answer = other.post(path, json=body)
                        self.assertEqual((answer.status_code, answer.json()["code"]), (409, "DESIGN_LOOP_NOT_ENFORCED"), answer.text)


class OutsideConditionTests(unittest.TestCase):
    """What the file's reader found changed outside MonkeyHub, as conditions on the parts it touched."""

    def setUp(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        make_project(root)
        record_path = root / PROJECT_ID / "input" / "runner" / "state-record.json"
        self.record = StateRecord.from_dict(json.loads(record_path.read_text(encoding="utf-8")))

    def test_each_change_is_a_question_for_the_architect_on_the_part_it_touched(self) -> None:
        findings = (OutOfBand("object_moved", "object:obj-portico-base", {"moved_m": 0.5}),
                    OutOfBand("row_changed", "parameter:module", {}),
                    OutOfBand("object_added", "object:imported-chair", {}))
        conditions = outside_conditions(self.record, findings, [{"targetRef": "entity:portico"}], file_sha256="a" * 64)
        self.assertEqual([row.subject_refs for row in conditions], [("entity:portico-base",), ("parameter:module",)],
                         "the part alone, never the whole it belongs to; an object no part delivered asks nothing")
        self.assertIn("0.500 m", conditions[0].statement)
        self.assertIn("waits for the architect's answer", conditions[0].statement)
        self.assertTrue(all(row.validator_ref is None for row in conditions))
        first = conditions[0].obligation_id
        self.assertTrue(first.startswith("outside-aaaaaaaaaaaa-"), "ids are the file's, so two files' questions never meet")
        with_them = replace(self.record, obligations=(*self.record.obligations, *conditions))
        results = {row.obligation_id: row.status for row in check_obligations(with_them)}
        self.assertEqual(results[first], "unchecked", "never counted as held")
        self.assertEqual([row.obligation_id for row in obligations_in_scope(with_them, ("entity:portico-base",))
                          if row.obligation_id.startswith("outside-")], [first])

    def test_a_seal_that_vouches_for_nothing_puts_the_whole_design_and_each_decision_to_the_architect(self) -> None:
        keep = {"disposition": "keep", "targetRef": "entity:portico", "rawLanguage": "门廊别动", "strength": "hard"}
        for kind in ("unsealed", "seal_broken", None):
            with self.subTest(kind=kind):
                findings = () if kind is None else (OutOfBand(kind, "file", {}),)
                whole, said = outside_conditions(self.record, findings, [keep], file_sha256="c" * 64, vouched=False)
                self.assertEqual(whole.subject_refs, (f"project:{PROJECT_ID}",))
                self.assertIn("cannot be told", whole.statement)
                self.assertIn("ask them before changing anything in the design", whole.statement)
                self.assertEqual(said.subject_refs, ("entity:portico",))
                self.assertIn("keep entity:portico (\"门廊别动\")", said.statement)
                self.assertIn("not recorded as theirs", said.statement)
        # A record removed from beside a sound seal changes nothing: the seal keeps the design.
        self.assertEqual(outside_conditions(self.record, (OutOfBand("state_replaced", "file", {}),), [keep],
                                            file_sha256="c" * 64), ())

    def test_a_part_moved_by_several_objects_asks_once_and_every_file_is_listed_newest_first(self) -> None:
        def moved(name, dx):
            return OutOfBand("object_moved", f"object:{name}", {
                "moved_m": dx, "before": {"min": [0, 0, 0], "max": [4, 2, 0.6]}, "after": {"min": [dx, 0, 0], "max": [4 + dx, 2, 0.6]}})

        older = outside_conditions(self.record, (moved("obj-portico-base", 0.3),), [], file_sha256="a" * 64)
        newer = outside_conditions(self.record, (moved("obj-portico-base", 0.1), moved("obj-portico-base-1", 0.1)), [],
                                   file_sha256="b" * 64)
        self.assertEqual(len(newer), 1)
        self.assertIn("(0.300, 0.000, 0.000) m (east, north, up)", older[0].statement)
        self.assertEqual(newer[0].statement.count("(0.100, 0.000, 0.000) m (east, north, up)"), 2)
        rows = outside_rows(replace(self.record, obligations=(*self.record.obligations, *older, *newer)))
        self.assertEqual([row["fileSha256"][0] for row in rows], ["b", "a"])


if __name__ == "__main__":
    unittest.main()
