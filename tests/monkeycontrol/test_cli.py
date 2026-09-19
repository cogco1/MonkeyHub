"""``python -m monkeycontrol`` with the runtime replaced by a stand-in.

The CLI owns the script file, the policy it builds, the ``${TEMP}``
substitution, one summary line per action and the exit code; none of that
needs a desktop, so every case here swaps the runtime factory for a fake.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from monkeycontrol import __main__ as cli
from monkeycontrol.contract import ContractError
from monkeycontrol.runtime import RuntimeRefusal

SCRIPT = {
    "schema": "ComputerActionScript@1",
    "policy": {"allowed_processes": ["notepad"], "mode": "demo"},
    "actions": [
        {
            "intent": "open the editor",
            "application": "notepad",
            "action": {"type": "launch", "command": ["notepad.exe"]},
        },
        {
            "intent": "press save",
            "application": "notepad",
            "target": {"controlType": "Button", "name": "Save"},
            "action": {"type": "click"},
        },
    ],
}


class FakeRuntime:
    """A runtime that answers with prepared receipts and remembers the policy."""

    def __init__(self, trace_dir, policy, receipts=None, error=None) -> None:
        self.trace_dir = trace_dir
        self.policy = policy
        self.payloads: list[dict] = []
        self.recordings: list[str] = []
        self.closed = False
        self._receipts = list(receipts or ())
        self._error = error

    def execute(self, payload, *, mode=None):
        self.payloads.append(payload)
        if self._error is not None:
            raise self._error
        if self._receipts:
            return self._receipts.pop(0)
        return receipt(f"s-{len(self.payloads):04d}")

    def inspect(self, application, window=None, *, depth=6):
        return {"application": application, "windows": [], "nodes": []}

    def record_start(self, name, *, interval_ms=250, region="window-monitor"):
        self.recordings.append((name, region))
        return {
            "name": name,
            "interval_ms": interval_ms,
            "region": region,
            "bounds": [0, 0, 1920, 1080],
        }

    def record_stop(self):
        return {
            "name": self.recordings[-1][0],
            "frame_count": 3,
            "video": {"format": "gif", "path": "raw.gif", "encoder": "pillow"},
        }

    def close(self):
        self.closed = True


def receipt(step_id: str, *, status: str = "succeeded", verification="passed") -> dict:
    return {
        "schema": "ComputerActionReceipt@1",
        "step_id": step_id,
        "intent": "press save",
        "application": "notepad",
        "target": {"requested": {"name": "Save"}, "resolved": {"name": "Save"}},
        "action": {"type": "click"},
        "status": status,
        "refusal": None if status == "succeeded" else {"code": "VERIFY_FAILED"},
        "verification": None
        if verification is None
        else {"expect": "window", "status": verification},
    }


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.trace = self.root / "trace"
        self.made: list[FakeRuntime] = []

    def install(self, **overrides) -> None:
        original = cli.build_runtime

        def factory(trace_dir, policy):
            built = FakeRuntime(trace_dir, policy, **overrides)
            self.made.append(built)
            return built

        cli.build_runtime = factory
        self.addCleanup(setattr, cli, "build_runtime", original)

    def script(self, payload: dict | None = None) -> str:
        path = self.root / "script.json"
        path.write_text(
            json.dumps(payload if payload is not None else SCRIPT), encoding="utf-8"
        )
        return str(path)

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class RunTests(CliTestCase):
    def test_a_script_that_succeeds_prints_one_line_per_action_and_exits_zero(
        self,
    ) -> None:
        self.install()
        code, out, _ = self.run_cli(
            "run", self.script(), "--trace-dir", str(self.trace)
        )
        self.assertEqual(code, 0)
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(lines), 3)
        self.assertIn("s-0001", lines[0])
        self.assertIn("succeeded", lines[0])
        self.assertIn("VERIFY passed", lines[0])
        self.assertIn("2 actions", lines[-1])
        self.assertTrue(self.made[0].closed)

    def test_the_script_policy_reaches_the_runtime_and_allow_extends_it(self) -> None:
        self.install()
        self.run_cli(
            "run",
            self.script(),
            "--trace-dir",
            str(self.trace),
            "--mode",
            "fast",
            "--allow",
            "explorer",
        )
        policy = self.made[0].policy
        self.assertEqual(policy.mode, "fast")
        self.assertEqual(sorted(policy.allowed_processes), ["explorer", "notepad"])
        self.assertEqual(self.made[0].trace_dir, self.trace)

    def test_a_failed_action_exits_one_and_the_tally_says_so(self) -> None:
        self.install(
            receipts=[
                receipt("s-0001"),
                receipt("s-0002", status="failed", verification="failed"),
            ]
        )
        code, out, _ = self.run_cli(
            "run", self.script(), "--trace-dir", str(self.trace)
        )
        self.assertEqual(code, 1)
        self.assertIn("VERIFY failed", out)
        self.assertIn("1 failed", out)

    def test_stop_on_failure_does_not_run_the_rest(self) -> None:
        self.install(
            receipts=[receipt("s-0001", status="refused", verification=None)]
        )
        code, out, _ = self.run_cli(
            "run",
            self.script(),
            "--trace-dir",
            str(self.trace),
            "--stop-on-failure",
        )
        self.assertEqual(code, 1)
        self.assertEqual(len(self.made[0].payloads), 1)
        self.assertIn("refused", out)

    def test_an_invalid_action_exits_two_with_the_message(self) -> None:
        self.install(error=ContractError("action.type must be one of click, wait"))
        code, out, err = self.run_cli(
            "run", self.script(), "--trace-dir", str(self.trace)
        )
        self.assertEqual(code, 2)
        self.assertIn("action.type must be one of", err)

    def test_temp_is_expanded_in_text_command_and_verification_path(self) -> None:
        self.install()
        temp = tempfile.gettempdir()
        self.run_cli(
            "run",
            self.script(
                {
                    "schema": "ComputerActionScript@1",
                    "policy": {"allowed_processes": ["notepad"]},
                    "actions": [
                        {
                            "intent": "name the file",
                            "application": "notepad",
                            "target": {"automationId": "1001"},
                            "action": {
                                "type": "set_value",
                                "text": "${TEMP}\\demo.txt",
                            },
                            "verification": {
                                "expect": "file",
                                "path": "${TEMP}\\demo.txt",
                            },
                        },
                        {
                            "intent": "open the folder",
                            "application": "explorer",
                            "action": {
                                "type": "launch",
                                "command": ["explorer.exe", "${TEMP}"],
                            },
                        },
                    ],
                }
            ),
            "--trace-dir",
            str(self.trace),
        )
        first, second = self.made[0].payloads
        self.assertEqual(first["action"]["text"], f"{temp}\\demo.txt")
        self.assertEqual(first["verification"]["path"], f"{temp}\\demo.txt")
        self.assertEqual(second["action"]["command"], ["explorer.exe", temp])

    def test_a_recording_is_started_and_stopped_around_the_actions(self) -> None:
        self.install()
        code, out, _ = self.run_cli(
            "run",
            self.script(),
            "--trace-dir",
            str(self.trace),
            "--record",
            "demo",
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.made[0].recordings, [("demo", "window-monitor")])
        self.assertIn("raw.gif", out)

    def test_a_runtime_refusal_is_its_own_exit_code(self) -> None:
        self.install()
        original = FakeRuntime.record_start

        def refuse(self, name, *, interval_ms=250, region="window-monitor"):
            raise RuntimeRefusal("RECORDING_ACTIVE", f"{name!r} is already recording")

        FakeRuntime.record_start = refuse
        self.addCleanup(setattr, FakeRuntime, "record_start", original)
        code, _, err = self.run_cli(
            "run",
            self.script(),
            "--trace-dir",
            str(self.trace),
            "--record",
            "demo",
        )
        self.assertEqual(code, 3)
        self.assertIn("RECORDING_ACTIVE", err)
        self.assertTrue(self.made[0].closed)

    def test_the_recording_region_reaches_the_runtime(self) -> None:
        self.install()
        self.run_cli(
            "run",
            self.script(),
            "--trace-dir",
            str(self.trace),
            "--record",
            "demo",
            "--record-region",
            "virtual",
        )
        self.assertEqual(self.made[0].recordings, [("demo", "virtual")])

    def test_a_script_that_is_not_a_script_exits_two(self) -> None:
        self.install()
        code, _, err = self.run_cli(
            "run",
            self.script({"schema": "Nonsense@1", "actions": []}),
            "--trace-dir",
            str(self.trace),
        )
        self.assertEqual(code, 2)
        self.assertIn("ComputerActionScript@1", err)


class InspectTests(CliTestCase):
    def test_inspect_prints_the_tree_as_json(self) -> None:
        self.install()
        code, out, _ = self.run_cli(
            "inspect", "--app", "notepad", "--trace-dir", str(self.trace)
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["application"], "notepad")


class RenderTests(CliTestCase):
    def test_render_delegates_to_the_overlay_module(self) -> None:
        seen = {}

        def fake_render(recording_dir, projection, *, out_dir=None):
            seen["args"] = (Path(recording_dir), projection, out_dir)
            return {"projection": projection, "frames": 2, "out_dir": "somewhere"}

        original = cli.render_overlays
        cli.render_overlays = fake_render
        self.addCleanup(setattr, cli, "render_overlays", original)
        code, out, _ = self.run_cli(
            "render", str(self.root / "recordings" / "demo"), "--projection", "clean"
        )
        self.assertEqual(code, 0)
        self.assertEqual(seen["args"][1], "clean")
        self.assertIn("2", out)


if __name__ == "__main__":
    unittest.main()
