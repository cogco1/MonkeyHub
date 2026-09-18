"""One PowerShell worker, spoken to in JSON lines.

MonkeyControl reaches the Windows desktop through two long-lived PowerShell
hosts: an MTA one that holds UI Automation and SendInput, and an STA one that
holds the WPF overlay and screen capture. This module owns the transport only
-- spawning, framing, id matching, timeouts, and replacing a worker that died,
replaying the request only when repeating it could change nothing -- and
nothing about what an op means. It writes no file: every byte MonkeyControl
keeps goes through :mod:`monkeycontrol.store`.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path

HOSTS = Path(__file__).resolve().parent / "hosts"
EXECUTION_HOST = HOSTS / "execution_host.ps1"
PRESENTATION_HOST = HOSTS / "presentation_host.ps1"
APARTMENTS = {"mta": "-Mta", "sta": "-Sta"}
#: The ops of both hosts that only look, so asking a restarted worker again
#: cannot repeat anything that reached the screen. Every other op -- the whole
#: of actuation, and the overlay, which a viewer would see twice -- is refused
#: after a crash instead of replayed.
OBSERVATIONS = frozenset(
    {
        "hello",
        "windows",
        "inspect",
        "find",
        "read",
        "foreground",
        "process",
        "screenshot",
        "clear",
    }
)
#: How long ``stop`` waits for the host to leave its loop before terminating it.
EXIT_GRACE_S = 2.0
_DIAGNOSTICS = 40
_EOF = object()


class HostError(RuntimeError):
    """A host refused, died or stayed silent; ``code`` says which."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class _HostGone(Exception):
    """Internal: the worker is no longer answering, so it may be restarted."""


def powershell_path() -> str | None:
    """Windows PowerShell 5.1, or ``None`` where this backend cannot run.

    The seam is a function so a test can say the backend is missing without
    pretending to be another operating system, and so the absence is answered
    with a refusal rather than a traceback from ``subprocess``.
    """

    if os.name != "nt":
        return None
    return shutil.which("powershell.exe")


