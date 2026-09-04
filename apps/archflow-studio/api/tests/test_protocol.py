"""The protocol boundary: the handshake, the project list, and the token gate.

These tests are about the seam a second client or a remote server would arrive
through, so they check the two things such a client cannot recover from being
wrong about: what the server says it is, and who it will answer.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app
from archflow_studio_api.protocol import (
    PROTOCOL,
    RHINO_EXPORT_CAPABILITY,
    SERVER_NAME,
    SERVER_VERSION,
)
from archflow_studio_api.settings import (
    LOCAL_MODE,
    REMOTE_MODE,
    SettingsError,
    StudioSettings,
)

from .support import PROJECT_ID, make_project

TOKEN = "a-token-nobody-guesses"
ORIGIN = "https://studio.example"


class ProtocolRouteTests(unittest.TestCase):
    """``GET /api/protocol`` answers without a project and names the server."""

    def setUp(self) -> None:
        self.settings = StudioSettings(project_dir=Path("unbound-placeholder"))
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)

    def test_the_handshake_names_protocol_server_version_and_mode(self) -> None:
        response = self.client.get("/api/protocol")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["protocol"], PROTOCOL)
        self.assertEqual(body["protocol"], "archflow/2")
        self.assertEqual(body["server"], SERVER_NAME)
        self.assertEqual(body["serverVersion"], SERVER_VERSION)
        self.assertEqual(body["mode"], LOCAL_MODE)

    def test_the_handshake_binds_no_project(self) -> None:
        # The project root points nowhere; the handshake still answers, which
        # is what lets a client tell "wrong server" from "wrong project".
        self.assertEqual(self.client.get("/api/protocol").status_code, 200)
        self.assertEqual(self.client.get("/api/project").status_code, 503)

    def test_capabilities_are_the_features_this_process_serves(self) -> None:
        capabilities = self.client.get("/api/protocol").json()["capabilities"]
        self.assertEqual(capabilities, sorted(capabilities))
        for feature in (
            "projection",
            "pick",
            "gestures",
            "intents",
            "proposals",
            "candidates",
            "captures",
            "artifacts",
            "events",
            "validation",
        ):
            self.assertIn(feature, capabilities)
        self.assertNotIn(RHINO_EXPORT_CAPABILITY, capabilities)

    def test_rhino_export_is_a_capability_only_where_it_is_enabled(self) -> None:
        client = TestClient(
            create_app(
                StudioSettings(
                    project_dir=Path("unbound-placeholder"), rhino_export=True
                )
            )
        )
        self.addCleanup(client.close)
        self.assertIn(
            RHINO_EXPORT_CAPABILITY,
            client.get("/api/protocol").json()["capabilities"],
        )

    def test_the_server_version_is_the_applications_own(self) -> None:
        # One constant, so the OpenAPI document and the handshake cannot
        # disagree about which build is running.
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(schema["info"]["version"], SERVER_VERSION)


class ProjectsRouteTests(unittest.TestCase):
    """``/api/projects`` is the general form; ``/api/project`` is its shortcut."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        make_project(self.root)
        self.client = TestClient(
            create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)

    def test_the_listing_is_the_one_bound_project_and_no_path(self) -> None:
        response = self.client.get("/api/projects")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "projects": [
                    {
                        "projectId": PROJECT_ID,
                        "name": PROJECT_ID,
                        "isDefault": True,
                    }
                ]
            },
        )

    def test_the_scoped_path_answers_exactly_what_the_shortcut_answers(
        self,
    ) -> None:
        scoped = self.client.get(f"/api/projects/{PROJECT_ID}")
        shortcut = self.client.get("/api/project")
        self.assertEqual(scoped.status_code, 200)
        self.assertEqual(shortcut.status_code, 200)
        self.assertEqual(scoped.json(), shortcut.json())

    def test_an_unknown_project_is_named_and_refused(self) -> None:
        response = self.client.get("/api/projects/not-this-one")
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertEqual(body["code"], "PROJECT_NOT_FOUND")
        self.assertIn("not-this-one", body["detail"])
        self.assertIn(PROJECT_ID, body["detail"])


