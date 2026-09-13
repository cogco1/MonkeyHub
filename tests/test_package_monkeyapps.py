from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import package_monkeyapps as builder


class PackageWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "source"
        self.workspace = self.root / "运行 space"
        self.git_config = self.root / "gitconfig"
        self.environment = patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": str(self.git_config), "GIT_CONFIG_NOSYSTEM": "1",
            "PYTHONUTF8": "1",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        subprocess.run(["git", "init", "-q", "-b", "codex/hub-acp", str(self.source)], check=True)
        self.git_config.write_text("[user]\n\tname = Existing Developer\n", encoding="utf-8")
        self.node = self.root / "node.exe"
        self.npm = self.root / "npm-cli.js"
        self.node.touch()
        self.npm.touch()

    def call(self, *args: str) -> dict[str, object]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(builder.main(["--source-root", str(self.source), *args]), 0)
        return json.loads(output.getvalue()) if output.getvalue().strip() else {}

    def configure(self) -> dict[str, object]:
        return self.call("--configure", "--workspace-root", str(self.workspace))

    def build_args(self) -> list[str]:
        return ["--node", str(self.node), "--npm-cli", str(self.npm)]

    def test_configure_then_new_process_reuses_paths_and_preserves_existing_data(self) -> None:
        project = self.workspace / "workspace/projects/existing"
        project.mkdir(parents=True)
        marker = project / "project.json"
        marker.write_text('{"project_id":"existing"}', encoding="utf-8")
        before = {path.relative_to(self.workspace): path.read_bytes()
                  for path in self.workspace.rglob("*") if path.is_file()}
        initial = self.configure()
        result = subprocess.run([
            sys.executable, "-X", "utf8", str(Path(builder.__file__).resolve()),
            "--source-root", str(self.source), "--show-paths",
        ], cwd=self.root, capture_output=True, text=True, encoding="utf-8", check=True)
        self.assertEqual(json.loads(result.stdout), initial)
        self.assertEqual(initial["sourceRoot"], str(self.source))
        self.assertEqual(initial["task"], "codex-hub-acp")
        self.assertEqual(initial["stagingDir"], str(self.workspace / "temp/package-monkeyapps/codex-hub-acp"))
        self.assertEqual(initial["outputDir"], str(self.workspace / "packages/codex-hub-acp"))
        self.assertEqual(initial["cacheDir"], str(self.workspace / "cache/package-monkeyapps"))
        self.assertEqual(before, {path.relative_to(self.workspace): path.read_bytes()
                                for path in self.workspace.rglob("*") if path.is_file()})
        self.assertFalse((self.workspace / "temp").exists())
        self.assertFalse((self.workspace / "packages").exists())
        self.assertIn("Existing Developer", self.git_config.read_text(encoding="utf-8"))

    def test_builds_use_stable_task_paths_and_share_cache_across_tasks(self) -> None:
        self.configure()
        with patch.object(builder, "package") as package:
            self.call(*self.build_args())
            self.call(*self.build_args())
            self.call("--task", "another-task", *self.build_args())
        first, repeated, other = [call.args for call in package.call_args_list]
        self.assertEqual(first, repeated)
        self.assertEqual(first[:2], (self.source, "HEAD"))
        self.assertEqual(first[2:5], (
            self.workspace / "temp/package-monkeyapps/codex-hub-acp",
            self.workspace / "packages/codex-hub-acp",
            self.workspace / "cache/package-monkeyapps",
        ))
        self.assertNotEqual(first[2:4], other[2:4])
        self.assertEqual(first[4], other[4])

    def test_explicit_paths_override_saved_root_without_changing_it(self) -> None:
        self.configure()
        before = self.git_config.read_bytes()
        overrides = [self.root / name for name in ("staging", "output", "cache")]
        with patch.object(builder, "package") as package:
            self.call("--staging-dir", str(overrides[0]), "--output-dir", str(overrides[1]),
                      "--cache-dir", str(overrides[2]), *self.build_args())
        self.assertEqual(package.call_args.args[2:5], tuple(overrides))
        self.assertEqual(self.git_config.read_bytes(), before)

    def test_legacy_explicit_paths_keep_the_staging_cache_default(self) -> None:
        staging, output = self.root / "staging", self.root / "output"
        with patch.object(builder, "package") as package:
            self.call("--staging-dir", str(staging), "--output-dir", str(output), *self.build_args())
        self.assertEqual(package.call_args.args[2:5], (staging, output, staging / "cache"))
        self.assertNotIn("workspace-root", self.git_config.read_text(encoding="utf-8"))

    def test_bad_roots_and_task_paths_fail_before_configuration_changes(self) -> None:
        before = self.git_config.read_bytes()
        monkeyfab = self.root / "monkeyfab"
        for args in (
            ("--workspace-root", str(self.source / "build")),
            ("--workspace-root", str(self.workspace), "--staging-dir", str(self.source / "build")),
            ("--workspace-root", str(self.workspace), "--cache-dir", str(monkeyfab / "cache"),
             "--monkeyfab-source", str(monkeyfab)),
            ("--workspace-root", str(self.workspace), "--task", "../project"),
        ):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.call("--configure", *args)
            self.assertEqual(self.git_config.read_bytes(), before)
        self.assertFalse(self.workspace.exists())
        self.assertFalse((self.source / "build").exists())

    def test_one_off_workspace_does_not_replace_personal_configuration(self) -> None:
        self.configure()
        before = self.git_config.read_bytes()
        other = self.root / "other-runtime"
        paths = self.call("--workspace-root", str(other), "--show-paths")
        self.assertEqual(paths["workspaceRoot"], str(other))
        self.assertEqual(self.git_config.read_bytes(), before)
        self.assertFalse(other.exists())

    def test_unconfigured_build_explains_the_one_time_setup(self) -> None:
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit):
            self.call(*self.build_args())
        self.assertIn("--configure --workspace-root", error.getvalue())


class PackageAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Hub 包依赖 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.bundle = self.root / "source", self.root / "bundle"
        self.node = self.root / "node.exe"
        self.node.write_bytes(b"selected Node runtime")
        for directory in (
            "archflow", "monkeyarch", "monkeydiagram", "monkeymonitor",
            "apps/archflow-studio/api/archflow_studio_api", "apps/archflow-studio/assets",
            "apps/monkeyhub/api", "apps/monkeyhub/installer/third-party",
        ):
            (self.source / directory).mkdir(parents=True)
        for relative in (
            "apps/monkeyhub/installer/third-party/README.md", "apps/monkeyhub/installer/README.md",
            "apps/monkeyhub/installer/INSTALL_MONKEYHUB.cmd",
            "apps/archflow-studio/launch-studio.ps1", "apps/monkeyhub/run.py",
            "apps/monkeyhub/launch-hub.ps1", "OPEN_MONKEYHUB.cmd", "pyproject.toml",
            "governance/module_registry.json", "apps/shared-web/src/appearance.js",
            "apps/shared-web/src/i18n.js", "apps/shared-web/src/browserTranslator.js",
            "apps/shared-web/src/base.css", "tools/create_project.py", "tools/run_project.py",
        ):
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("fixture", encoding="utf-8")
        for relative in ("apps/archflow-studio/web", "apps/monkeyhub/web"):
            web = self.source / relative
            (web / "dist").mkdir(parents=True)
            (web / "dist/index.html").write_text("fixture", encoding="utf-8")
            (web / "package-lock.json").write_text('{"packages": {}}', encoding="utf-8")
        self.hub = self.source / "apps/monkeyhub"
        self.adapter_relative = "node_modules/@agentclientprotocol/codex-acp"
        for relative, name, version in (
            (self.adapter_relative, "@agentclientprotocol/codex-acp", "1.11.0"),
            ("node_modules/@openai/codex", "@openai/codex", "0.153.4"),
        ):
            package = self.hub / relative
            package.mkdir(parents=True)
            (package / "package.json").write_text(json.dumps({"name": name, "version": version}), encoding="utf-8")
        adapter = self.hub / self.adapter_relative
        (adapter / "dist").mkdir()
        (adapter / "dist/index.js").write_text("console.log('adapter fixture');", encoding="utf-8")
        (adapter / "LICENSE").write_bytes(b"adapter license\n")
        (self.hub / "package-lock.json").write_text(json.dumps({"packages": {
            self.adapter_relative: {"version": "1.11.0"},
            "node_modules/@openai/codex": {"version": "0.153.4"},
            "node_modules/@openai/codex-win32-x64": {"version": "0.153.4", "optional": True},
        }}), encoding="utf-8")

    def test_build_installs_locked_adapter_without_optional_codex_binary(self) -> None:
        with patch.object(builder, "run") as run:
            builder.build_web(self.source, self.node, self.root / "npm-cli.js", {})
        installs = [call for call in run.call_args_list if call.kwargs.get("cwd") == self.hub]
        self.assertEqual(len(installs), 1)
        self.assertIn("ci", installs[0].args[0])
        self.assertIn("--omit=dev", installs[0].args[0])
        self.assertIn("--omit=optional", installs[0].args[0])

    def test_bundle_contains_adapter_node_registry_and_exact_release_notices(self) -> None:
        def upstream(url, **kwargs):
            return io.BytesIO((url + "\n").encode("utf-8"))

        with patch.object(builder, "run", return_value="v24.14.0"), \
                patch.object(builder.urllib.request, "urlopen", side_effect=upstream) as fetched:
            builder.collect_application(self.source, self.bundle, "a" * 40, node=self.node)
        self.assertEqual((self.bundle / "_runtime/node/node.exe").read_bytes(), self.node.read_bytes())
        self.assertEqual((self.bundle / "apps/monkeyhub" / self.adapter_relative / "dist/index.js").read_bytes(),
                         (self.hub / self.adapter_relative / "dist/index.js").read_bytes())
        self.assertEqual((self.bundle / "governance/module_registry.json").read_text(), "fixture")
        self.assertIn("governance/module_registry.json", builder.SOURCE_PATHS)
        urls = {call.args[0] for call in fetched.call_args_list}
        self.assertEqual(urls, {
            "https://raw.githubusercontent.com/nodejs/node/v24.14.0/LICENSE",
            "https://raw.githubusercontent.com/openai/codex/rust-v0.153.4/LICENSE",
            "https://raw.githubusercontent.com/openai/codex/rust-v0.153.4/NOTICE",
        })
        notices = self.bundle / "apps/monkeyhub/installer/third-party"
        index = (notices / "README.md").read_text(encoding="utf-8")
        self.assertIn("monkeyhub ACP: @agentclientprotocol/codex-acp 1.11.0", index)
        self.assertIn("Node.js v24.14.0", index)
        for path in notices.glob("*.txt"):
            self.assertIn(path.name, index)
        self.assertEqual((notices / "web-agentclientprotocol-codex-acp-1.11.0-1-LICENSE.txt").read_bytes(), b"adapter license\n")


