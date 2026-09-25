"""The Hub is the only writer of a Studio child's environment.

The standalone launcher used to forward runtime.json and the saved preferences
into ARCHFLOW_STUDIO_* variables, and had tests for it. That launcher is gone
(#126); this is the same contract, owned by the Hub: project, CAD backend and
reference run from application settings, intent provider/model/timeout from the
user's saved preferences, and nothing inherited from the shell that started the
Hub. Nor does any child the Hub starts inherit the Hub's own stdin (#58).
"""

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
for directory in (ROOT, ROOT / "apps/archflow-studio/api", ROOT / "apps/monkeyhub/api"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from archflow_studio_api.transport.settings import ApplicationSettingsDto
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.render_adapters.gemini import adapter_from_settings
from monkeyhub_api.applications import Applications
from monkeyhub_api.models import HubFailure


class StudioChildEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub 环境 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "project.json").write_text("{}", encoding="utf-8")
        self.appdata = self.root / "roaming"
        self.applications = Applications(self.root / "source", self.root / "runtime", hub_port=18790)
        # What a developer's shell may carry: the Hub must replace all of it.
        self.inherited = {
            "APPDATA": str(self.appdata),
            "ARCHFLOW_STUDIO_PROJECT_DIR": "somebody-elses-project",
            "ARCHFLOW_STUDIO_MODE": "remote",
            "ARCHFLOW_STUDIO_BIND": "0.0.0.0",
            "ARCHFLOW_STUDIO_INTENT_PROVIDER": "anthropic",
            "UNRELATED_SETTING": "kept",
        }

    def settings(self, **fields):
        return ApplicationSettingsDto.model_validate({"projectDir": str(self.project), **fields})

    def save_preferences(self, **preferences):
        (self.appdata / "MonkeyArch").mkdir(parents=True, exist_ok=True)
        (self.appdata / "MonkeyArch" / "settings.json").write_text(json.dumps(preferences), encoding="utf-8")

    def test_defaults_come_from_settings_and_nothing_from_the_shell(self):
        with patch.dict(os.environ, self.inherited, clear=True):
            command, environment = self.applications._command("studio", self.settings())
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(Path(command[1]), (self.root / "source").resolve() / "apps/monkeyhub/run.py")
        self.assertEqual(command[2:], ["--service", "studio", "--host", "127.0.0.1"])
        studio = {key: value for key, value in environment.items() if key.startswith("ARCHFLOW_STUDIO_")}
        self.assertEqual(studio, {
            "ARCHFLOW_STUDIO_MODE": "local",
            "ARCHFLOW_STUDIO_PROJECT_DIR": str(self.project),
            "ARCHFLOW_STUDIO_CAD_EXPORT": "occt",
            "ARCHFLOW_STUDIO_INTENT_PROVIDER": "deterministic",
        })
        self.assertEqual(
            environment["MONKEYMONITOR_DATA_DIR"],
            str((self.root / "runtime").resolve() / "diagnostics" / "monkeymonitor"),
        )
        self.assertEqual(environment["UNRELATED_SETTING"], "kept")

    def test_saved_preferences_and_settings_reach_the_child(self):
        self.save_preferences(intentProvider="codex", intentModel="gpt-5", intentTimeoutS=45.5)
        with patch.dict(os.environ, self.inherited, clear=True):
            _, environment = self.applications._command(
                "studio", self.settings(cadExport="off", referenceRun="run-003"),
            )
        self.assertEqual(environment["ARCHFLOW_STUDIO_CAD_EXPORT"], "off")
        self.assertEqual(environment["ARCHFLOW_STUDIO_REFERENCE_RUN"], "run-003")
        self.assertEqual(environment["ARCHFLOW_STUDIO_INTENT_PROVIDER"], "codex")
        self.assertEqual(environment["ARCHFLOW_STUDIO_INTENT_MODEL"], "gpt-5")
        self.assertEqual(float(environment["ARCHFLOW_STUDIO_INTENT_TIMEOUT_S"]), 45.5)

    def test_a_folder_without_a_manifest_is_refused_but_no_web_build_is_required(self):
        (self.project / "project.json").unlink()
        with patch.dict(os.environ, self.inherited, clear=True), self.assertRaises(HubFailure) as refused:
            self.applications._command("studio", self.settings())
        self.assertEqual(refused.exception.error.code, "PROJECT_REQUIRED")
        (self.project / "project.json").write_text("{}", encoding="utf-8")
        with patch.dict(os.environ, self.inherited, clear=True):
            command, _ = self.applications._command("studio", self.settings())
        self.assertNotIn("--web-dir", command)

    def test_render_preferences_reach_factory_only_with_explicit_hub_key(self):
        self.save_preferences(renderProvider="gemini", renderModel="gemini-3.1-flash-image", renderTimeoutS=67)
        synthetic = {**self.inherited, "MONKEYHUB_RENDER_API_KEY": "synthetic-render-key",
                     "ARCHFLOW_STUDIO_RENDER_API_KEY": "discard-this-inherited-value",
                     "ARCHFLOW_STUDIO_RENDER_MODEL": "discard-this-model"}
        with patch.dict(os.environ, synthetic, clear=True):
            _, environment = self.applications._command("studio", self.settings())
        self.assertNotIn("MONKEYHUB_RENDER_API_KEY", environment)
        self.assertEqual(environment["ARCHFLOW_STUDIO_RENDER_API_KEY"], "synthetic-render-key")
        with patch.dict(os.environ, environment, clear=True):
            settings = StudioSettings.from_env()
        adapter = adapter_from_settings(settings)
        self.assertIsNotNone(adapter)
        self.assertTrue(adapter.capability().available)
        self.assertEqual(settings.render_model, "gemini-3.1-flash-image")
        self.assertEqual(settings.render_timeout_s, 67)
        self.assertNotIn("synthetic-render-key", repr(settings))

    def test_render_off_drops_key_and_missing_key_stays_unavailable(self):
        self.save_preferences(renderProvider="off")
        with patch.dict(os.environ, {**self.inherited, "MONKEYHUB_RENDER_API_KEY": "synthetic-only"}, clear=True):
            _, environment = self.applications._command("studio", self.settings())
        self.assertFalse(any("RENDER" in key for key in environment))
        self.save_preferences(renderProvider="gemini")
        with patch.dict(os.environ, self.inherited, clear=True):
            _, environment = self.applications._command("studio", self.settings())
        with patch.dict(os.environ, environment, clear=True):
            adapter = adapter_from_settings(StudioSettings.from_env())
        self.assertIsNotNone(adapter)
        self.assertFalse(adapter.capability().available)


