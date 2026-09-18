"""The JSON-lines host protocol, driven by a fake PowerShell worker.

No test here spawns PowerShell: every case injects a ``launcher`` that returns
a scripted process, so the protocol, the id matching, the error mapping and the
single transparent restart are observable on any operating system.
"""

from __future__ import annotations

import json
import queue
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from monkeycontrol.host import (
    EXECUTION_HOST,
    PRESENTATION_HOST,
    HostError,
    HostProcess,
)

HELLO = {"host": "execution", "version": "1", "dpi_aware": True, "screen": [1920, 1080]}


class _Pipe:
    """A readable line pipe the fake host pushes replies into."""

    def __init__(self) -> None:
        self._lines: queue.Queue[str] = queue.Queue()

    def push(self, line: str) -> None:
        self._lines.put(line)

    def close(self) -> None:
        self._lines.put("")

    def readline(self) -> str:
        return self._lines.get()


class FakeHost:
    """A ``Popen``-like worker that answers each written line from a script."""

    def __init__(
        self, answer, *, closed: bool = False, stubborn: bool = False
    ) -> None:
        self._answer = answer
        self._stubborn = stubborn
        self.stdout = _Pipe()
        self.stderr = _Pipe()
        self.stderr.close()
        self.written: list[dict] = []
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.stdin = self
        if closed:
            self.returncode = 1
            self.stdout.close()
        self._closed = closed

    # The process writes into its own stdin pipe, so the fake is the pipe too.
    def write(self, line: str) -> None:
        if self._closed:
            raise OSError("the host is gone")
        request = json.loads(line)
        self.written.append(request)
        for reply in self._answer(self, request):
            self.stdout.push(json.dumps(reply))

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self._closed = True

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0
        self.stdout.close()

    def kill(self) -> None:
        self.killed = True
        self.terminate()

    def wait(self, timeout: float | None = None) -> int:
        if self._stubborn and not self.terminated:
            raise subprocess.TimeoutExpired("powershell.exe", timeout or 0.0)
        self.returncode = 0
        return 0


def scripted(**replies):
    """Answer ``hello`` and each named op with one canned result."""

    def answer(host: FakeHost, request: dict) -> list[dict]:
        ident = request["id"]
        if request["op"] == "hello":
            return [{"id": ident, "ok": True, "result": HELLO}]
        if request["op"] == "exit":
            return []
        if request["op"] not in replies:
            return [
                {
                    "id": ident,
                    "ok": False,
                    "error": {"code": "HOST_ERROR", "message": "unknown op"},
                }
            ]
        return [{"id": ident, "ok": True, "result": replies[request["op"]]}]

    return answer


class Launcher:
    """Records every spawn and hands out the next scripted worker."""

    def __init__(self, *hosts: FakeHost) -> None:
        self._hosts = list(hosts)
        self.commands: list[list[str]] = []
        self.spawned: list[FakeHost] = []

    def __call__(self, command) -> FakeHost:
        self.commands.append(list(command))
        host = self._hosts[min(len(self.spawned), len(self._hosts) - 1)]
        self.spawned.append(host)
        return host