class RemoteModeSettingsTests(unittest.TestCase):
    """A remote process that could answer anyone is not a thing that exists."""

    def test_remote_without_a_token_refuses_before_the_app_is_built(self) -> None:
        with self.assertRaises(SettingsError) as raised:
            StudioSettings(project_dir=Path("p"), mode=REMOTE_MODE)
        self.assertIn("ARCHFLOW_STUDIO_TOKEN", str(raised.exception))

    def test_remote_without_origins_refuses(self) -> None:
        with self.assertRaises(SettingsError) as raised:
            StudioSettings(
                project_dir=Path("p"), mode=REMOTE_MODE, api_token=TOKEN
            )
        self.assertIn("ARCHFLOW_STUDIO_ORIGINS", str(raised.exception))

    def test_an_unknown_mode_is_refused_by_name(self) -> None:
        with self.assertRaises(SettingsError) as raised:
            StudioSettings(project_dir=Path("p"), mode="sideways")
        self.assertIn("sideways", str(raised.exception))

    def test_local_mode_needs_nothing_and_binds_loopback(self) -> None:
        settings = StudioSettings(project_dir=Path("p"))
        self.assertEqual(settings.mode, LOCAL_MODE)
        self.assertEqual(settings.bind_host, "127.0.0.1")
        self.assertIsNone(settings.api_token)
        self.assertEqual(settings.origins, ())


class RemoteModeGateTests(unittest.TestCase):
    """In remote mode the token is required; in local mode nothing changed."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        make_project(self.root)
        self.remote = TestClient(
            create_app(
                StudioSettings(
                    project_dir=self.root / PROJECT_ID,
                    mode=REMOTE_MODE,
                    api_token=TOKEN,
                    origins=(ORIGIN,),
                )
            )
        )
        self.addCleanup(self.remote.close)
        self.local = TestClient(
            create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.local.close)

    def test_a_request_without_a_token_is_unauthenticated(self) -> None:
        response = self.remote.get("/api/state")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "UNAUTHENTICATED")
        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")

    def test_a_wrong_token_is_the_same_refusal(self) -> None:
        response = self.remote.get(
            "/api/state", headers={"Authorization": "Bearer not-the-token"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "UNAUTHENTICATED")

    def test_the_right_token_reaches_the_route(self) -> None:
        response = self.remote.get(
            "/api/state", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        self.assertEqual(response.status_code, 200)

    def test_health_and_protocol_answer_without_a_token(self) -> None:
        self.assertEqual(self.remote.get("/api/health").status_code, 200)
        protocol = self.remote.get("/api/protocol")
        self.assertEqual(protocol.status_code, 200)
        self.assertEqual(protocol.json()["mode"], REMOTE_MODE)

    def test_an_unknown_api_path_is_refused_before_it_is_looked_up(self) -> None:
        # 401 rather than 404: an anonymous caller learns nothing about which
        # paths this server has.
        response = self.remote.get("/api/nonsense")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            self.remote.get(
                "/api/nonsense", headers={"Authorization": f"Bearer {TOKEN}"}
            ).status_code,
            404,
        )

    def test_the_allowed_origin_is_answered_and_another_is_not(self) -> None:
        allowed = self.remote.options(
            "/api/state",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertEqual(allowed.headers.get("access-control-allow-origin"), ORIGIN)
        refused = self.remote.options(
            "/api/state",
            headers={
                "Origin": "https://elsewhere.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertIsNone(refused.headers.get("access-control-allow-origin"))

    def test_local_mode_answers_with_no_token_and_adds_no_cors(self) -> None:
        response = self.local.get("/api/state")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("access-control-allow-origin", response.headers)


if __name__ == "__main__":
    unittest.main()