HUB_PACKAGE = ROOT / "apps/monkeyhub/api/monkeyhub_api"

# Everything that starts a process, by the module that provides it.
PROCESS_STARTERS = {
    "subprocess": {"Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"},
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
    "asyncio.subprocess": {"create_subprocess_exec", "create_subprocess_shell"},
    "os": {"system", "popen", "posix_spawn", "posix_spawnp",
           *(f"exec{suffix}" for suffix in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")),
           *(f"spawn{suffix}" for suffix in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe"))},
}

# "<file>:<function>" of a start that may leave the Hub's own stdin to its
# child, and why that child can never wait on the pipe the desktop holds.
# Empty: every process the Hub starts names a stdin of its own.
INHERITS_HUB_STDIN: dict[str, str] = {}


def process_starts(source: str) -> list[tuple[str, int, bool]]:
    """Each place ``source`` starts a process: (function, line, whether the child's stdin is named).

    A starter handed on to another call, as in ``asyncio.to_thread(subprocess.run, ...)``,
    is judged by that call's keywords. One kept as a plain value can say nothing
    about the stdin it will be given, so it never counts as naming one; neither
    do ``stdin=None`` and a ``**`` mapping. Annotations start nothing.
    """
    tree = ast.parse(source)
    modules: dict[str, str] = {}
    imported: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.partition(".")[0]
                modules[alias.asname or top] = alias.name if alias.asname else top
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                imported[alias.asname or alias.name] = (node.module, alias.name)

    def starter(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            module, name = imported.get(node.id, ("", ""))
        elif isinstance(node, ast.Attribute):
            path, value = [], node.value
            while isinstance(value, ast.Attribute):
                path.insert(0, value.attr)
                value = value.value
            if not isinstance(value, ast.Name) or not (value.id in modules or value.id in imported):
                return False
            base = modules[value.id] if value.id in modules else ".".join(imported[value.id])
            module, name = ".".join((base, *path)), node.attr
        else:
            return False
        return name in PROCESS_STARTERS.get(module, ())

    def names_stdin(call: ast.Call) -> bool:
        return any(keyword.arg == "stdin" and not (isinstance(keyword.value, ast.Constant) and keyword.value.value is None)
                   for keyword in call.keywords)

    starts = []

    def visit(node: ast.AST, scope: str, parent: ast.AST | None) -> None:
        if starter(node):
            call = parent if isinstance(parent, ast.Call) and (parent.func is node or node in parent.args) else None
            starts.append((scope or "<module>", node.lineno, call is not None and names_stdin(call)))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = f"{scope}.{node.name}" if scope else node.name
        for field, value in ast.iter_fields(node):
            if field in {"annotation", "returns"}:
                continue
            for child in value if isinstance(value, list) else [value]:
                if isinstance(child, ast.AST):
                    visit(child, scope, node)

    visit(tree, "", None)
    return starts


class HubChildStdinTests(unittest.TestCase):
    """No child of the Hub waits on the Hub's own stdin (#58).

    The desktop starts the Hub with --managed-stdin, and one of its threads
    always waits to read that pipe. On Windows a child that inherits the pipe
    does not start until that read returns: the update preflight timed out
    after 180 s on it, and a checkout's ``git rev-parse`` gives up after its
    5 s. So every process the Hub package starts names its child's stdin:
    DEVNULL, a pipe the Hub owns, or an entry in INHERITS_HUB_STDIN saying why.
    """

    def test_the_scan_sees_every_way_a_process_is_started(self):
        starts = process_starts(textwrap.dedent("""
            import asyncio, os, subprocess as sp
            from subprocess import Popen as Spawn

            def named(handle: sp.Popen) -> sp.Popen:
                sp.run(["git"], stdin=sp.DEVNULL)
                Spawn(["worker"], stdin=sp.PIPE)
                asyncio.to_thread(sp.run, ["taskkill"], stdin=sp.DEVNULL)
                return asyncio.create_subprocess_exec("adapter", stdin=asyncio.subprocess.PIPE)

            def inherited(options):
                sp.run(["git"], capture_output=True)
                sp.check_output(["git"], stdin=None)
                sp.run(["git"], **options)
                asyncio.to_thread(sp.run, ["taskkill"], stdout=sp.DEVNULL)
                os.system("git")

            runner = sp.run
        """))
        self.assertEqual([(scope, named) for scope, _, named in starts],
                         [("named", True)] * 4 + [("inherited", False)] * 5 + [("<module>", False)])

    def test_every_process_the_hub_starts_names_its_stdin(self):
        starts: dict[str, list[tuple[int, bool]]] = {}
        for path in sorted(HUB_PACKAGE.rglob("*.py")):
            for scope, line, named in process_starts(path.read_text(encoding="utf-8")):
                starts.setdefault(f"{path.relative_to(HUB_PACKAGE).as_posix()}:{scope}", []).append((line, named))
        # The scan reads the real package: these start processes today.
        self.assertLessEqual({"applications.py:source_revision", "workers.py:WorkerSupervisor.start",
                              "acp_session.py:CodexAcpSession._start", "chat.py:_stop_process"}, set(starts))
        unnamed = [f"{site} (line {line})" for site, rows in starts.items() if site not in INHERITS_HUB_STDIN
                   for line, named in rows if not named]
        self.assertEqual(unnamed, [], "pass stdin=subprocess.DEVNULL or a pipe the Hub owns, "
                                      "or say in INHERITS_HUB_STDIN why this child may read the Hub's own stdin")
        self.assertEqual(sorted(set(INHERITS_HUB_STDIN) - set(starts)), [], "a justification for a start that is gone")

    @unittest.skipUnless(os.name == "nt", "a pending synchronous pipe read holds up the children that inherit it on Windows")
    def test_the_checkout_revision_is_read_while_the_hub_stdin_is_waited_on(self):
        if (ROOT / "source-version.txt").exists() or not (ROOT / ".git").exists():
            self.skipTest("needs a git checkout without a packaged source marker")
        expected = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, check=True).stdout.strip().lower()
        temporary = tempfile.TemporaryDirectory(prefix="hub-stdin-")
        self.addCleanup(temporary.cleanup)
        harness = Path(temporary.name) / "managed_hub.py"
        harness.write_text("\n".join((
            "import sys, threading, time",
            "from pathlib import Path",
            f"sys.path[:0] = {[str(ROOT), str(ROOT / 'apps/archflow-studio/api'), str(ROOT / 'apps/monkeyhub/api')]!r}",
            "from monkeyhub_api.applications import source_revision",
            "# What --managed-stdin does: one thread always waits on the Hub's stdin.",
            "threading.Thread(target=sys.stdin.read, daemon=True).start()",
            "time.sleep(0.5)",
            f"print(source_revision(Path({str(ROOT)!r})))",
        )) + "\n", encoding="utf-8")
        with subprocess.Popen([sys.executable, str(harness)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, creationflags=subprocess.CREATE_NO_WINDOW) as hub:
            guard = threading.Timer(60, hub.kill)
            guard.start()
            try:
                output = hub.stdout.read()
            finally:
                guard.cancel()
        self.assertEqual(output.strip(), expected, "git waited on the Hub's stdin and the Hub lost its source version")


if __name__ == "__main__":
    unittest.main()
