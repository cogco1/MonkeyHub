"""The project root is chosen by the operator, never defaulted in code."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from project_runtime.main import create_app, main
from project_runtime.settings import (
    CAD_EXPORT_ENV,
    CAD_EXPORT_OCCT,
    CAD_EXPORT_OFF,
    CAD_EXPORT_RHINO,
    LOCAL_MODE,
    REMOTE_MODE,
    RHINO_EXPORT_ENV,
    SettingsError,
    StudioSettings,
    cad_export_from_env,
)

from .support import make_empty_project


class SettingsTests(unittest.TestCase):
    def test_local_mode_accepts_only_loopback_listeners(self) -> None:
        for host in ("127.0.0.1", "127.0.0.2", "::1", "localhost"):
            with self.subTest(host=host):
                self.assertEqual(
                    StudioSettings(project_dir=Path("p"), bind_host=host).bind_host,
                    host,
                )
        for host in ("0.0.0.0", "::", "192.168.1.2", "studio.example"):
            with self.subTest(host=host), self.assertRaisesRegex(SettingsError, "loopback"):
                StudioSettings(project_dir=Path("p"), bind_host=host)

    def test_cli_validates_the_effective_listener_before_starting(self) -> None:
        with patch.dict(os.environ, {
            "ARCHFLOW_STUDIO_PROJECT_DIR": "unused-project",
            "ARCHFLOW_STUDIO_BIND": "127.0.0.1",
        }, clear=True), patch("project_runtime.main.uvicorn.run") as serve:
            with self.assertRaisesRegex(SettingsError, "loopback"):
                main(["--host", "0.0.0.0"])
            serve.assert_not_called()

        with patch.dict(os.environ, {
            "ARCHFLOW_STUDIO_PROJECT_DIR": "unused-project",
            "ARCHFLOW_STUDIO_BIND": "0.0.0.0",
        }, clear=True), patch("project_runtime.main.create_app") as create, patch(
            "project_runtime.main.uvicorn.run"
        ) as serve:
            main(["--host", "127.0.0.1"])
            self.assertEqual(create.call_args.args[0].bind_host, "127.0.0.1")
            self.assertEqual(serve.call_args.kwargs["host"], "127.0.0.1")

    def test_cli_project_dir_binds_the_explicit_project_over_the_environment(self) -> None:
        with patch.dict(os.environ, {
            "ARCHFLOW_STUDIO_PROJECT_DIR": "inherited-project",
        }, clear=True), patch("project_runtime.main.create_app") as create, patch(
            "project_runtime.main.uvicorn.run"
        ) as serve:
            main(["--project-dir", "explicit-project", "--port", "18001"])
            self.assertEqual(create.call_args.args[0].project_dir, Path("explicit-project"))
            self.assertEqual(serve.call_args.kwargs["port"], 18001)

    def test_missing_project_dir_refuses_and_names_the_variable(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ARCHFLOW_STUDIO_PROJECT_DIR", None)
            with self.assertRaises(SettingsError) as raised:
                StudioSettings.from_env()
        self.assertIn("ARCHFLOW_STUDIO_PROJECT_DIR", str(raised.exception))

    def test_project_dir_comes_from_the_environment(self) -> None:
        with patch.dict(
            os.environ, {"ARCHFLOW_STUDIO_PROJECT_DIR": "some/project"}, clear=False
        ):
            self.assertEqual(StudioSettings.from_env().project_dir, Path("some/project"))

    def test_the_codex_executable_resolves_through_path(self) -> None:
        with patch.dict(os.environ, {"ARCHFLOW_STUDIO_PROJECT_DIR": "some/project"}, clear=False):
            os.environ.pop("ARCHFLOW_STUDIO_CODEX", None)
            with patch("project_runtime.settings.shutil.which", return_value=r"C:\npm\codex.CMD") as which:
                self.assertEqual(StudioSettings.from_env().codex_executable, r"C:\npm\codex.CMD")
            which.assert_called_once_with("codex")
            with patch("project_runtime.settings.shutil.which", return_value=None):
                self.assertEqual(StudioSettings.from_env().codex_executable, "codex")
        absolute = str(Path(tempfile.gettempdir()) / "tools" / "codex.exe")
        with patch.dict(
            os.environ,
            {"ARCHFLOW_STUDIO_PROJECT_DIR": "some/project", "ARCHFLOW_STUDIO_CODEX": absolute},
            clear=False,
        ):
            with patch("project_runtime.settings.shutil.which") as which:
                self.assertEqual(StudioSettings.from_env().codex_executable, absolute)
            which.assert_not_called()

    def test_the_reference_run_is_configured_and_otherwise_unset(self) -> None:
        with patch.dict(
            os.environ, {"ARCHFLOW_STUDIO_PROJECT_DIR": "some/project"}, clear=False
        ):
            os.environ.pop("ARCHFLOW_STUDIO_REFERENCE_RUN", None)
            self.assertIsNone(StudioSettings.from_env().reference_run)
        with patch.dict(
            os.environ,
            {
                "ARCHFLOW_STUDIO_PROJECT_DIR": "some/project",
                "ARCHFLOW_STUDIO_REFERENCE_RUN": " run-002 ",
            },
            clear=False,
        ):
            self.assertEqual(StudioSettings.from_env().reference_run, "run-002")

    def test_the_mode_bind_and_token_are_read_from_the_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"ARCHFLOW_STUDIO_PROJECT_DIR": "some/project"},
            clear=False,
        ):
            for name in (
                "ARCHFLOW_STUDIO_MODE",
                "ARCHFLOW_STUDIO_BIND",
                "ARCHFLOW_STUDIO_TOKEN",
                "ARCHFLOW_STUDIO_ORIGINS",
            ):
                os.environ.pop(name, None)
            settings = StudioSettings.from_env()
        self.assertEqual(settings.mode, LOCAL_MODE)
        self.assertEqual(settings.bind_host, "127.0.0.1")
        self.assertIsNone(settings.api_token)
        self.assertEqual(settings.origins, ())
        with patch.dict(
            os.environ,
            {
                "ARCHFLOW_STUDIO_PROJECT_DIR": "some/project",
                "ARCHFLOW_STUDIO_MODE": " remote ",
                "ARCHFLOW_STUDIO_BIND": "0.0.0.0",
                "ARCHFLOW_STUDIO_TOKEN": " s3cret ",
                "ARCHFLOW_STUDIO_ORIGINS": "https://a.example, https://b.example ,",
            },
            clear=False,
        ):
            settings = StudioSettings.from_env()
        self.assertEqual(settings.mode, REMOTE_MODE)
        self.assertEqual(settings.bind_host, "0.0.0.0")
        self.assertEqual(settings.api_token, "s3cret")
        self.assertEqual(
            settings.origins, ("https://a.example", "https://b.example")
        )

    # ---- the one CAD export setting

    def test_a_process_nothing_configured_exports_through_occt(self) -> None:
        self.assertEqual(cad_export_from_env({}), CAD_EXPORT_OCCT)
        self.assertEqual(StudioSettings(project_dir=Path("p")).cad_export, CAD_EXPORT_OCCT)
        settings = StudioSettings(project_dir=Path("p"))
        self.assertTrue(settings.exports)
        self.assertFalse(settings.rhino_lane)

    def test_the_legacy_boolean_only_enables_or_disables_and_never_selects_rhino(self) -> None:
        # An explicit ``0`` written by an older launcher stays effective: no export.
        self.assertEqual(cad_export_from_env({RHINO_EXPORT_ENV: "0"}), CAD_EXPORT_OFF)
        # ``1`` meant "export"; export now means the ordinary OCCT path, not Rhino.
        self.assertEqual(cad_export_from_env({RHINO_EXPORT_ENV: "1"}), CAD_EXPORT_OCCT)
        self.assertEqual(cad_export_from_env({RHINO_EXPORT_ENV: ""}), CAD_EXPORT_OCCT)
        self.assertEqual(cad_export_from_env({RHINO_EXPORT_ENV: "yes"}), CAD_EXPORT_OFF)

    def test_the_new_variable_wins_over_the_legacy_one(self) -> None:
        for value, expected in (("rhino", CAD_EXPORT_RHINO), (" OCCT ", CAD_EXPORT_OCCT), ("off", CAD_EXPORT_OFF)):
            with self.subTest(value=value):
                self.assertEqual(
                    cad_export_from_env({CAD_EXPORT_ENV: value, RHINO_EXPORT_ENV: "0"}), expected
                )

    def test_an_unknown_cad_export_is_refused_by_name(self) -> None:
        with self.assertRaises(SettingsError) as raised:
            cad_export_from_env({CAD_EXPORT_ENV: "sideways"})
        self.assertIn(CAD_EXPORT_ENV, str(raised.exception))
        self.assertIn("sideways", str(raised.exception))
        with self.assertRaises(SettingsError):
            StudioSettings(project_dir=Path("p"), cad_export="sideways")

    def test_from_env_reads_the_cad_export_through_the_same_rule(self) -> None:
        with patch.dict(
            os.environ,
            {"ARCHFLOW_STUDIO_PROJECT_DIR": "some/project", RHINO_EXPORT_ENV: "0"},
            clear=False,
        ):
            os.environ.pop(CAD_EXPORT_ENV, None)
            self.assertEqual(StudioSettings.from_env().cad_export, CAD_EXPORT_OFF)
        with patch.dict(
            os.environ,
            {"ARCHFLOW_STUDIO_PROJECT_DIR": "some/project", CAD_EXPORT_ENV: "rhino", RHINO_EXPORT_ENV: "0"},
            clear=False,
        ):
            settings = StudioSettings.from_env()
        self.assertEqual(settings.cad_export, CAD_EXPORT_RHINO)
        self.assertTrue(settings.rhino_lane)

    def test_remote_mode_from_the_environment_refuses_without_a_token(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "ARCHFLOW_STUDIO_PROJECT_DIR": "some/project",
                "ARCHFLOW_STUDIO_MODE": "remote",
            },
            clear=False,
        ):
            os.environ.pop("ARCHFLOW_STUDIO_TOKEN", None)
            with self.assertRaises(SettingsError) as raised:
                StudioSettings.from_env()
        self.assertIn("ARCHFLOW_STUDIO_TOKEN", str(raised.exception))


class UserSettingsRetiredTests(unittest.TestCase):
    """The account's preferences are MonkeyHub's (#486): the runtime neither serves nor advertises them."""

    def test_the_runtime_has_no_user_settings_route_or_capability(self) -> None:
        client = TestClient(create_app(StudioSettings(project_dir=Path("unbound-placeholder"))))
        self.addCleanup(client.close)
        self.assertNotIn("user-settings", client.get("/api/protocol").json()["capabilities"])
        self.assertNotIn("/api/settings/user", client.app.openapi()["paths"])
        self.assertEqual(client.get("/api/settings/user").status_code, 404)


if __name__ == "__main__":
    unittest.main()
