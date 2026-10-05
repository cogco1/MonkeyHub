"""Two isolated Hub instances and real owned Runtimes over real loopback Iroh.

Synthetic local fixtures only; this is not the two-physical-machine acceptance.
"""
import base64
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from archflow.project.repository import FilesystemProjectRepository
from monkeyhub_api.app.composition import HubSettings, create_app
from monkeyhub_api.settings.team import TeamSettings

ROOT = Path(__file__).resolve().parents[4]
BINARY = ROOT / "packages/monkeymesh/native/target/debug/monkeymesh-tcp"


class FixtureSecrets:
    def __init__(self):
        self.value = {}
    def read(self):
        return json.loads(json.dumps(self.value))
    def write(self, value):
        self.value = json.loads(json.dumps(value))


class MembershipTests(unittest.TestCase):
    def test_device_persists_and_invitation_is_single_use_and_peer_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TeamSettings(root, secret_store=FixtureSecrets() if os.name == "nt" else None)
            device = store.identity()
            self.assertEqual(TeamSettings(root, secret_store=store.secrets).identity(), device)
            self.assertEqual(TeamSettings(root, secret_store=store.secrets).transport_key("sample"), store.transport_key("sample"))
            self.assertNotEqual(store.transport_key("sample"), store.transport_key("other"))
            store.put_project("sample", {"members": {}, "invites": {}})
            code = store.invite("sample", "designer")
            token = "a" * 43
            member = store.redeem("sample", code, "peer-one", "actor-one", "Alice", token)
            self.assertEqual(member["role"], "designer")
            self.assertEqual(store.redeem("sample", code, "peer-one", "actor-one", "Alice", token), member)
            with self.assertRaises(Exception):
                store.redeem("sample", code, "peer-two", "actor-two", "Bob", "b" * 43)
            self.assertNotIn(token, store.path.read_text())
            self.assertNotIn(code, store.path.read_text())


@unittest.skipUnless(BINARY.exists(), "Build the official monkeymesh transport first")
class TwoHubTests(unittest.TestCase):
    def test_join_clone_permissions_and_revoke(self):
        with tempfile.TemporaryDirectory(prefix="monkeyhub-team-") as temporary:
            root = Path(temporary)
            with patch.dict(os.environ, {"APPDATA": str(root / "roaming")}), patch("monkeyhub_api.settings.credentials.saved", return_value=None):
                owner_root, member_root = root / "owner", root / "member"
                project, replica = root / "owner-projects" / "team-test", root / "member-projects" / "team-test"
                FilesystemProjectRepository.initialize(project, project_id="team-test", initial_state={"project_id": "team-test", "version": 0})
                owner = create_app(HubSettings(runtime_root=owner_root, port=18791), source_root=ROOT)
                member = create_app(HubSettings(runtime_root=member_root, port=18792), source_root=ROOT)
                owner.state.teams.settings.secrets = FixtureSecrets()
                member.state.teams.settings.secrets = FixtureSecrets()
                owner.state.teams.test_loopback = member.state.teams.test_loopback = True
                oc, mc = TestClient(owner, base_url="http://127.0.0.1:18791"), TestClient(member, base_url="http://127.0.0.1:18792")
                try:
                    owner.state.applications.start("monkeyarch", project_dir=str(project))
                    deadline = time.monotonic() + 30
                    while not owner.state.applications.status("monkeyarch", project_dir=str(project)).apiUrl:
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(.05)
                    original_pid = owner.state.applications.status("monkeyarch", project_dir=str(project)).processId
                    shared = oc.post("/api/team/share", json={"projectDir": str(project), "name": "Owner", "role": "viewer"})
                    self.assertEqual(shared.status_code, 200, shared.text)
                    self.assertNotEqual(owner.state.applications.status("monkeyarch", project_dir=str(project)).processId, original_pid)
                    joined = mc.post("/api/team/join", json={"projectDir": str(replica), "name": "Member", "invitation": shared.json()["invitation"]})
                    self.assertEqual(joined.status_code, 200, joined.text)
                    deadline = time.monotonic() + 45
                    while time.monotonic() < deadline:
                        status = mc.get("/api/team/projects/team-test").json()
                        if status["status"] == "synced":
                            break
                        time.sleep(.2)
                    else:
                        logs = "\n".join(p.read_text(errors="replace")[-6000:] for p in root.rglob("*.log"))
                        self.fail(f"Clone did not finish: {status}\n{logs}")
                    self.assertEqual(FilesystemProjectRepository.open(replica).read_head(), FilesystemProjectRepository.open(project).read_head())
                    from monkeyhub_api.runtime.worker_http import request_http
                    token = member.state.teams.settings.token("team-test")
                    connection = member.state.teams.connections["team-test"]
                    health = request_http(connection.url, "/api/health", headers={"Authorization": "Bearer " + token}).json()
                    self.assertNotIn("projectDir", health)
                    self.assertNotIn("processId", health)
                    local_url = None
                    deadline = time.monotonic() + 10
                    while not local_url:
                        self.assertLess(time.monotonic(), deadline)
                        local_url = member.state.applications.status("monkeyarch", project_dir=str(replica)).apiUrl
                        if not local_url: time.sleep(.05)
                    self.assertEqual(request_http(local_url, "/api/proposals", "POST", b"{}", {"Content-Type": "application/json"}).status, 403)
                    actor = member.state.teams.settings.identity()["actorId"]
                    self.assertEqual(oc.put(f"/api/team/projects/team-test/members/{actor}", json={"role": "revoked"}).status_code, 200)
                    connection = member.state.teams.connections["team-test"]
                    from monkeyhub_api.runtime.worker_http import request_http
                    token = member.state.teams.settings.token("team-test")
                    # An existing connection must lose authority as well as new connections.
                    try:
                        result = request_http(connection.url, "/api/sync/manifest", headers={"Authorization": "Bearer " + token}, timeout=5)
                        self.assertIn(result.status, {401, 403})
                    except OSError:
                        pass  # The transport can close it before HTTP is received.
                    self.assertTrue((replica / "HEAD").exists())
                finally:
                    for app in (member, owner):
                        app.state.teams.close()
                        app.state.applications.shutdown()
                        app.state.runtimes.shutdown()
                        app.state.chats.shutdown()
