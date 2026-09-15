from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

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
        for args in (
            ("--workspace-root", str(self.source / "build")),
            ("--workspace-root", str(self.workspace), "--staging-dir", str(self.source / "build")),
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
            "apps/monkeyfab/src/monkeyfab", "apps/monkeyfab/tests",
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
            "SECURITY.md",
            "apps/monkeyfab/src/monkeyfab/__main__.py", "apps/monkeyfab/pyproject.toml",
            "apps/monkeyfab/tests/test_cli.py",
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
        # A user holding only the ZIP can still find the security-reporting route.
        self.assertEqual((self.bundle / "SECURITY.md").read_text(), "fixture")
        self.assertIn("SECURITY.md", builder.SOURCE_PATHS)
        self.assertEqual((self.bundle / "apps/monkeyfab/src/monkeyfab/__main__.py").read_text(), "fixture")
        self.assertEqual((self.bundle / "apps/monkeyfab/pyproject.toml").read_text(), "fixture")
        self.assertFalse((self.bundle / "apps/monkeyfab/tests").exists())
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
    def test_inventory_identifies_shipped_dependencies_and_frontend_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, bundle = root / "source", root / "bundle"
            for name in ("monkeyhub", "archflow-studio"):
                lock = source / f"apps/{name}/web/package-lock.json"
                lock.parent.mkdir(parents=True)
                lock.write_bytes(b'{"lockfileVersion": 3}')
                asset = bundle / f"apps/{name}/web/dist/index.html"
                asset.parent.mkdir(parents=True)
                asset.write_bytes(name.encode())
            lock = source / "apps/monkeyhub/package-lock.json"
            lock.write_bytes(b'{"lockfileVersion": 3}')
            adapter = bundle / "apps/monkeyhub/node_modules/@agentclientprotocol/codex-acp/package.json"
            adapter.parent.mkdir(parents=True)
            adapter.write_text('{"name":"@agentclientprotocol/codex-acp","version":"1.11.0"}', encoding="utf-8")
            requirements = bundle / "_runtime/requirements-lock.txt"
            requirements.parent.mkdir()
            requirements.write_bytes(b"cadquery-ocp==7.8.1.1.post1\n")
            with patch.object(builder, "run", return_value="v24.14.0"):
                inventory = builder.runtime_inventory(source, bundle)
            self.assertEqual(inventory["nodeVersion"], "v24.14.0")
            self.assertEqual(inventory["acpAdapter"]["version"], "1.11.0")
            self.assertEqual(inventory["pythonRequirements"]["sha256"], builder.sha256(requirements))
            for name in ("monkeyhub", "archflow-studio"):
                asset = f"apps/{name}/web/dist/index.html"
                self.assertEqual(inventory["frontends"][name]["files"], {asset: builder.sha256(bundle / asset)})

    @unittest.skipUnless(sys.platform == "win32", "Windows installer and shortcut behavior")
    def test_desktop_install_preserves_data_and_keeps_only_latest_app_shortcut(self):
        with tempfile.TemporaryDirectory(prefix="Hub install space ") as temporary:
            root = Path(temporary)
            local, shortcuts = root / "local", root / "shortcuts"
            shortcuts.mkdir()
            environment = dict(os.environ, LOCALAPPDATA=str(local))
            retained = {}
            for relative in ("project/HEAD", "local/MonkeyHub/config/applications.json", "local/MonkeyHub/chats/retained.json"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"existing user data\n")
                retained[path] = path.read_bytes()
            for commit, desktop in (("a" * 40, False), ("a" * 40, True), ("b" * 40, True)):
                bundle = root / (("desktop" if desktop else "browser") + commit[:1])
                required = ["OPEN_MONKEYHUB.cmd", "_runtime/python/python.exe", "apps/monkeyhub/run.py",
                            "apps/monkeyhub/launch-hub.ps1", "apps/monkeyhub/web/dist/index.html",
                            "apps/archflow-studio/web/dist/index.html",
                            "apps/monkeyfab/src/monkeyfab/__main__.py", "apps/monkeyfab/pyproject.toml"]
                if desktop:
                    required.extend(("MonkeyArch.exe", "_runtime/desktop-Cargo.lock"))
                for relative in required:
                    path = bundle / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"fixture - never executed")
                (bundle / "source-version.txt").write_text(commit, encoding="utf-8")
                (bundle / "build-info.json").write_text(json.dumps({"sourceCommit": commit,
                    **({"desktop": {"sourceCommit": commit}} if desktop else {})}), encoding="utf-8")
                script = bundle / "apps/monkeyhub/installer/install.ps1"
                script.parent.mkdir(parents=True)
                shutil.copy2(builder.SOURCE_ROOT / "apps/monkeyhub/installer/install.ps1", script)
                command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                           "-CreateDesktopShortcut", "-DesktopDirectory", str(shortcuts)]
                for _ in range(2):  # Reinstall the same build without changing its files.
                    result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                name = commit[:12] + ("-desktop" if desktop else "")
                self.assertTrue((local / "MonkeyHub/versions" / name / required[-1]).is_file())
                for path, before in retained.items():
                    self.assertEqual(path.read_bytes(), before, path)
                installed_entry = local / "MonkeyHub/versions" / name / required[-1]
                retained[installed_entry] = installed_entry.read_bytes()
                if desktop:
                    (bundle / "MonkeyArch.exe").unlink()
                    missing = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
                    self.assertNotEqual(missing.returncode, 0)
                    self.assertIn("MonkeyArch.exe", missing.stdout + missing.stderr)
            self.assertTrue((local / "MonkeyHub/versions" / ("a" * 12) / "OPEN_MONKEYHUB.cmd").is_file())
            installed = local / "MonkeyHub/versions" / (commit[:12] + "-desktop")
            browser_entry = installed / "OPEN_MONKEYHUB.cmd"
            desktop_entry = installed / "MonkeyArch.exe"
            self.assertTrue(browser_entry.is_file())
            self.assertTrue(desktop_entry.is_file())
            self.assertFalse((shortcuts / "MonkeyHub.lnk").exists())
            self.assertTrue((shortcuts / "MonkeyArch.lnk").exists())
            inspect = root / "inspect.ps1"
            inspect.write_text("param($Directory)\n$shell = New-Object -ComObject WScript.Shell\n"
                               "@('MonkeyArch.lnk') | ForEach-Object { "
                               "$link = $shell.CreateShortcut((Join-Path $Directory $_)); "
                               "[PSCustomObject]@{Target=$link.TargetPath; WindowStyle=$link.WindowStyle} } "
                               "| ConvertTo-Json -Compress\n", encoding="utf-8")
            result = subprocess.run(["powershell.exe", "-NoProfile", "-File", str(inspect), str(shortcuts)],
                                    capture_output=True, text=True, timeout=15, check=True)
            link = json.loads(result.stdout)
            self.assertTrue(Path(link["Target"]).samefile(desktop_entry), link)
            self.assertEqual(link["WindowStyle"], 1)

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


