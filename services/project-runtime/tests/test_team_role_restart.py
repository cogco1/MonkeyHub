"""The last verified restriction survives an offline Runtime restart."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from archflow.project.repository import FilesystemProjectRepository
from project_runtime.binding import release_bound_project
from project_runtime.errors import StudioError
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings
from project_runtime.sync_state import MemberRoleObservation
from project_runtime.synchronization import TeamSynchronization


class RoleRestartTests(unittest.TestCase):
    def test_verified_downgrade_and_revocation_survive_restart_and_only_upstream_regrants(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = FilesystemProjectRepository.initialize(root / "sample", project_id="sample", initial_state={})
            settings = StudioSettings(project_dir=root / "sample", cache_dir=root / "cache", cad_export="off",
                sync_url="http://127.0.0.1:1", sync_token="synthetic-member-token", sync_project_id="sample",
                team_actor_id="member", team_actor_name="Member", team_role="designer", sync_automatic=True,
                team_state_file=root / "team-state" / "sample.json")
            class Client:
                project_id = "sample"
                role = "designer"
                denied = False
                def request(self, method, path, *args, **kwargs):
                    if self.denied:
                        raise StudioError(401, "MEMBER_REVOKED", "synthetic revoked membership")
                    if path.endswith("identity"):
                        return {"actorId": "member", "name": "Member", "role": self.role}
                    return {"lines": []}
                def manifest(self): return repo.export_transfer(include_contents=False)
            client = Client()
            app = create_app(settings)
            worker = TeamSynchronization(app.state)
            def cold_write_status():
                # TestClient without a context invokes no network/lifespan work.
                offline = create_app(settings)
                try:
                    return TestClient(offline).post("/api/proposals", json={}).status_code
                finally:
                    release_bound_project(offline.state)
                    offline.state.jobs.shutdown()
            # A missing observation never trusts the stale original invitation for writes.
            self.assertEqual(worker.role, "viewer")
            self.assertEqual(cold_write_status(), 403)
            try:
                with patch("project_runtime.synchronization.SharedProjectClient", return_value=client):
                    worker.step()
                    self.assertEqual(MemberRoleObservation(settings).read(), "designer")
                    self.assertEqual(cold_write_status(), 422)
                    client.role = "viewer"
                    worker.step()
                    self.assertEqual(settings.team_role, "designer")
                    self.assertEqual(cold_write_status(), 403)
                    client.denied = True
                    worker.start()
                    deadline = time.monotonic() + 3
                    while MemberRoleObservation(settings).read() != "revoked":
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(.01)
                    worker.stop()
                    self.assertEqual(cold_write_status(), 403)
                    client.denied, client.role = False, "moderator"
                    restarted = TeamSynchronization(app.state)
                    self.assertEqual(restarted.role, "revoked")
                    restarted.step()
                    self.assertEqual(MemberRoleObservation(settings).read(), "moderator")
                    self.assertEqual(cold_write_status(), 422)
                saved = settings.team_state_file.read_text()
                self.assertNotIn(settings.sync_token, saved)
                settings.team_state_file.write_text('{"role": []}')
                self.assertEqual(cold_write_status(), 403)
                settings.team_state_file.unlink()
                self.assertEqual(cold_write_status(), 403)
            finally:
                release_bound_project(app.state)
                app.state.jobs.shutdown()
