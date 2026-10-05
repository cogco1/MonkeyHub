"""A disconnected member computes locally, then its named candidate line catches up."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from monkeyhub_api.app.composition import HubSettings, create_app
from monkeyhub_api.runtime.worker_http import request_http
from archflow.project.repository import FilesystemProjectRepository
from monkeycad.backends.occt.kernel import occt_available
from .collaboration_support import seed_collaboration_project
from .support import PROJECT_ID

ROOT = Path(__file__).resolve().parents[3]
BINARY = ROOT / "packages/monkeymesh/native/target/debug/monkeymesh-tcp"

@unittest.skipUnless(BINARY.exists() and occt_available(), "Real Iroh and OCCT required")
class TeamCandidateCatchupTests(unittest.TestCase):
    def test_offline_candidate_lineage_and_named_actor_heads(self):
        with tempfile.TemporaryDirectory(prefix="team-candidate-") as directory:
            root = Path(directory)
            with patch.dict(os.environ, {"APPDATA": str(root / "roaming")}), patch("monkeyhub_api.settings.credentials.saved", return_value=None):
                source, replica = root / "owner-projects" / PROJECT_ID, root / "member-projects" / PROJECT_ID
                seed = seed_collaboration_project(source)
                owner = create_app(HubSettings(runtime_root=root / "owner", port=19791), source_root=ROOT)
                member = create_app(HubSettings(runtime_root=root / "member", port=19792), source_root=ROOT)
                class FixtureSecrets:
                    def __init__(self): self.value = {}
                    def read(self): return json.loads(json.dumps(self.value))
                    def write(self, value): self.value = json.loads(json.dumps(value))
                owner.state.teams.settings.secrets = FixtureSecrets()
                member.state.teams.settings.secrets = FixtureSecrets()
                owner.state.teams.test_loopback = member.state.teams.test_loopback = True
                owner_client = TestClient(owner, base_url="http://127.0.0.1:19791")
                member_client = TestClient(member, base_url="http://127.0.0.1:19792")
                def wait_for(predicate, description):
                    deadline = time.monotonic() + 90
                    while time.monotonic() < deadline:
                        value = predicate()
                        if value:
                            return value
                        time.sleep(.2)
                    logs = "\n".join(p.read_text(errors="replace")[-4000:] for p in root.rglob("*.log"))
                    self.fail(description + "\n" + logs)
                def runtime(app, path):
                    return wait_for(lambda: app.state.applications.status("monkeyarch", project_dir=str(path)).apiUrl, "Runtime not ready")
                def request(url, path, body=None, method=None, status=200):
                    result = request_http(url, path, method or ("POST" if body is not None else "GET"),
                        None if body is None else json.dumps(body).encode(), {"Content-Type": "application/json"}, timeout=90)
                    self.assertEqual(result.status, status, result.body.decode(errors="replace"))
                    return result.json()
                try:
                    shared = owner_client.post("/api/team/share", json={"projectDir": str(source), "name": "Architect Alice", "role": "designer"})
                    self.assertEqual(shared.status_code, 200, shared.text)
                    owner_url = runtime(owner, source)
                    stage = request(owner_url, "/api/design-stages/initialize", {"projectId": PROJECT_ID, "modelSource": seed["modelSource"]}, status=201)
                    joined = member_client.post("/api/team/join", json={"projectDir": str(replica), "name": "Architect Bob", "invitation": shared.json()["invitation"]})
                    self.assertEqual(joined.status_code, 200, joined.text)
                    wait_for(lambda: member_client.get(f"/api/team/projects/{PROJECT_ID}").json().get("status") == "synced", "Replica not synced")
                    member_url = runtime(member, replica)
                    before = FilesystemProjectRepository.open(replica).read_head()
                    # Explicitly disconnect the owner Runtime. Its Hub's limited ingress remains, returning 503.
                    owner.state.applications.stop("monkeyarch", project_dir=str(source))
                    proposal = request(member_url, "/api/proposals", {
                        "stateDigest": stage["modelSource"]["stateDigest"], "sourceRunId": stage["candidateId"],
                        "sourceStageRef": stage["stageRef"], "targetComponentId": "portico", "elementId": "portico-base", "utterance": "set height to 0.8"}, status=201)
                    started = request(member_url, f"/api/proposals/{proposal['proposalId']}/candidate", {}, status=202)
                    job = wait_for(lambda: (value if (value := request(member_url, f"/api/jobs/{started['jobId']}"))["status"] in {"succeeded", "failed"} else None), "Local candidate did not finish")
                    self.assertEqual(job["status"], "succeeded", job)
                    candidate = started["candidateId"]
                    current = request(member_url, "/api/working-draft")
                    request(member_url, "/api/working-draft", {"projectId": PROJECT_ID, "baseRevisionSha256": current["revisionSha256"], "runId": candidate}, "PUT")
                    predecessor = candidate
                    continued = request(member_url, f"/api/candidates/{predecessor}")
                    proposal = request(member_url, "/api/proposals", {
                        "stateDigest": continued["stateDigest"], "sourceRunId": predecessor,
                        "sourceStageRef": stage["stageRef"], "targetComponentId": "portico", "elementId": "portico-cornice", "utterance": "set height to 0.4"}, status=201)
                    started = request(member_url, f"/api/proposals/{proposal['proposalId']}/candidate", {}, status=202)
                    job = wait_for(lambda: (value if (value := request(member_url, f"/api/jobs/{started['jobId']}"))["status"] in {"succeeded", "failed"} else None), "Continued offline candidate did not finish")
                    self.assertEqual(job["status"], "succeeded", job)
                    candidate = started["candidateId"]
                    current = request(member_url, "/api/working-draft")
                    request(member_url, "/api/working-draft", {"projectId": PROJECT_ID, "baseRevisionSha256": current["revisionSha256"], "runId": candidate}, "PUT")
                    chosen = candidate
                    current = request(member_url, "/api/working-draft")
                    request(member_url, "/api/working-draft", {"projectId": PROJECT_ID, "baseRevisionSha256": current["revisionSha256"], "runId": stage["candidateId"]}, "PUT")
                    alternative = request(member_url, "/api/proposals", {
                        "stateDigest": stage["modelSource"]["stateDigest"], "sourceRunId": stage["candidateId"],
                        "sourceStageRef": stage["stageRef"], "targetComponentId": "portico", "elementId": "portico-base", "utterance": "set height to 0.95"}, status=201)
                    alternate_job = request(member_url, f"/api/proposals/{alternative['proposalId']}/candidate", {}, status=202)
                    completed = wait_for(lambda: (value if (value := request(member_url, f"/api/jobs/{alternate_job['jobId']}"))["status"] in {"succeeded", "failed"} else None), "Divergent offline sibling did not finish")
                    self.assertEqual(completed["status"], "succeeded", completed)
                    sibling = alternate_job["candidateId"]
                    current = request(member_url, "/api/working-draft")
                    request(member_url, "/api/working-draft", {"projectId": PROJECT_ID, "baseRevisionSha256": current["revisionSha256"], "runId": chosen}, "PUT")
                    actor = member.state.teams.settings.identity()["actorId"]
                    owner_actor = owner.state.teams.settings.identity()["actorId"]
                    local = FilesystemProjectRepository.open(replica)
                    self.assertEqual(local.sync_actor_line(actor, owner_actor_id=owner_actor)["current"], candidate)
                    self.assertEqual(local.read_head(), before)
                    owner.state.applications.start("monkeyarch", project_dir=str(source))
                    owner_url = runtime(owner, source)
                    remote = FilesystemProjectRepository.open(source)
                    wait_for(lambda: remote.sync_actor_line(actor, owner_actor_id=owner_actor)["current"] == candidate, "Offline candidate head was not uploaded")
                    graph = request(owner_url, "/api/worktrees")
                    self.assertTrue(any(row["actorId"] == actor and row["displayName"] == "Architect Bob" and row["runId"] == candidate for row in graph["actorHeads"]), graph)
                    self.assertIn(candidate, remote.run_ids())
                    self.assertIn(predecessor, remote.export_transfer(run_id=candidate)["run_ids"])
                    self.assertNotIn(sibling, remote.export_transfer(run_id=candidate)["run_ids"])
                    self.assertIn(sibling, remote.export_sync_transfer()["run_ids"])
                    wait_for(lambda: member_client.get(f"/api/team/projects/{PROJECT_ID}").json().get("status") == "synced", "Sibling inventory did not settle")
                    def uploads():
                        return sum(path.read_text(errors="replace").count('POST /api/sync/candidates HTTP/1.1') for path in (root / "owner" / "logs").glob("*.log"))
                    count = uploads()
                    time.sleep(6)
                    self.assertEqual(uploads(), count, "Unchanged siblings must not be uploaded again on the next sync")
                    self.assertIn(stage["candidateId"], remote.export_transfer(run_id=candidate)["run_ids"])
                    self.assertEqual(remote.read_head(), before)
                    self.assertEqual(remote.read_design_branches(), local.read_design_branches())
                    # Peer ingress cannot invoke owner computation or Hub settings.
                    connection = member.state.teams.connections[PROJECT_ID]
                    token = member.state.teams.settings.token(PROJECT_ID)
                    for path in ("/api/settings/user", "/api/proposals"):
                        response = request_http(connection.url, path, "POST", b"{}", {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
                        self.assertEqual(response.status, 403, response.body)
                    # Existing exact-base Stage acceptance is still separately permissioned.
                    request(member_url, f"/api/candidates/{candidate}/accept", {"projectId": PROJECT_ID, "branchId": "main", "expectedHeadStageRef": stage["stageRef"]}, status=403)
                finally:
                    for app in (member, owner):
                        app.state.teams.close()
                        app.state.applications.shutdown()
                        app.state.runtimes.shutdown()
                        app.state.chats.shutdown()