class ReleaseEvidenceTests(unittest.TestCase):
    """Slice A of GH-58: derived manifest, shipped SBOM and mutation rejection."""

    BUILD_INFO = {
        "sourceCommit": "c" * 40, "target": "windows-x64", "channel": "candidate",
        "pythonVersion": "3.13.15",
        "pythonUrl": "https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip",
        "pythonSha256": "d" * 64,
        "runtimeInventory": {
            "nodeVersion": "v24.14.0",
            "pythonRequirements": {"path": "_runtime/requirements-lock.txt", "sha256": "e" * 64},
            "acpAdapter": {"name": "@agentclientprotocol/codex-acp", "version": "1.11.0",
                           "packageLockSha256": "f" * 64},
            "frontends": {}, "externalDependencies": [],
        },
    }

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Hub 发行证据 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source, self.bundle = self.root / "source", self.root / "bundle"
        self.output = self.root / "output"
        self.output.mkdir()
        node = self.bundle / "_runtime/node/node.exe"
        node.parent.mkdir(parents=True)
        node.write_bytes(b"selected Node runtime")
        site = self.bundle / builder.PYTHON_SITE
        for name, version, metadata in (
            ("rhino3dm", "8.32.1", "License-Expression: MIT\n"),
            ("cadquery_ocp", "7.9.3.1.1", "License: LGPL-2.1-only\n"),
        ):
            info = site / f"{name}-{version}.dist-info"
            info.mkdir(parents=True)
            (info / "METADATA").write_text(
                f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n{metadata}", encoding="utf-8")
        for locked, tree, packages in (
            ("apps/monkeyhub", self.bundle / "apps/monkeyhub",
             (("@agentclientprotocol/codex-acp", "1.11.0", "Apache-2.0"),)),
            ("apps/monkeyhub/web", self.source / "apps/monkeyhub/web", (("react", "19.2.0", "MIT"),)),
            ("apps/archflow-studio/web", self.source / "apps/archflow-studio/web",
             (("react", "19.2.0", "MIT"), ("three", "0.181.0", "MIT"))),
        ):
            entries = {}
            for name, version, license_id in packages:
                relative = f"node_modules/{name}"
                entries[relative] = {"version": version}
                package = tree / relative
                package.mkdir(parents=True)
                (package / "package.json").write_text(json.dumps(
                    {"name": name, "version": version, "license": license_id}), encoding="utf-8")
            lock = self.source / locked / "package-lock.json"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(json.dumps({"packages": {"": {"name": locked}, **entries}}), encoding="utf-8")

    def sbom(self, build_info: dict | None = None) -> dict:
        return builder.sbom_document(
            self.source, self.bundle, build_info or dict(self.BUILD_INFO), "cccccccccccc")

    PREFIX = "MonkeyHub-cccccccccccc-windows-x64"

    def released(self, build_info: dict | None = None, *,
                 archived_sbom: str | None = None, archived_info: str | None = None) -> Path:
        """Write one finished release directory the way package() does.

        The archive really carries build-info.json and the SBOM, so the digest
        bindings can be exercised; the overrides let a test ship a different
        copy inside the ZIP than the manifest binds.
        """
        build_info = build_info or dict(self.BUILD_INFO)
        info = self.bundle / "build-info.json"
        info.write_text(json.dumps(build_info), encoding="utf-8")
        sbom_text = json.dumps(self.sbom(build_info))
        (self.bundle / builder.SBOM_NAME).write_text(sbom_text, encoding="utf-8")
        sbom = self.output / f"{self.PREFIX}.cyclonedx.json"
        sbom.write_text(sbom_text, encoding="utf-8")
        archive = self.output / f"{self.PREFIX}-candidate.zip"
        with zipfile.ZipFile(archive, "w") as opened:
            opened.writestr(f"{self.PREFIX}/build-info.json",
                            archived_info if archived_info is not None else info.read_text(encoding="utf-8"))
            opened.writestr(f"{self.PREFIX}/{builder.SBOM_NAME}",
                            archived_sbom if archived_sbom is not None else sbom_text)
            opened.writestr(f"{self.PREFIX}/OPEN_MONKEYHUB.cmd", "fixture")
        checksum = self.output / f"{archive.name}.sha256"
        checksum.write_text(f"{builder.sha256(archive)}  {archive.name}\n", encoding="utf-8")
        manifest = self.output / f"{archive.name}.release-manifest.json"
        manifest.write_text(json.dumps(builder.release_manifest(
            build_info, "cccccccccccc", self.PREFIX, info, archive,
            (archive, checksum, sbom), sbom)), encoding="utf-8")
        return manifest

    def test_sbom_inventories_the_python_node_and_rust_contents_actually_shipped(self) -> None:
        build_info = dict(self.BUILD_INFO, desktop={
            "version": "0.1.0", "sourceCommit": "c" * 40, "cargoVersion": "cargo fixture",
            "cargoLockSha256": "a" * 64, "executableSha256": "b" * 64})
        lock = self.bundle / "_runtime/desktop-Cargo.lock"
        lock.write_text('version = 4\n\n[[package]]\nname = "monkeyarch-desktop"\nversion = "0.1.0"\n\n'
                        '[[package]]\nname = "tauri"\nversion = "2.11.5"\n'
                        f'source = "{builder.CRATES_IO}"\nchecksum = "{"9" * 64}"\n', encoding="utf-8")
        document = self.sbom(build_info)
        self.assertEqual(document["bomFormat"], "CycloneDX")
        self.assertEqual(document["specVersion"], "1.6")
        components = {component["bom-ref"]: component for component in document["components"]}
        # Python: read from the shipped site-packages, not from a kept list.
        self.assertIn("pkg:pypi/rhino3dm@8.32.1", components)
        self.assertEqual(components["pkg:pypi/rhino3dm@8.32.1"]["licenses"], [{"expression": "MIT"}])
        self.assertIn("pkg:pypi/cadquery-ocp@7.9.3.1.1", components)  # purl normalises _ to -
        self.assertEqual(components["pkg:pypi/cadquery-ocp@7.9.3.1.1"]["licenses"],
                         [{"license": {"name": "LGPL-2.1-only"}}])
        # Node: the adapter ships as files; a frontend package is a build input,
        # because the bundler decides what actually reaches dist.
        adapter = components["pkg:npm/%40agentclientprotocol/codex-acp@1.11.0"]
        self.assertEqual(adapter["properties"], [{"name": builder.SHIPPED_IN,
                                                  "value": "apps/monkeyhub/node_modules"}])
        self.assertEqual(components["pkg:npm/react@19.2.0"]["properties"], [
            {"name": builder.BUILD_INPUT,
             "value": "apps/archflow-studio/web/package-lock.json -> apps/archflow-studio/web/dist"},
            {"name": builder.BUILD_INPUT,
             "value": "apps/monkeyhub/web/package-lock.json -> apps/monkeyhub/web/dist"},
        ])
        # Rust: crates.io entries only, as build inputs; one lock covers every
        # target and build script, so compiled-in presence is not established.
        self.assertIn("pkg:cargo/tauri@2.11.5", components)
        self.assertEqual(components["pkg:cargo/tauri@2.11.5"]["hashes"],
                         [{"alg": "SHA-256", "content": "9" * 64}])
        self.assertEqual(components["pkg:cargo/tauri@2.11.5"]["properties"], [
            {"name": builder.BUILD_INPUT, "value": "_runtime/desktop-Cargo.lock -> MonkeyArch.exe"}])
        self.assertNotIn("pkg:cargo/monkeyarch-desktop@0.1.0", components)
        # Only the executable itself is claimed as shipped.
        self.assertEqual(components["monkeyhub:MonkeyArch.exe"]["properties"],
                         [{"name": builder.SHIPPED_IN, "value": "MonkeyArch.exe"}])
        self.assertEqual(components["monkeyhub:MonkeyArch.exe"]["hashes"],
                         [{"alg": "SHA-256", "content": "b" * 64}])
        meanings = {row["name"] for row in document["metadata"]["properties"]}
        self.assertIn(f"{builder.BUILD_INPUT}:meaning", meanings)
        # Runtimes: versions come from build-info, the shipped file supplies its hash.
        self.assertEqual(components[builder.purl("generic", "node", "v24.14.0")]["hashes"],
                         [{"alg": "SHA-256", "content": builder.sha256(self.bundle / "_runtime/node/node.exe")}])
        graph = document["dependencies"][0]
        self.assertEqual(graph["ref"], "monkeyhub")
        self.assertEqual(sorted(graph["dependsOn"]), sorted(components))
        self.assertEqual(document["metadata"]["component"]["version"], "cccccccccccc")
        # The document adds no variation of its own, so one inventory yields one
        # set of bytes. That is not a claim about two builds of one commit.
        self.assertNotIn("serialNumber", document)
        self.assertNotIn("timestamp", document["metadata"])
        self.assertEqual(json.dumps(self.sbom(build_info)), json.dumps(document))

    def test_pypi_purls_follow_the_official_purl_normalisation(self) -> None:
        # purl-spec tests/types/pypi-test.json canonicalises this exact input.
        self.assertEqual(builder.purl("pypi", "Django_package".lower().replace("_", "-"), "1.11.1.dev1"),
                         "pkg:pypi/django-package@1.11.1.dev1")
        # A dot in a package name is preserved; only distribution filenames
        # replace it, and this purl names the package.
        self.assertEqual(builder.purl("pypi", "zope.interface", "7.2"), "pkg:pypi/zope.interface@7.2")

    def test_sbom_reports_a_production_dependency_missing_from_the_bundle(self) -> None:
        shutil.rmtree(self.bundle / "apps/monkeyhub/node_modules/@agentclientprotocol/codex-acp")
        with self.assertRaisesRegex(ValueError, "Production dependency is not installed"):
            self.sbom()

    def test_manifest_derives_every_release_fact_from_build_info(self) -> None:
        changed = dict(self.BUILD_INFO, pythonVersion="3.13.99", channel="beta")
        changed["runtimeInventory"] = dict(self.BUILD_INFO["runtimeInventory"], nodeVersion="v26.0.0")
        manifest = json.loads(self.released(changed).read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "ReleaseManifest@1")
        self.assertEqual(manifest["release"]["sourceCommit"], "c" * 40)
        self.assertEqual(manifest["release"]["channel"], "beta")
        self.assertEqual(manifest["build"]["pythonVersion"], "3.13.99")
        self.assertEqual(manifest["build"]["nodeVersion"], "v26.0.0")
        self.assertEqual(manifest["build"]["acpAdapter"], self.BUILD_INFO["runtimeInventory"]["acpAdapter"])
        # The manifest binds the build metadata it views instead of restating it,
        # and names where that copy travels so verification can go and read it.
        info = self.bundle / "build-info.json"
        self.assertEqual(manifest["buildInfo"]["sha256"], builder.sha256(info))
        self.assertEqual(manifest["buildInfo"]["pathInArchive"], f"{self.PREFIX}/build-info.json")
        self.assertEqual(manifest["sbom"]["pathInArchive"], f"{self.PREFIX}/{builder.SBOM_NAME}")
        self.assertEqual(manifest["archive"], f"{self.PREFIX}-candidate.zip")

    def test_unsigned_candidate_is_marked_rather_than_trusted_by_its_checksum(self) -> None:
        manifest = json.loads(self.released().read_text(encoding="utf-8"))
        self.assertEqual(manifest["trust"]["status"], "candidate-unsigned")
        self.assertIs(manifest["trust"]["signed"], False)
        self.assertIsNone(manifest["trust"]["signature"])
        self.assertIn("do not establish origin", manifest["trust"]["statement"].lower())
        self.assertEqual(manifest["sbom"]["format"], "CycloneDX")
        self.assertEqual(manifest["sbom"]["specVersion"], "1.6")

    def test_verification_rejects_mutated_missing_and_unlisted_release_files(self) -> None:
        manifest = self.released()
        self.assertEqual(builder.verify_release(manifest), [])
        archive = self.output / f"{self.PREFIX}-candidate.zip"
        original = archive.read_bytes()
        archive.write_bytes(original.replace(b"fixture", b"tampere"))  # same length
        problems = builder.verify_release(manifest)
        self.assertTrue(any(archive.name in problem and "SHA-256" in problem for problem in problems), problems)
        archive.write_bytes(original + b"extra")
        self.assertTrue(any("bytes on disk" in problem for problem in builder.verify_release(manifest)))
        archive.write_bytes(original)
        self.assertEqual(builder.verify_release(manifest), [])
        sbom = self.output / f"{self.PREFIX}.cyclonedx.json"
        text = sbom.read_text(encoding="utf-8")
        sbom.unlink()
        self.assertTrue(any("not present" in problem for problem in builder.verify_release(manifest)))
        sbom.write_text(text, encoding="utf-8")
        # A file smuggled into this release's own set is not silently accepted.
        (self.output / f"{self.PREFIX}-setup.exe").write_bytes(b"unlisted installer")
        problems = builder.verify_release(manifest)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("absent from its closed manifest", problems[0])

    def test_verification_checks_the_build_info_copy_inside_the_archive(self) -> None:
        """The manifest claims to bind build-info.json, so it must read it."""
        manifest = self.released(archived_info=json.dumps(
            dict(self.BUILD_INFO, sourceCommit="0" * 40)))
        problems = builder.verify_release(manifest)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(f"{self.PREFIX}/build-info.json", problems[0])
        self.assertIn("manifest binds build-info.json to", problems[0])
        # Removing it from the archive is reported, not passed over in silence.
        archive = self.output / f"{self.PREFIX}-candidate.zip"
        clean = self.released()
        with zipfile.ZipFile(archive, "w") as opened:
            opened.writestr(f"{self.PREFIX}/{builder.SBOM_NAME}",
                            (self.output / f"{self.PREFIX}.cyclonedx.json").read_text(encoding="utf-8"))
        problems = builder.verify_release(clean)
        self.assertTrue(any("does not contain" in problem and "build-info.json" in problem
                            for problem in problems), problems)

    def test_verification_checks_the_bundled_sbom_against_the_sidecar_digest(self) -> None:
        manifest = self.released(archived_sbom='{"bomFormat":"CycloneDX","specVersion":"1.6"}')
        problems = builder.verify_release(manifest)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(f"{self.PREFIX}/{builder.SBOM_NAME}", problems[0])
        self.assertIn("manifest binds SBOM to", problems[0])

    def test_verification_rejects_a_manifest_whose_sbom_digest_contradicts_its_table(self) -> None:
        manifest = self.released()
        document = json.loads(manifest.read_text(encoding="utf-8"))
        document["sbom"]["sha256"] = "0" * 64
        manifest.write_text(json.dumps(document), encoding="utf-8")
        problems = builder.verify_release(manifest)
        self.assertTrue(any("the artifact table records" in problem for problem in problems), problems)

    def test_verification_rejects_a_corrupt_archive_without_crashing(self) -> None:
        manifest = self.released()
        (self.output / f"{self.PREFIX}-candidate.zip").write_bytes(b"not a zip at all")
        problems = builder.verify_release(manifest)
        self.assertTrue(any("SHA-256" in problem for problem in problems), problems)
        # The listed digest already failed; opening it must still not raise.
        document = json.loads(manifest.read_text(encoding="utf-8"))
        for entry in document["artifacts"]:
            if entry["path"].endswith(".zip"):
                entry["size"] = len(b"not a zip at all")
                entry["sha256"] = builder.sha256(self.output / f"{self.PREFIX}-candidate.zip")
        manifest.write_text(json.dumps(document), encoding="utf-8")
        self.assertTrue(any("unreadable archive" in problem
                            for problem in builder.verify_release(manifest)))

    def test_build_info_binding_cannot_be_redirected_to_the_sbom(self) -> None:
        manifest = self.released()
        document = json.loads(manifest.read_text(encoding="utf-8"))
        document["buildInfo"] = dict(document["sbom"])
        manifest.write_text(json.dumps(document), encoding="utf-8")
        self.assertTrue(any("build-info.json: pathInArchive must name" in problem
                            for problem in builder.verify_release(manifest)))

    def test_verification_reports_a_malformed_manifest_instead_of_failing(self) -> None:
        manifest = self.output / f"{self.PREFIX}-broken.release-manifest.json"
        good = json.loads(self.released().read_text(encoding="utf-8"))
        for document, expected in (
            ("{ not json", "unreadable"),
            (json.dumps({"schema": "Something@2"}), "not a ReleaseManifest@1"),
            (json.dumps({"schema": "ReleaseManifest@1", "artifactPrefix": self.PREFIX}),
             "lists no distributed artifact"),
            (json.dumps({**good, "artifactPrefix": "../escape"}), "artifactPrefix must not contain"),
            (json.dumps({**good, "artifactPrefix": None}), "artifactPrefix must be a non-empty name"),
            (json.dumps({**good, "artifacts": ["../secrets"]}), "must be an object"),
            # A separator is rejected by character, so a Windows escape is caught
            # on Linux and a POSIX one on Windows.
            (json.dumps({**good, "artifacts": [{"path": f"{self.PREFIX}\\..\\escape"}]}),
             "must not contain a path separator"),
            (json.dumps({**good, "artifacts": [{"path": f"{self.PREFIX}/../escape"}]}),
             "must not contain a path separator"),
            (json.dumps({**good, "artifacts": [{"path": ".."}]}), "must not be a relative directory"),
            (json.dumps({**good, "artifacts": [{"path": "elsewhere.zip"}]}),
             "does not carry the release prefix"),
            (json.dumps({**good, "artifacts": good["artifacts"] + [good["artifacts"][0]]}),
             "listed twice"),
            (json.dumps({**good, "archive": f"{self.PREFIX}-absent.zip"}),
             "distributed archive is not one of the listed artifacts"),
            (json.dumps({**good, "sbom": {**good["sbom"], "pathInArchive": "../outside"}}),
             "pathInArchive must be a path inside the archive"),
            (json.dumps({**good, "buildInfo": {**good["buildInfo"], "pathInArchive": "other/build-info.json"}}),
             f"is not inside {self.PREFIX}/"),
        ):
            with self.subTest(expected=expected):
                manifest.write_text(document, encoding="utf-8")
                problems = builder.verify_release(manifest)
                self.assertTrue(any(expected in problem for problem in problems), problems)
        manifest.unlink()

    def test_verify_command_reports_failure_without_building(self) -> None:
        manifest = self.released()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(builder.main(["--verify", str(manifest)]), 0)
        self.assertIn("PASS", output.getvalue())
        self.assertIn("unsigned", output.getvalue())
        (self.output / "MonkeyHub-cccccccccccc-windows-x64-candidate.zip").write_bytes(b"replaced")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(builder.main(["--verify", str(manifest)]), 1)
        self.assertIn("FAIL", output.getvalue())

    def test_generated_sbom_validates_against_the_official_cyclonedx_schema(self) -> None:
        """Optional proof against the published schema.

        Run with CYCLONEDX_SCHEMA pointing at the official bom-1.6.schema.json
        from https://github.com/CycloneDX/specification/tree/master/schema.
        """
        schema_path = os.environ.get("CYCLONEDX_SCHEMA")
        if not schema_path or not Path(schema_path).is_file():
            self.skipTest("set CYCLONEDX_SCHEMA to the official bom-1.6.schema.json")
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is not installed")
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        schema.pop("$schema", None)  # spdx.schema.json is fetched over the network otherwise
        jsonschema.Draft7Validator(schema).validate(self.sbom())


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/desktop.yml"
NORMALIZE_STEP = "Normalize release version across package evidence"


def workflow_step(name: str) -> tuple[str, dict[str, str]]:
    """The ``run`` body and declared ``env`` of one desktop.yml step.

    Read from the workflow rather than copied, because the release
    normalisation exists only there: a second copy here could pass while the
    step that actually runs on release-candidate is broken. Only the shape this
    file already uses is understood -- two-space YAML, a literal ``run`` block
    and plain ``KEY: value`` env entries -- and anything else raises instead of
    quietly yielding an empty script.
    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    starts = [index for index, line in enumerate(lines) if line.strip() == f"- name: {name}"]
    if len(starts) != 1:
        raise AssertionError(f"expected exactly one {name!r} step, found {len(starts)}")
    start = starts[0]
    indent = len(lines[start]) - len(lines[start].lstrip())
    body = lines[start + 1:]
    for offset, line in enumerate(body):
        if line.strip().startswith("- ") and len(line) - len(line.lstrip()) == indent:
            body = body[:offset]
            break

    def block(key: str) -> list[str]:
        for offset, line in enumerate(body):
            if line.strip() == key:
                inner = len(line) - len(line.lstrip())
                held = []
                for following in body[offset + 1:]:
                    if following.strip() and len(following) - len(following.lstrip()) <= inner:
                        break
                    held.append(following)
                return held
        return []

    run = block("run: |")
    if not run:
        raise AssertionError(f"{name!r} has no literal run block")
    margin = min(len(line) - len(line.lstrip()) for line in run if line.strip())
    environment = {}
    for line in block("env:"):
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        key, _, value = entry.partition(":")
        environment[key.strip()] = value.strip()
    return "\n".join(line[margin:] for line in run), environment


def inline_python(run: str) -> str:
    """The single-quoted PowerShell here-string the step pipes to a .py file."""
    lines = run.splitlines()
    opens = [index for index, line in enumerate(lines) if line == "@'"]
    closes = [index for index, line in enumerate(lines) if line.startswith("'@")]
    if len(opens) != 1 or len(closes) != 1:
        raise AssertionError("expected exactly one here-string in the step")
    return "\n".join(lines[opens[0] + 1:closes[0]])


class ReleaseCandidateNormalizationTests(unittest.TestCase):
    """The release-candidate normalisation in desktop.yml, without a real build.

    The step promotes one assembled bundle to a named version and rewrites the
    evidence around it. It runs only on the release-candidate branch, so a
    defect there is invisible to every other build; these tests execute the
    script the workflow actually ships against a synthetic bundle.
    """

    VERSION = "0.1.77"
    SOURCE_SHA = "c" * 40
    STALE = "MonkeyHub-abcdef123456-windows-x64-candidate.zip"

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Hub 候选发行 ")
        self.addCleanup(temporary.cleanup)
        self.runner_temp = Path(temporary.name).resolve()
        self.output = self.runner_temp / "mhp/output"
        self.output.mkdir(parents=True)
        # A previous unversioned build output the step is expected to replace.
        (self.output / self.STALE).write_bytes(b"superseded")
        self.build = self.runner_temp / "mhp/staging/task"
        self.source = self.build / "source"
        self.bundle = self.build / "MonkeyHub-abcdef123456-windows-x64"
        node = self.bundle / "_runtime/node/node.exe"
        node.parent.mkdir(parents=True)
        node.write_bytes(b"selected Node runtime")
        info = self.bundle / builder.PYTHON_SITE / "rhino3dm-8.32.1.dist-info"
        info.mkdir(parents=True)
        (info / "METADATA").write_text(
            "Metadata-Version: 2.4\nName: rhino3dm\nVersion: 8.32.1\nLicense-Expression: MIT\n",
            encoding="utf-8")
        (self.bundle / "_runtime/desktop-Cargo.lock").write_text(
            'version = 4\n\n[[package]]\nname = "monkeyarch-desktop"\nversion = '
            f'"{self.VERSION}"\n\n[[package]]\nname = "tauri"\nversion = "2.11.5"\n'
            f'source = "{builder.CRATES_IO}"\nchecksum = "{"9" * 64}"\n', encoding="utf-8")
        for locked, tree in (("apps/monkeyhub", self.bundle / "apps/monkeyhub"),
                             ("apps/monkeyhub/web", self.source / "apps/monkeyhub/web"),
                             ("apps/archflow-studio/web", self.source / "apps/archflow-studio/web")):
            package = tree / "node_modules/react"
            package.mkdir(parents=True)
            (package / "package.json").write_text(
                json.dumps({"name": "react", "version": "19.2.0", "license": "MIT"}), encoding="utf-8")
            lock = self.source / locked / "package-lock.json"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(json.dumps({"packages": {
                "": {"name": locked}, "node_modules/react": {"version": "19.2.0"}}}), encoding="utf-8")
        (self.bundle / "OPEN_MONKEYHUB.cmd").write_text("fixture", encoding="utf-8")
        self.write_build_info()

    def write_build_info(self, **overrides: object) -> None:
        desktop = {"version": self.VERSION, "sourceCommit": self.SOURCE_SHA,
                   "cargoVersion": "cargo fixture", "cargoLockSha256": "a" * 64,
                   "executableSha256": "b" * 64}
        document = dict(ReleaseEvidenceTests.BUILD_INFO, sourceCommit=self.SOURCE_SHA, desktop=desktop)
        document.update(overrides)
        (self.bundle / "build-info.json").write_text(
            json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")

    def normalize(self) -> subprocess.CompletedProcess:
        """Run the workflow's script the way the release-candidate build does.

        The script is written into RUNNER_TEMP, not the checkout, so it is only
        importable through what the step declares; PYTHONPATH is dropped first
        so an ambient value cannot stand in for the workflow's own environment.
        """
        run, declared = workflow_step(NORMALIZE_STEP)
        script = self.runner_temp / "mhp/normalize-release.py"
        script.write_text(inline_python(run), encoding="utf-8", newline="\n")
        repository = str(WORKFLOW.parents[2])
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        for key, value in declared.items():
            environment[key] = value.replace("${{ github.workspace }}", repository)
        environment.update({
            "RUNNER_TEMP": str(self.runner_temp),
            "GITHUB_WORKSPACE": repository,
            "PACKAGE_BUILD": str(self.build),
            "MONKEYHUB_RELEASE_VERSION": self.VERSION,
            "MONKEYHUB_SOURCE_SHA": self.SOURCE_SHA,
            "PYTHONUTF8": "1",
        })
        return subprocess.run([sys.executable, str(script)], cwd=repository, env=environment,
                              capture_output=True, text=True, encoding="utf-8")

    def test_promotion_binds_one_version_across_bundle_zip_sbom_manifest_and_build_info(self) -> None:
        completed = self.normalize()
        self.assertEqual(completed.returncode, 0, f"{completed.stdout}\n{completed.stderr}")
        prefix = f"MonkeyHub-{self.VERSION}-windows-x64"
        self.assertTrue((self.build / prefix).is_dir(), "the bundle directory carries the version")

        archive = self.output / f"{prefix}-candidate.zip"
        manifest = self.output / f"{archive.name}.release-manifest.json"
        sbom = self.output / f"{prefix}.cyclonedx.json"
        checksum = self.output / f"{archive.name}.sha256"
        self.assertEqual({path.name for path in self.output.iterdir()},
                         {archive.name, manifest.name, sbom.name, checksum.name})
        self.assertFalse((self.output / self.STALE).exists(), "the unversioned output is replaced")

        document = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(document["release"]["version"], self.VERSION)
        self.assertEqual(document["release"]["sourceCommit"], self.SOURCE_SHA)
        self.assertEqual(document["artifactPrefix"], prefix)
        self.assertEqual(json.loads(sbom.read_text(encoding="utf-8"))["metadata"]["component"]["version"],
                         self.VERSION)
        self.assertEqual(checksum.read_text(encoding="utf-8"),
                         f"{builder.sha256(archive)}  {archive.name}\n")

        with zipfile.ZipFile(archive) as opened:
            names = opened.namelist()
            shipped = json.loads(opened.read(f"{prefix}/build-info.json"))
            bundled_sbom = json.loads(opened.read(f"{prefix}/{builder.SBOM_NAME}"))
        self.assertTrue(all(name.startswith(f"{prefix}/") for name in names), names)
        self.assertEqual(shipped["releaseVersion"], self.VERSION)
        self.assertEqual(shipped["desktop"]["version"], self.VERSION)
        self.assertEqual(bundled_sbom["metadata"]["component"]["version"], self.VERSION)
        self.assertEqual(builder.verify_release(manifest), [])

    def test_a_desktop_built_at_another_version_is_refused_before_any_output_changes(self) -> None:
        self.write_build_info(desktop={"version": "0.1.1", "sourceCommit": self.SOURCE_SHA,
                                       "cargoVersion": "cargo fixture", "cargoLockSha256": "a" * 64,
                                       "executableSha256": "b" * 64})
        completed = self.normalize()
        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertIn("desktop version does not match", completed.stderr)
        self.assertEqual({path.name for path in self.output.iterdir()}, {self.STALE},
                         "a refused promotion leaves the previous output untouched")

    def test_a_bundle_built_from_another_commit_is_refused(self) -> None:
        self.write_build_info(sourceCommit="d" * 40)
        completed = self.normalize()
        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertIn("sourceCommit does not match", completed.stderr)
        self.assertEqual({path.name for path in self.output.iterdir()}, {self.STALE})


if __name__ == "__main__":
    unittest.main()
