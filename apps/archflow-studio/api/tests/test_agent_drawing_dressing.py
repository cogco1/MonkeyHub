"""The Hub Agent places entourage on a real cut plan through its bound tool (#244).

The plan, its projection and its dressing owner are the Studio's own. Only the
Hub's transport is answered in process: its reads of its own chat and apps, its
runtime admission of a write, and the bound Studio itself, reached through this
TestClient exactly as the forwarded request would reach it.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi.testclient import TestClient

from archflow.adapters.occt_backend import occt_available
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_candidate import CandidateTestCase
from .test_drawing_plans import room_edit

HUB = "http://127.0.0.1:8790"
STUDIO = "http://127.0.0.1:8791"


def hub_chat():
    """The Hub's chat adapter, which lives beside this API rather than inside it."""

    with patch.object(sys, "path", [str(Path(__file__).resolve().parents[3] / "monkeyhub/api"), *sys.path]):
        from monkeyhub_api import chat
        from monkeyhub_api.models import HubFailure
    return chat, HubFailure


def files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class AgentDressingTests(CandidateTestCase):
    def setUp(self):
        super().setUp()
        self.client.close()
        self.project = self.root / PROJECT_ID
        self.app = create_app(StudioSettings(project_dir=self.project, cad_export="occt"))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        request = self.client.post("/api/proposals", json={"projectId": PROJECT_ID,
            "stateDigest": self.state_digest, "sourceRunId": REFERENCE_RUN_ID, "semanticEdit": room_edit()})
        self.assertEqual(request.status_code, 201, request.text)
        result = self.start(request.json()["proposalId"])
        self.assertEqual(self.finished(result["jobId"])["status"], "succeeded")
        candidate = self.client.get(f"/api/candidates/{result['candidateId']}").json()
        model = next(row["modelSource"] for row in candidate["artifacts"] if row["format"] == "3dm")
        accepted = self.client.post("/api/design-stages/initialize", json={"projectId": PROJECT_ID, "modelSource": model})
        self.assertEqual(accepted.status_code, 201, accepted.text)
        self.stage = accepted.json()
        self.chat, self.HubFailure = hub_chat()
        project_id, project_dir = self.chat._project(str(self.project))
        self.session = {"id": str(uuid4()), "status": "running", "projectId": project_id, "projectDir": project_dir,
                        "messages": [{"id": str(uuid4()), "role": "user", "content": "Add people and a tree to the plan."}]}
        runtime = uuid5(NAMESPACE_URL, f"{project_id}:{os.path.normcase(str(Path(project_dir).resolve()))}")
        self.admitted = f"/api/runtime/projects/{runtime}/studio"
        self.writes = []

    def forward(self, base, path, method="GET", body=None, timeout=None, *, headers=None):
        """What the Hub's own services answer, and the bound Studio itself."""

        if path == f"/api/chat/sessions/{self.session['id']}":
            return self.session
        if path.startswith("/api/apps?"):
            return [{"appId": "monkeyarch", "state": "running", "apiUrl": STUDIO + "/", "processId": 7}]
        if path == "/api/health":
            return {"processId": 7, "sourceRevision": "same-revision"}
        if path == "/api/project":
            return {"projectId": self.session["projectId"], "projectDir": self.session["projectDir"]}
        if path.startswith(self.admitted):
            # A write the runtime admits under its own operation identity, then forwards.
            self.assertEqual(base, HUB)
            self.assertIn("Idempotency-Key", headers)
            path = path.removeprefix(self.admitted)
            self.writes.append(path)
        else:
            self.assertEqual((base, headers), (STUDIO, None), path)
        reply = self.client.request(method, path, json=body)
        answer = reply.json()
        if reply.status_code >= 400:
            raise self.HubFailure(reply.status_code, answer.get("code", "CHAT_TOOL_FAILED"), str(answer.get("detail")))
        return answer

    def agent(self, method, path, body=None):
        """One studio_request exactly as the Agent's CLI makes it."""

        with patch.object(self.chat, "_request_json", side_effect=self.forward):
            return self.chat.call_tool(HUB, self.session["id"], "studio_request",
                                       {"method": method, "path": path, **({} if body is None else {"body": body})})

    def test_three_objects_the_agent_places_stay_editable_and_a_refused_batch_writes_nothing(self):
        plan = {"projectId": PROJECT_ID, "sourceStageRef": self.stage["stageRef"], "drawingId": "room-plan",
                "cutHeight": 1.2, "bottom": 0, "scaleDenominator": 50}
        first = self.agent("POST", "/api/drawings/plans", plan)
        objects = [{"id": name, "assetId": asset, "positionUv": position, "size": size, "flipped": False,
                    "anchorObjectId": None}
                   for name, asset, position, size in (("person-a", "person-plan", [1, 1], .6),
                                                       ("person-b", "person-plan", [3, 1], .6),
                                                       ("tree-a", "tree-plan", [2, 3], 1.2))]
        placed = self.agent("POST", "/api/drawings/plans", {
            **plan, "previousRevisionRef": first["revisionRef"],
            "dressingOperations": [{"op": "insert", "id": row["id"], "object": row} for row in objects]})
        self.assertEqual(placed["viewRecipe"]["dressing"], objects)
        self.assertEqual(len({row["id"] for row in placed["viewRecipe"]["dressing"]}), 3)
        # Each one is edited by its own id, and the others stay as they were.
        edited = self.agent("POST", "/api/drawings/plans", {
            **plan, "previousRevisionRef": placed["revisionRef"],
            "dressingOperations": [{"op": "move", "id": "person-b", "positionUv": [3, 2]},
                                   {"op": "flip", "id": "tree-a", "flipped": True}]})
        self.assertEqual(edited["viewRecipe"]["dressing"],
                         [objects[0], {**objects[1], "positionUv": [3, 2]}, {**objects[2], "flipped": True}])
        revision = {"runId": edited["runId"], "assetSha256": edited["assetSha256"], "revisionRef": edited["revisionRef"]}
        vector = self.agent("GET", "/api/drawings/plans/vector?" + urlencode(revision))
        for name in ("person-a", "person-b", "tree-a"):
            self.assertIn(f'data-dressing="{name}"', vector["svg"])
        status = self.agent("POST", "/api/drawings/plans/status", revision)
        self.assertEqual([(row["id"], row["status"]) for row in status["dressing"]],
                         [("person-a", "resolved"), ("person-b", "resolved"), ("tree-a", "resolved")])
        before, documents = files(self.project), self.client.get("/api/documents").json()
        with self.assertRaises(self.HubFailure) as refused:
            self.agent("POST", "/api/drawings/plans", {
                **plan, "previousRevisionRef": edited["revisionRef"],
                "dressingOperations": [{"op": "move", "id": "person-a", "positionUv": [2, 2]},
                                       {"op": "delete", "id": "missing"}]})
        self.assertEqual((refused.exception.status, refused.exception.error.code), (422, "DRAWING_DRESSING_MISSING"))
        self.assertEqual(self.client.get("/api/documents").json(), documents)
        self.assertEqual(files(self.project), before, "a refused batch writes nothing")
        self.assertEqual(self.writes, ["/api/drawings/plans"] * 4, "only the drawing writes are admitted")


if __name__ == "__main__":
    unittest.main()
