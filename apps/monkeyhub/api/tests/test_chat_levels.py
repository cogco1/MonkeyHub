"""A project level is moved or added from the chat with elevation set-datum, and what stands on it follows (#651).

The calls go through the chat tool layer exactly as a connected CLI sends them,
to a real Studio over one P036 project; transport is in-process and no model runs.
"""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

source_roots.put_first(ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from archflow.project.repository import FilesystemProjectRepository  # noqa: E402
from archflow.state.state_record import StateRecord  # noqa: E402
from project_runtime.main import create_app  # noqa: E402
from project_runtime.settings import StudioSettings  # noqa: E402
from monkeyhub_api.chat import preparation, tool_calls, transport  # noqa: E402
from monkeyhub_api.models import HubFailure  # noqa: E402


class ChatLevelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub levels ")
        self.addCleanup(temporary.cleanup)
        self.project_id = "levels"
        self.project = Path(temporary.name) / self.project_id
        FilesystemProjectRepository.initialize(
            self.project, project_id=self.project_id, initial_state={"project_id": self.project_id, "version": 0},
            authored_record=StateRecord(project_id=self.project_id, run_id="authored", entities=()).to_dict())
        app = create_app(StudioSettings(project_dir=self.project, cad_export="off"))
        app.state.managed_instance_id = "hub-test"
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.hub, self.base = "http://127.0.0.1:8700", "http://127.0.0.1:8701"
        self.session = {"id": str(uuid4()), "projectId": self.project_id, "projectDir": str(self.project.resolve()),
                        "status": "running", "messages": [{"id": str(uuid4()), "role": "user", "status": "complete",
                                                           "content": "加一层二楼，在上面放一块楼板，再把二楼抬高到 4 米。"}]}
        self.sent = []
        self.addCleanup(patch.stopall)
        patch.object(preparation, "_bound_studio", side_effect=lambda *a, **k: (self.base, deepcopy(self.session))).start()
        patch.object(transport, "_request_json", side_effect=self.request).start()

    def request(self, base, path, method="GET", body=None, **kwargs):
        if base == self.hub and path.startswith("/api/chat/sessions/"):
            return deepcopy(self.session)
        if base == self.hub:
            path = path.split("/studio", 1)[1]
        self.sent.append((method, path.split("?", 1)[0]))
        response = self.client.request(method, path, json=body)
        if response.is_error:
            failure = response.json()
            raise HubFailure(response.status_code, failure["code"], failure["detail"])
        return response.json()

    def call(self, method, path, body=None, name="studio_request", **options):
        """One tool call exactly as the CLI would send it."""
        arguments = {"method": method, "path": path, **options}
        if body is not None:
            arguments["body"] = body
        return tool_calls.call_tool(self.hub, self.session["id"], name, arguments)

    def refused(self, code, *args, **kwargs):
        with self.assertRaises(HubFailure) as refusal:
            self.call(*args, **kwargs)
        self.assertEqual(refusal.exception.error.code, code, refusal.exception.error.detail)
        return refusal.exception.error.detail

    def candidate(self, proposal):
        self.assertEqual(proposal["status"], "proposed", proposal)
        made = self.call("POST", f"/api/proposals/{proposal['proposalId']}/candidate", awaitSeconds=60)
        self.assertEqual(made["status"], "succeeded", made)
        return made["candidateId"], made["candidate"]["stateDigest"]

    def slab(self, run_id):
        model = self.client.get("/api/construction/model", params={"run": run_id}).json()
        return next(row["bounds"] for row in model["entities"] if row["id"] == "slab")

    def test_a_level_is_added_and_moved_from_the_chat_and_its_slab_follows(self):
        base = self.call("POST", "/api/project/modeling")
        self.assertEqual(base["levels"], [{"levelId": "ground", "elevation": 0.0}])
        added = self.call("POST", "/api/proposals/elevation", {
            "stateDigest": base["stateDigest"], "action": "set-datum", "levelId": "level-2", "value": 3.5})
        self.assertEqual(added["impact"]["direct"], ["entity:level-2"])
        self.assertNotIn("decisionOperator", added, "the answer keeps the checked change, not its operator")
        # An empty project's first run needs an element: the slab continues the same chain, one candidate.
        stood = self.call("POST", "/api/proposals/construction", {
            "stateDigest": base["stateDigest"], "sourceProposalId": added["proposalId"],
            "script": 'slab = extrude(rect(0, 0, 6, 4), 0.3, at=level("level-2"))\n'})
        built, digest = self.candidate(stood)
        self.assertEqual(self.slab(built), [[0.0, 3.5, 0.0], [6.0, 3.8, 4.0]])

        raised = self.call("POST", "/api/proposals/elevation", {
            "stateDigest": digest, "sourceRunId": built, "action": "set-datum", "levelId": "level-2", "value": 4.0})
        self.assertEqual(raised["impact"]["direct"], ["entity:level-2"])
        self.assertIn("entity:slab-body", raised["impact"]["propagated"])
        after, _ = self.candidate(raised)
        self.assertEqual(self.slab(after), [[0.0, 4.0, 0.0], [6.0, 4.3, 4.0]])
        frame = self.call("GET", f"/api/state/frame?run={after}")
        levels = {row["levelId"]: row["elevation"] for row in frame["levels"]}
        self.assertEqual(levels["level-2"], 4.0)

    def test_an_element_elevation_and_the_other_replaced_routes_are_refused_before_anything_is_sent(self):
        base = self.call("POST", "/api/project/modeling")
        self.sent.clear()
        for body in ({"stateDigest": base["stateDigest"], "action": "set-top", "elementId": "mass", "value": 4.0},
                     {"stateDigest": base["stateDigest"], "action": "bind-base", "elementId": "mass",
                      "reference": {"kind": "level", "id": "ground"}},
                     {"stateDigest": base["stateDigest"], "levelId": "level-2", "value": 3.0}):
            with self.subTest(body=body):
                detail = self.refused("CHAT_TOOL_INVALID", "POST", "/api/proposals/elevation", body)
                self.assertIn("POST /api/proposals/construction", detail)
                self.assertIn("set-datum", detail)
        for name in ("sketch", "transform", "push-pull", "delete"):
            with self.subTest(route=name):
                detail = self.refused("CHAT_TOOL_UNAVAILABLE", "POST", f"/api/proposals/{name}", {"stateDigest": base["stateDigest"]})
                self.assertIn("POST /api/proposals/construction with one script", detail)
        self.assertEqual(self.sent, [], "a refused request reaches no Studio")

    def test_the_elevation_contract_the_chat_reads_offers_set_datum_only(self):
        contract = self.call("POST", "/api/proposals/elevation", name="studio_schema")
        request = contract["components"]["schemas"]["ElevationEditRequestDto"]["properties"]
        self.assertEqual(request["action"]["enum"], ["set-datum"])
        self.assertFalse({"elementId", "reference"} & set(request))
        self.assertTrue({"levelId", "value", "stateDigest"} <= set(request))
        listed = tool_calls.call_tool(self.hub, self.session["id"], "studio_schema",
                                      {"method": "POST", "pathPrefix": "/api/proposals", "limit": 50})
        self.assertIn("/api/proposals/elevation", {row["path"] for row in listed["actions"]})


if __name__ == "__main__":
    unittest.main()
