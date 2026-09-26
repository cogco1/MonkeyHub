"""Who asked for a drawing revision, and what the revision chain says was corrected (05-S2).

The real cases draw an imported model's cut plans, so every revision is a real
one; the Agent's requests go through the Hub's own studio_request, answered by
this Studio in process exactly as the forwarded request would reach it.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi.testclient import TestClient

from archflow.adapters.occt_backend import occt_available
from archflow.project.refs import record_ref_from_uri
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project
from .test_agent_drawing_dressing import hub_chat

HUB = "http://127.0.0.1:8790"
STUDIO = "http://127.0.0.1:8791"
MODEL = Path(__file__).parent / "fixtures/model-source-a.3dm"


class ImportedPlans(unittest.TestCase):
    """One project, one imported model, and the cut plans a person or the Agent asks for."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository, _ = make_project(self.root)
        self.project = self.root / PROJECT_ID
        self.settings = StudioSettings(project_dir=self.project, reference_run=REFERENCE_RUN_ID, cad_export="off")
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)
        self.asset = self.upload(MODEL.read_bytes())

    def upload(self, data):
        response = self.client.post("/api/model-assets", json={
            "projectId": PROJECT_ID, "fileName": "model.3dm", "contentBase64": base64.b64encode(data).decode()})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def plan(self, drawing, previous=None, *, asset=None, **changes):
        """A cut-plan request for one drawing of an imported model, continuing ``previous`` when given."""
        asset = asset or self.asset
        return {"projectId": PROJECT_ID, "drawingId": drawing, "cutHeight": 1.2, "bottom": 0, "scaleDenominator": 50,
                "sourceAsset": {"runId": asset["runId"], "assetSha256": asset["sha256"]},
                **({} if previous is None else {"previousRevisionRef": previous["revisionRef"]}), **changes}

    def draw(self, drawing, previous=None, **changes):
        """One revision, asked for directly, as the Drawing canvas asks."""
        response = self.client.post("/api/drawings/plans", json=self.plan(drawing, previous, **changes))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def agent(self, body):
        """One cut-plan request exactly as the Agent's CLI makes it, through the Hub's studio_request."""
        chat, failure = hub_chat()
        project_id, project_dir = chat._project(str(self.project))
        session = {"id": str(uuid4()), "status": "running", "projectId": project_id, "projectDir": project_dir,
                   "messages": [{"id": str(uuid4()), "role": "user", "content": "Open up the hatch on this plan."}]}
        runtime = uuid5(NAMESPACE_URL, f"{project_id}:{os.path.normcase(str(Path(project_dir).resolve()))}")
        admitted = f"/api/runtime/projects/{runtime}/studio"

        def forward(base, path, method="GET", body=None, timeout=None, *, headers=None):
            if path == f"/api/chat/sessions/{session['id']}":
                return session
            if path.startswith("/api/apps?"):
                return [{"appId": "monkeyarch", "state": "running", "apiUrl": STUDIO + "/", "processId": 7}]
            if path == "/api/health":
                return {"processId": 7, "sourceRevision": "same-revision"}
            if path == "/api/project":
                return {"projectId": project_id, "projectDir": project_dir}
            # The plan write, admitted by the Hub's runtime and forwarded as it holds it.
            self.assertEqual((base, method, path), (HUB, "POST", admitted + "/api/drawings/plans"))
            reply = self.client.post("/api/drawings/plans", json=body)
            if reply.status_code >= 400:
                raise failure(reply.status_code, reply.json().get("code", "CHAT_TOOL_FAILED"), str(reply.json().get("detail")))
            return reply.json()

        with patch.object(chat, "_request_json", side_effect=forward):
            return chat.call_tool(HUB, session["id"], "studio_request",
                                  {"method": "POST", "path": "/api/drawings/plans", "body": body})


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class WhoAskedTests(ImportedPlans):
    def test_a_revision_keeps_whether_a_person_or_the_agent_asked_and_one_that_did_not_say_reads_null(self):
        # A request that names no kind is retained as every revision drawn before
        # sourceKind existed was: without it.
        unknown = self.draw("plan-a")
        person = self.draw("plan-a", unknown, hatchSpacingMm=3, sourceKind="human")
        agent = self.agent(self.plan("plan-a", person, hatchSpacingMm=4))
        self.assertEqual([row["sourceKind"] for row in (unknown, person, agent)], [None, "human", "agent"])
        receipts = [self.repository.load_json(record_ref_from_uri(row["revisionRef"], PROJECT_ID))
                    for row in (unknown, person, agent)]
        self.assertEqual([receipt.get("sourceKind") for receipt in receipts], [None, "human", "agent"])
        self.assertNotIn("sourceKind", person["viewRecipe"])
        self.assertEqual(agent["attribution"]["origin"], "studio", "who asked is still the boundary's own record")
        # The kind never makes or tells revisions apart: the Agent asking for
        # exactly what a person drew gets that person's revision, as they asked.
        with patch("archflow_studio_api.application.drawing_plans.freeze_cut_plan",
                   side_effect=AssertionError("an identical request is the retained revision")):
            self.assertEqual(self.agent(self.plan("plan-a", unknown, hatchSpacingMm=3)), person)
        with TestClient(create_app(self.settings)) as client:
            cold = {row["revisionRef"]: row for row in client.get("/api/documents").json()["documents"]}
        self.assertEqual([cold[row["revisionRef"]] for row in (unknown, person, agent)], [unknown, person, agent])
        for kind in ("robot", "", "Human"):
            response = self.client.post("/api/drawings/plans", json=self.plan("plan-a", agent, sourceKind=kind))
            self.assertEqual(response.status_code, 422, response.text)
