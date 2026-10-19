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
from project_runtime.application.state_files import decision_request, outside_conditions
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
        started = self.client.post("/api/exports", json={
            "targetFormat": "3dm", "withState": True,
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

    def test_only_a_3dm_of_an_exact_revision_carries_its_state(self) -> None:
        self.landed(2.2)
        head = self.head()
        refused = self.client.post("/api/exports", json={"targetFormat": "glb", "withState": True, "projectRevision": {
            "runId": head["runId"], "stateDigest": head["stateDigest"]}})
        self.assertEqual((refused.status_code, refused.json()["code"]), (422, "EXPORT_STATE_UNAVAILABLE"), refused.text)


class OutsideConditionTests(unittest.TestCase):
    """What the file's reader found changed outside MonkeyHub, as conditions on the parts it touched."""

    def setUp(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        make_project(root)
        record_path = root / PROJECT_ID / "input" / "runner" / "state-record.json"
        self.record = StateRecord.from_dict(json.loads(record_path.read_text(encoding="utf-8")))

    def test_each_change_is_a_condition_no_check_verifies_on_what_it_touched(self) -> None:
        findings = (OutOfBand("object_moved", "object:obj-portico-base", {"moved_m": 0.5}),
                    OutOfBand("row_changed", "parameter:module", {}),
                    OutOfBand("object_added", "object:imported-chair", {}),
                    OutOfBand("unsealed", "file", {}))
        conditions = outside_conditions(self.record, findings, [{"targetRef": "entity:portico"}], file_sha256="a" * 64)
        self.assertEqual([row.subject_refs for row in conditions],
                         [("entity:portico-base", "entity:portico"), ("parameter:module",)],
                         "an object no part delivered and a missing seal make no condition")
        self.assertIn("0.500 m", conditions[0].statement)
        self.assertTrue(all(row.validator_ref is None for row in conditions))
        with_them = replace(self.record, obligations=(*self.record.obligations, *conditions))
        results = {row.obligation_id: row.status for row in check_obligations(with_them)}
        self.assertEqual(results["outside-1"], "unchecked", "never counted as held")
        self.assertEqual([row.obligation_id for row in obligations_in_scope(with_them, ("entity:portico-base",))
                          if row.obligation_id.startswith("outside-")], ["outside-1"])


if __name__ == "__main__":
    unittest.main()
