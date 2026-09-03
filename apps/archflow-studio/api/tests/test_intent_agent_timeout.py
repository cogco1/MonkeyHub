"""A codex that does not answer is killed with its children.

``codex`` on this machine is a shim — ``codex.cmd`` → ``cmd.exe`` → ``node``
— and a timeout that kills only the shim leaves a grandchild holding the
pipes, so the compile would sit past the timeout it promised. The executable
here is a real shim of the same shape: a script that starts a Python
grandchild which writes to stdout and then sleeps far longer than the
timeout. The compiler must answer 502 within the timeout and leave no
grandchild behind.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.intent_agent import (
    AGENT_FAILED,
    CodexCompiler,
    Selection,
)
from archflow_studio_api.application.projection import project_state
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.errors import StudioError

from .support import PROJECT_ID, make_project

TIMEOUT_S = 1.0
GRANDCHILD_SLEEP_S = 20.0


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return str(pid) in listing
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _write_shim(directory: Path) -> tuple[Path, Path]:
    """A shim of codex's own shape: a script whose child holds stdout and sleeps."""

    pid_file = directory / "grandchild.pid"
    grandchild = directory / "hang.py"
    grandchild.write_text(
        "import os, sys, time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "sys.stdout.write('started')\n"
        "sys.stdout.flush()\n"
        f"time.sleep({GRANDCHILD_SLEEP_S})\n",
        encoding="utf-8",
    )
    if sys.platform == "win32":
        shim = directory / "codex.cmd"
        shim.write_text(
            "@echo off\r\n"
            f'"{sys.executable}" "{grandchild}"\r\n',
            encoding="utf-8",
        )
    else:
        shim = directory / "codex"
        shim.write_text(
            "#!/bin/sh\n"
            f'"{sys.executable}" "{grandchild}"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)
    return shim, pid_file


class CodexTimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        make_project(self.root)
        app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        self.projection = project_state(bound_project(app.state))
        self.shim, self.pid_file = _write_shim(self.root)

    def _compile(self) -> tuple[StudioError, float]:
        compiler = CodexCompiler(executable=str(self.shim), timeout_s=TIMEOUT_S)
        started = time.perf_counter()
        with self.assertRaises(StudioError) as caught:
            compiler.compile(
                message="make the west portico a little taller",
                selection=Selection(component_id="portico", element_id=None),
                projection=self.projection,
            )
        return caught.exception, time.perf_counter() - started

    def test_a_hung_agent_is_refused_within_its_timeout(self) -> None:
        error, elapsed = self._compile()
        self.assertEqual(error.status, 502)
        self.assertEqual(error.code, AGENT_FAILED)
        self.assertIn(f"did not answer within {TIMEOUT_S:g} s", error.detail)
        self.assertLess(
            elapsed,
            TIMEOUT_S + 5.0,
            f"the compile took {elapsed:.1f} s: the timeout did not end the call",
        )

    def test_the_agents_children_are_gone_after_the_timeout(self) -> None:
        _, elapsed = self._compile()
        # Only a prompt return makes the check below mean anything: a call
        # that waited for the grandchild to finish would find it gone too.
        self.assertLess(elapsed, TIMEOUT_S + 5.0)
        self.assertTrue(self.pid_file.is_file(), "the grandchild never started")
        pid = int(self.pid_file.read_text(encoding="utf-8"))
        deadline = time.perf_counter() + 2.0
        while _alive(pid) and time.perf_counter() < deadline:
            time.sleep(0.1)
        self.assertFalse(_alive(pid), f"grandchild {pid} outlived the timeout")