class DesktopPackageTests(unittest.TestCase):
    def test_desktop_is_built_from_snapshot_with_bound_revision_and_external_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            source, bundle = build / "source", build / "bundle"
            desktop = source / "apps/monkeyhub/desktop"
            desktop.mkdir(parents=True)
            (desktop / "Cargo.toml").write_text('[package]\nversion="0.1.0"\n', encoding="utf-8")
            (desktop / "Cargo.lock").write_text('version = 4\n', encoding="utf-8")
            (bundle / "_runtime").mkdir(parents=True)
            executable = build / "desktop-target/release/MonkeyArch.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"fixture native executable")
            cargo = build / "cargo.exe"
            with patch.object(builder, "run", side_effect=["", json.dumps({"sourceRevision": "a" * 40, "version": "0.1.0"}), "cargo fixture"]) as run:
                result = builder.build_desktop(source, bundle, "a" * 40, cargo, {"TEMP": str(build / "tmp")})
            command = run.call_args_list[0]
            self.assertEqual(command.args[0], [str(cargo), "build", "--locked", "--release",
                                              "--target-dir", str(build / "desktop-target")])
            self.assertEqual(command.kwargs["cwd"], desktop)
            self.assertEqual(command.kwargs["environment"]["ARCHFLOW_SOURCE_REVISION"], "a" * 40)
            self.assertEqual((bundle / "MonkeyArch.exe").read_bytes(), executable.read_bytes())
            self.assertEqual((bundle / "_runtime/desktop-Cargo.lock").read_bytes(), (desktop / "Cargo.lock").read_bytes())
            self.assertEqual(result["sourceCommit"], "a" * 40)
            self.assertEqual(result["executableSha256"], builder.sha256(executable))

    def test_missing_desktop_compiler_fails_before_staging_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(builder.sys, "platform", "win32"), patch.object(builder, "run", return_value="a" * 40):
                with self.assertRaisesRegex(ValueError, "Rust/MSVC Cargo"):
                    builder.package(root / "source", "HEAD", root / "staging", root / "output", root / "cache",
                                    root / "node.exe", root / "npm-cli.js", desktop=True, cargo=root / "missing.exe")
            self.assertFalse((root / "staging").exists())
            self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