def _popen(command: Iterable[str]) -> subprocess.Popen:
    """Spawn one host with no console window and UTF-8 pipes."""

    return subprocess.Popen(
        list(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


class HostProcess:
    """One PowerShell worker speaking JSON lines.

    A request is ``{"id", "op", "args"}`` on stdin; a reply is
    ``{"id", "ok": true, "result"}`` or ``{"id", "ok": false, "error"}`` on
    stdout, one per line. Anything else the host prints is diagnostics and is
    kept only to make an error message useful.
    """

    def __init__(
        self,
        script: Path,
        *,
        apartment: str,
        timeout_s: float = 20.0,
        launcher: Callable[..., subprocess.Popen] | None = None,
    ) -> None:
        if apartment not in APARTMENTS:
            raise ValueError(
                f"apartment must be one of {', '.join(sorted(APARTMENTS))}, "
                f"not {apartment!r}"
            )
        self._script = Path(script)
        self._apartment = apartment
        self._timeout = float(timeout_s)
        self._launcher = launcher
        self._process: subprocess.Popen | None = None
        self._replies: queue.Queue = queue.Queue()
        self._diagnostics: deque[str] = deque(maxlen=_DIAGNOSTICS)
        self._readers: list[threading.Thread] = []
        self._counter = 0
        self._hello: dict = {}
        self._lock = threading.Lock()
        # Its own lock: the reader threads append here while a request that is
        # already holding ``_lock`` reads the tail for an error message.
        self._notes = threading.Lock()

    def __enter__(self) -> "HostProcess":
        self.start()
        return self

    def __exit__(self, *_exception) -> None:
        self.stop()

    @property
    def script(self) -> Path:
        return self._script

    @property
    def hello(self) -> dict:
        """What the host said about itself, or ``{}`` before it started."""

        return dict(self._hello)

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> dict:
        """Spawn the host and greet it; the greeting is its capabilities.

        Starting a worker that is already running is not a second worker: the
        greeting it already gave is returned. A worker that has since exited is
        cleaned up first, so no handle is ever left dangling.
        """

        with self._lock:
            if self._process is not None:
                if self._process.poll() is None:
                    return dict(self._hello)
                self._terminate()
            self._spawn()
            try:
                self._hello = self._exchange("hello", {})
            except _HostGone:
                # A host that cannot even say hello is broken the same way a
                # crashed one is, and gets the same single retry.
                self._terminate()
                self._spawn()
                try:
                    self._hello = self._exchange("hello", {})
                except _HostGone as exc:
                    self._terminate()
                    raise self._dead("hello") from exc
        return dict(self._hello)

    def request(self, op: str, **args) -> dict:
        """Ask the host to do one thing and return its result.

        A refusal the host expressed is raised with the host's own code, which
        is how ``TARGET_UNRESOLVED`` and ``WINDOW_NOT_FOUND`` reach the caller.

        A worker that died is always replaced, but only an op in
        :data:`OBSERVATIONS` is asked again: a click or a keystroke may already
        have reached the screen before the worker went, and sending it twice is
        worse than saying so. A worker that merely stayed silent is not
        replayed either, for the same reason.
        """

        with self._lock:
            try:
                return self._exchange(op, args)
            except _HostGone:
                self._terminate()
                self._spawn()
                try:
                    self._hello = self._exchange("hello", {})
                except _HostGone as exc:
                    self._terminate()
                    raise self._dead(op) from exc
                if op not in OBSERVATIONS:
                    raise HostError(
                        "HOST_ERROR",
                        f"{self._script.name} died while answering {op}, which "
                        "may or may not have reached the screen; it was not "
                        "sent again" + self._trailing(),
                    )
                try:
                    return self._exchange(op, args)
                except _HostGone as exc:
                    self._terminate()
                    raise self._dead(op) from exc

    def stop(self) -> None:
        """Ask the host to leave its loop, then make sure it is gone."""

        with self._lock:
            process = self._process
            if process is None:
                return
            if process.poll() is None:
                try:
                    self._send({"id": 0, "op": "exit", "args": {}})
                    process.wait(timeout=EXIT_GRACE_S)
                except (OSError, ValueError, _HostGone, subprocess.TimeoutExpired):
                    pass
            self._terminate()

    def _spawn(self) -> None:
        launcher = self._launcher
        if launcher is None:
            executable = powershell_path()
            if executable is None:
                raise HostError(
                    "BACKEND_UNAVAILABLE",
                    "monkeycontrol drives the desktop through Windows PowerShell, "
                    "which this machine does not have",
                )
            if not self._script.is_file():
                raise HostError(
                    "BACKEND_UNAVAILABLE", f"the host script {self._script} is missing"
                )
            launcher = _popen
        else:
            executable = "powershell.exe"
        # The queue is bound into the reader below rather than read off self,
        # so a thread still draining the worker that just died cannot drop an
        # end-of-file marker into the replies of the one replacing it.
        replies: queue.Queue = queue.Queue()
        self._replies = replies
        with self._notes:
            self._diagnostics.clear()
        self._counter = 0
        self._process = launcher(
            [
                executable,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                APARTMENTS[self._apartment],
                "-File",
                str(self._script),
            ]
        )
        self._readers = [
            self._reader(
                self._process.stdout, lambda line: self._reply(replies, line)
            ),
            self._reader(self._process.stderr, self._note),
        ]

    def _reader(self, stream, consume: Callable[[object], None]) -> threading.Thread:
        def drain() -> None:
            try:
                while True:
                    line = stream.readline()
                    if not line:
                        break
                    consume(line)
            except (OSError, ValueError):
                pass
            finally:
                consume("")

        thread = threading.Thread(target=drain, daemon=True)
        thread.start()
        return thread

    def _reply(self, replies: queue.Queue, line: str) -> None:
        """Queue one parsed reply; anything unparsable is only diagnostics."""

        if not line:
            replies.put(_EOF)
            return
        text = line.strip().lstrip("\ufeff")
        if not text:
            return
        try:
            message = json.loads(text)
        except ValueError:
            self._note(text)
            return
        if isinstance(message, dict) and "id" in message:
            replies.put(message)
        else:
            self._note(text)

    def _send(self, request: dict) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise _HostGone("the host is not running")
        try:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise _HostGone(f"the host closed its input: {exc}") from exc

    def _exchange(self, op: str, args: dict) -> dict:
        if self._process is None:
            raise HostError(
                "HOST_ERROR", f"{op} was asked of a host that never started"
            )
        self._counter += 1
        ident = self._counter
        self._send({"id": ident, "op": op, "args": args})
        deadline = time.monotonic() + self._timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                message = self._replies.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue
            if message is _EOF:
                raise _HostGone(f"the host closed its output while answering {op}")
            if message.get("id") != ident:
                # A late answer to a request that already gave up: drop it.
                continue
            if message.get("ok"):
                result = message.get("result")
                return result if isinstance(result, dict) else {}
            error = message.get("error") or {}
            raise HostError(
                str(error.get("code") or "HOST_ERROR"),
                str(error.get("message") or f"the host refused {op}"),
            )
        raise HostError(
            "HOST_ERROR",
            f"{self._script.name} did not answer {op} within {self._timeout:g}s"
            + self._trailing(),
        )

    def _dead(self, op: str) -> HostError:
        return HostError(
            "HOST_ERROR",
            f"{self._script.name} died twice while answering {op}" + self._trailing(),
        )

    def _note(self, line: str) -> None:
        """Keep one line of host diagnostics, from whichever thread read it."""

        with self._notes:
            self._diagnostics.append(line)

    def _trailing(self) -> str:
        """The host's last word, for an error message.

        Snapshotting under the lock matters: a reader thread appends here
        while the failing request reads it, and a deque mutated mid-iteration
        would raise over the failure it was meant to explain.
        """

        with self._notes:
            lines = [line.strip() for line in self._diagnostics if line.strip()]
        return f"; last host output: {lines[-1]}" if lines else ""

    def _terminate(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=EXIT_GRACE_S)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
        self._readers = []
