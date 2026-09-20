"""Actor grants cover existing routes, and a shared process cannot execute work."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request

from archflow_studio_api.application.authentication import read_actor_credentials, require_actor
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import SettingsError, StudioSettings
from archflow_studio_api.transport.errors import StudioError

from .support import PROJECT_ID, make_project


class CollaborationAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        make_project(self.root)
        self.actors_file = self.root / "actors.json"
        self.actors_file.write_text(json.dumps({"actors": [
            {"actor_id": actor, "token": token, "projects": {project: actions}}
            for actor, token, project, actions in (
                ("reader", "reader-secret", PROJECT_ID, ["read"]),
                ("designer", "designer-secret", PROJECT_ID, ["read", "propose"]),
                ("reviewer", "reviewer-secret", PROJECT_ID, ["read", "accept"]),
                ("issuer", "issuer-secret", PROJECT_ID, ["release"]),
                ("outsider", "outsider-secret", "other-project", ["read", "propose", "accept"]),
            )
        ]}), encoding="utf-8")

    def settings(self, *, shared: bool = True, **overrides) -> StudioSettings:
        values = dict(project_dir=self.root / PROJECT_ID, cad_export="off", mode="remote",
                      origins=("https://studio.example",), actors_file=self.actors_file,
                      service_role="shared_project" if shared else "runtime")
        values.update(overrides)
        return StudioSettings(**values)

    def client(self, *, shared: bool = True, **overrides) -> TestClient:
        app = create_app(self.settings(shared=shared, **overrides))
        self.addCleanup(app.state.jobs.shutdown)
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def headers(self, actor: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {actor}-secret"}

    def test_actor_reads_only_the_project_it_is_granted(self) -> None:
        client = self.client()
        self.assertEqual(client.get("/api/project", headers=self.headers("reader")).status_code, 200)
        denied = client.get("/api/project", headers=self.headers("outsider"))
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["code"], "ACTION_FORBIDDEN")

    def test_missing_wrong_and_legacy_tokens_do_not_bypass_actor_config(self) -> None:
        client = self.client(api_token="old-shared-secret")
        for header in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer old-shared-secret"}):
            response = client.get("/api/project", headers=header)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")

    def test_reader_cannot_use_any_existing_runtime_write_route(self) -> None:
        client = self.client(shared=False)
        checked = 0
        for path, methods in client.get("/openapi.json").json()["paths"].items():
            for method in set(methods) & {"post", "put", "patch", "delete"}:
                if not path.startswith("/api/"):
                    continue
                checked += 1
                with self.subTest(method=method, path=path):
                    response = client.request(method, path, headers=self.headers("reader"), json={"actor_id": "reviewer"})
                    self.assertEqual(response.status_code, 403)
        self.assertGreater(checked, 10)

    def test_accept_does_not_follow_from_propose_or_release(self) -> None:
        client = self.client()
        for path in ("/api/candidates/candidate-1/accept", "/api/design-stages/initialize", "/api/design-branches"):
            for actor in ("reader", "designer", "issuer"):
                with self.subTest(path=path, actor=actor):
                    self.assertEqual(client.post(path, headers=self.headers(actor), json={}).status_code, 403)
            # A permitted caller reaches DTO validation, rather than the grant gate.
            self.assertEqual(client.post(path, headers=self.headers("reviewer"), json={}).status_code, 422)

    def test_parameter_lock_decisions_require_accept_and_use_authenticated_actor(self) -> None:
        client = self.client(shared=False)
        state = client.get("/api/state", headers=self.headers("reader")).json()
        body = {"stateDigest": state["stateDigest"], "parameterKeys": ["module"], "action": "lock"}
        path = "/api/proposals/parameter-locks"
        for actor in ("reader", "designer", "issuer", "outsider"):
            with self.subTest(actor=actor):
                response = client.post(path, headers=self.headers(actor), json=body)
                self.assertEqual(response.status_code, 403, response.text)
        allowed = client.post(path, headers=self.headers("reviewer"), json=body)
        self.assertEqual(allowed.status_code, 201, allowed.text)
        self.assertEqual(allowed.json()["change"]["edits"]["parameters"][0]["lock_authority"], "reviewer")
        for actor in ("designer", "issuer"):
            self.assertEqual(client.post(path, headers=self.headers(actor), json={**body, "action": "unlock"}).status_code, 403)
        self.assertEqual(client.post(path, headers=self.headers("reviewer"), json={**body, "lockAuthority": "forged"}).status_code, 422)
        self.assertEqual(self.client().post(path, headers=self.headers("reviewer"), json=body).status_code, 403)

    def test_body_cannot_replace_actor_or_project_scope(self) -> None:
        client = self.client(shared=False)

        @client.app.post("/api/actor-check")
        def actor_check(request: Request, payload: dict):
            actor = require_actor(request, "propose", payload.get("project_id"))
            return {"actor_id": actor.actor_id, "can_release": actor.allows(PROJECT_ID, "release")}

        response = client.post("/api/actor-check", headers=self.headers("designer"), json={"actor_id": "issuer", "project_id": PROJECT_ID})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"actor_id": "designer", "can_release": False})
        self.assertEqual(client.post("/api/actor-check", headers=self.headers("designer"), json={"project_id": "other-project"}).status_code, 403)

    def test_accept_grant_never_authorizes_release(self) -> None:
        client = self.client(shared=False)

        @client.app.get("/api/release-check")
        def release_check(request: Request):
            require_actor(request, "release")
            return {"ok": True}

        self.assertEqual(client.get("/api/release-check", headers=self.headers("reviewer")).status_code, 403)

    def test_shared_service_starts_without_constructing_any_agent(self) -> None:
        with patch("archflow_studio_api.main.compiler_from_settings", side_effect=AssertionError("agent started")):
            client = self.client(intent_provider="codex", codex_executable="does-not-exist", cad_export="occt")
        self.assertIsNone(client.app.state.intent_compiler)
        with self.assertRaises(StudioError):
            client.app.state.jobs.submit(candidate_id="forbidden", proposal_id="forbidden",
                                         work=lambda: self.fail("shared service ran candidate"))
        self.assertEqual(client.get("/api/health").status_code, 200)
        self.assertEqual(client.get("/api/protocol").status_code, 200)

    def test_shared_service_does_not_advertise_or_run_compute_routes(self) -> None:
        client = self.client()
        schema_paths = client.get("/openapi.json").json()["paths"]
        for path in ("/api/intents", "/api/drawings/elevations", "/api/proposals", "/api/program", "/api/candidates/combine"):
            with self.subTest(path=path):
                self.assertNotIn("post", schema_paths.get(path, {}))
                response = client.post(path, headers=self.headers("designer"), json={})
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["code"], "SERVICE_ROLE_FORBIDDEN")
        # Even an accidentally mounted old endpoint stays behind the process-role gate.
        @client.app.post("/api/compute-accident")
        def accidental_compute():
            self.fail("shared service reached compute")
        self.assertEqual(client.post("/api/compute-accident", headers=self.headers("designer")).status_code, 403)

    def test_secret_values_are_not_in_reprs_or_refusals(self) -> None:
        settings = self.settings(api_token="legacy-secret")
        credentials = read_actor_credentials(self.actors_file, settings.project_dir)
        for secret in ("legacy-secret", "reader-secret", "designer-secret"):
            self.assertNotIn(secret, repr(settings))
            self.assertNotIn(secret, repr(credentials))
        self.actors_file.write_text('{"actors": [{"token": "never-echo-this-secret"', encoding="utf-8")
        with self.assertRaises(SettingsError) as failure:
            create_app(settings)
        self.assertNotIn("never-echo-this-secret", str(failure.exception))

    def test_legacy_local_and_remote_keep_their_behavior(self) -> None:
        for mode, token in (("local", None), ("remote", "legacy-secret")):
            settings = StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="off", mode=mode,
                                      api_token=token, origins=("https://studio.example",))
            app = create_app(settings)
            self.addCleanup(app.state.jobs.shutdown)
            with TestClient(app) as client:
                headers = {} if token is None else {"Authorization": f"Bearer {token}"}
                self.assertEqual(client.get("/api/project", headers=headers).status_code, 200)


class CollaborationSettingsTests(unittest.TestCase):
    def test_explicit_environment_supplies_actor_and_upstream_configuration(self) -> None:
        with patch.dict(os.environ, {
            "ARCHFLOW_STUDIO_PROJECT_DIR": "project",
            "ARCHFLOW_STUDIO_MODE": "remote",
            "ARCHFLOW_STUDIO_ORIGINS": "https://studio.example",
            "ARCHFLOW_STUDIO_ACTORS_FILE": "config/actors.json",
        }, clear=True):
            actor_settings = StudioSettings.from_env()
        self.assertEqual(actor_settings.actors_file, Path("config/actors.json"))
        with patch.dict(os.environ, {
            "ARCHFLOW_STUDIO_PROJECT_DIR": "project",
            "ARCHFLOW_STUDIO_SYNC_URL": "https://shared.example",
            "ARCHFLOW_STUDIO_SYNC_TOKEN": "upstream-secret",
            "ARCHFLOW_STUDIO_SYNC_PROJECT_ID": "project",
        }, clear=True):
            settings = StudioSettings.from_env()
        self.assertEqual(settings.service_role, "runtime")
        self.assertEqual(settings.mode, "local")
        self.assertIsNone(settings.actors_file)
        self.assertEqual(settings.sync_url, "https://shared.example")
        self.assertEqual(settings.sync_project_id, "project")
        self.assertNotIn("upstream-secret", repr(settings))

    def test_shared_service_needs_explicit_actors(self) -> None:
        with self.assertRaises(SettingsError):
            StudioSettings(project_dir=Path("p"), service_role="shared_project")
        with self.assertRaises(SettingsError):
            StudioSettings(project_dir=Path("p"), mode="remote", service_role="shared_project", api_token="secret", origins=("https://studio.example",))

    def test_sync_is_complete_runtime_only_and_does_not_expose_secrets(self) -> None:
        complete = dict(sync_url="https://shared.example", sync_token="upstream-secret", sync_project_id="p")
        settings = StudioSettings(project_dir=Path("p"), **complete)
        self.assertNotIn("upstream-secret", repr(settings))
        for partial in ({"sync_url": complete["sync_url"]}, {"sync_token": "upstream-secret"}, {"sync_project_id": "p"}):
            with self.assertRaises(SettingsError):
                StudioSettings(project_dir=Path("p"), **partial)
        with self.assertRaises(SettingsError):
            StudioSettings(project_dir=Path("p"), mode="remote", origins=("https://studio.example",), actors_file=Path("actors.json"), service_role="shared_project", **complete)
        for remote in ({"api_token": "local-listener-secret"}, {"actors_file": Path("actors.json")}):
            with self.assertRaises(SettingsError):
                StudioSettings(project_dir=Path("p"), mode="remote", origins=("https://studio.example",), **remote, **complete)

    def test_sync_rejects_relative_or_credential_bearing_urls(self) -> None:
        for url in ("/api", "file:///shared", "https://user:secret@shared.example", "https://shared.example?token=secret", "http://shared.example:bad"):
            with self.subTest(url=url), self.assertRaises(SettingsError) as failure:
                StudioSettings(project_dir=Path("p"), sync_url=url, sync_token="upstream-secret", sync_project_id="p")
            self.assertNotIn("upstream-secret", str(failure.exception))

    def test_actor_configuration_cannot_be_project_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            with self.assertRaises(SettingsError):
                read_actor_credentials(project / "actors.json", project)


if __name__ == "__main__":
    unittest.main()