class HostProcessTests(unittest.TestCase):
    def process(self, *hosts: FakeHost, **kwargs) -> tuple[HostProcess, Launcher]:
        launcher = Launcher(*hosts)
        host = HostProcess(
            EXECUTION_HOST, apartment="mta", launcher=launcher, **kwargs
        )
        self.addCleanup(host.stop)
        return host, launcher

    def test_the_shipped_host_scripts_exist(self) -> None:
        for script in (EXECUTION_HOST, PRESENTATION_HOST):
            self.assertTrue(Path(script).is_file(), script)

    def test_start_says_hello_and_returns_what_the_host_answered(self) -> None:
        worker = FakeHost(scripted())
        host, launcher = self.process(worker)
        self.assertEqual(host.start(), HELLO)
        self.assertEqual([entry["op"] for entry in worker.written], ["hello"])
        self.assertEqual(worker.written[0]["args"], {})
        self.assertTrue(host.alive)
        command = launcher.commands[0]
        self.assertEqual(Path(command[0]).name.lower(), "powershell.exe")
        self.assertIn("-Mta", command)
        self.assertIn("-NoProfile", command)
        self.assertIn("-NonInteractive", command)
        self.assertEqual(command[-2:], ["-File", str(EXECUTION_HOST)])

    def test_the_apartment_reaches_the_command_line(self) -> None:
        launcher = Launcher(FakeHost(scripted()))
        host = HostProcess(
            PRESENTATION_HOST, apartment="sta", launcher=launcher
        )
        self.addCleanup(host.stop)
        host.start()
        self.assertIn("-Sta", launcher.commands[0])
        self.assertNotIn("-Mta", launcher.commands[0])

    def test_an_unknown_apartment_is_refused_before_anything_spawns(self) -> None:
        launcher = Launcher(FakeHost(scripted()))
        with self.assertRaises(ValueError):
            HostProcess(EXECUTION_HOST, apartment="free", launcher=launcher)
        self.assertEqual(launcher.commands, [])

    def test_a_request_is_answered_by_id_and_stale_replies_are_ignored(self) -> None:
        def answer(host: FakeHost, request: dict) -> list[dict]:
            if request["op"] == "hello":
                return [{"id": request["id"], "ok": True, "result": HELLO}]
            # A reply to a request that already timed out precedes this one.
            return [
                {"id": request["id"] - 100, "ok": True, "result": {"stale": True}},
                {"id": request["id"], "ok": True, "result": {"pid": 42}},
            ]

        worker = FakeHost(answer)
        host, _ = self.process(worker)
        host.start()
        self.assertEqual(host.request("foreground"), {"pid": 42})
        self.assertEqual(worker.written[1]["op"], "foreground")

    def test_arguments_travel_as_the_args_object(self) -> None:
        worker = FakeHost(scripted(click={}))
        host, _ = self.process(worker)
        host.start()
        self.assertEqual(host.request("click", x=10, y=20, button="left"), {})
        self.assertEqual(
            worker.written[1],
            {"id": 2, "op": "click", "args": {"x": 10, "y": 20, "button": "left"}},
        )

    def test_a_refusal_keeps_the_hosts_own_code_and_message(self) -> None:
        def answer(host: FakeHost, request: dict) -> list[dict]:
            if request["op"] == "hello":
                return [{"id": request["id"], "ok": True, "result": HELLO}]
            return [
                {
                    "id": request["id"],
                    "ok": False,
                    "error": {
                        "code": "TARGET_UNRESOLVED",
                        "message": "no such element",
                    },
                }
            ]

        host, launcher = self.process(FakeHost(answer))
        host.start()
        with self.assertRaises(HostError) as caught:
            host.request("find", handle=1)
        self.assertEqual(caught.exception.code, "TARGET_UNRESOLVED")
        self.assertIn("no such element", str(caught.exception))
        # A refusal is an answer, not a crash: the worker is left alone.
        self.assertEqual(len(launcher.spawned), 1)
        self.assertTrue(host.alive)

    def test_a_dead_host_is_restarted_once_and_the_retry_is_invisible(self) -> None:
        dead = FakeHost(scripted(), closed=True)
        live = FakeHost(scripted(process={"name": "notepad"}))
        launcher = Launcher(dead, live)
        host = HostProcess(EXECUTION_HOST, apartment="mta", launcher=launcher)
        self.addCleanup(host.stop)
        host.start()
        self.assertEqual(host.request("process", pid=7), {"name": "notepad"})
        self.assertEqual(len(launcher.spawned), 2)
        self.assertEqual([entry["op"] for entry in live.written], ["hello", "process"])

    def test_a_second_crash_is_a_host_error(self) -> None:
        first = FakeHost(scripted(), closed=True)
        second = FakeHost(scripted(), closed=True)
        launcher = Launcher(first, second)
        host = HostProcess(EXECUTION_HOST, apartment="mta", launcher=launcher)
        self.addCleanup(host.stop)
        with self.assertRaises(HostError) as caught:
            host.start()
        self.assertEqual(caught.exception.code, "HOST_ERROR")
        self.assertEqual(len(launcher.spawned), 2)

    def test_a_silent_host_times_out_without_a_retry(self) -> None:
        def answer(host: FakeHost, request: dict) -> list[dict]:
            if request["op"] == "hello":
                return [{"id": request["id"], "ok": True, "result": HELLO}]
            return []

        launcher = Launcher(FakeHost(answer))
        host = HostProcess(
            EXECUTION_HOST, apartment="mta", timeout_s=0.2, launcher=launcher
        )
        self.addCleanup(host.stop)
        host.start()
        with self.assertRaises(HostError) as caught:
            host.request("inspect", handle=1)
        self.assertEqual(caught.exception.code, "HOST_ERROR")
        self.assertIn("inspect", str(caught.exception))
        # Retrying an action that may already have reached the screen is worse
        # than refusing it, so a timeout never replays.
        self.assertEqual(len(launcher.spawned), 1)

    def test_stop_asks_the_host_to_leave_its_loop(self) -> None:
        worker = FakeHost(scripted())
        host, _ = self.process(worker)
        host.start()
        host.stop()
        self.assertEqual([entry["op"] for entry in worker.written], ["hello", "exit"])
        self.assertFalse(host.alive)
        # Stopping twice is how a cleanup after an explicit stop behaves.
        host.stop()
        self.assertEqual([entry["op"] for entry in worker.written], ["hello", "exit"])

    def test_a_host_that_will_not_leave_is_terminated(self) -> None:
        worker = FakeHost(scripted(), stubborn=True)
        host, _ = self.process(worker)
        host.start()
        host.stop()
        self.assertTrue(worker.terminated)
        self.assertFalse(host.alive)

    def test_a_request_before_start_is_a_host_error(self) -> None:
        host, _ = self.process(FakeHost(scripted()))
        with self.assertRaises(HostError) as caught:
            host.request("foreground")
        self.assertEqual(caught.exception.code, "HOST_ERROR")

    def test_without_powershell_the_backend_is_unavailable(self) -> None:
        host = HostProcess(EXECUTION_HOST, apartment="mta")
        self.addCleanup(host.stop)
        with mock.patch("monkeycontrol.host.powershell_path", return_value=None):
            with self.assertRaises(HostError) as caught:
                host.start()
        self.assertEqual(caught.exception.code, "BACKEND_UNAVAILABLE")
        self.assertFalse(host.alive)


if __name__ == "__main__":
    unittest.main()
