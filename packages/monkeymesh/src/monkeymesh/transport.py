"""Owned Iroh process and transparent TCP endpoints; all secrets use its private stdin."""
from __future__ import annotations

import json
from pathlib import Path
import queue
import subprocess
import threading
from typing import Callable


class MeshUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("The secure project connection is unavailable.")


class IrohTransport:
    """One endpoint with a supplied device key. This class never creates or stores keys."""

    def __init__(self, executable: Path, *, secret_key: str, mode: str,
                 target: int | None = None, enrollment_target: int | None = None,
                 ticket: str | None = None, enrollment: bool = False,
                 allowed: tuple[str, ...] = (), pairing: bool = False,
                 peer_changed: Callable[[dict], None] | None = None,
                 test_loopback: bool = False):
        self._lock = threading.Lock()
        self._ready = queue.Queue(maxsize=1)
        self._peer_changed = peer_changed
        self._closed = False
        try:
            self._process = subprocess.Popen(
                [str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._send({"mode": mode, "secretKey": secret_key,
                        "target": f"127.0.0.1:{target}" if target else None,
                        "enrollmentTarget": f"127.0.0.1:{enrollment_target}" if enrollment_target else None,
                        "ticket": ticket, "enrollment": enrollment, "allowed": list(allowed),
                        "pairing": pairing, "testLoopback": test_loopback})
            self._reader = threading.Thread(target=self._read, daemon=True, name="monkeymesh-control")
            self._reader.start()
            ready = self._ready.get(timeout=30)
            if ready is None:
                raise MeshUnavailable()
            self.node_id, self.ticket, self.port = ready["nodeId"], ready["ticket"], ready["port"]
        except (OSError, ValueError, KeyError, queue.Empty, MeshUnavailable):
            self.close()
            raise MeshUnavailable() from None

    @property
    def alive(self) -> bool:
        return not self._closed and self._process.poll() is None

    @property
    def url(self) -> str:
        if self.port is None:
            raise MeshUnavailable()
        return f"http://127.0.0.1:{self.port}"

    def _send(self, message: dict) -> None:
        with self._lock:
            try:
                self._process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
                self._process.stdin.flush()
            except (OSError, ValueError, AttributeError):
                raise MeshUnavailable() from None

    def admission(self, allowed: list[str], *, pairing: bool) -> None:
        self._send({"command": "admission", "allowed": allowed, "pairing": pairing})

    def _read(self) -> None:
        try:
            for line in self._process.stdout:
                if len(line) > 128 * 1024:
                    break
                event = json.loads(line)
                if event.get("event") == "ready":
                    self._ready.put_nowait(event)
                elif event.get("event") in {"peer", "peerClosed"}:
                    if self._peer_changed is None:
                        continue  # Without an admission owner, no bytes are forwarded.
                    self._peer_changed(event)
                    if event["event"] == "peer":
                        self._send({"command": "ack", "id": event["id"]})
        except (OSError, ValueError, KeyError, queue.Full, MeshUnavailable):
            pass
        finally:
            try:
                self._ready.put_nowait(None)
            except queue.Full:
                pass

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        process = getattr(self, "_process", None)
        if process is None:
            return
        try:
            self._send({"command": "stop"})
            process.stdin.close()
        except (OSError, ValueError, MeshUnavailable):
            pass
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            # Only this owned transport is killed, never a Runtime doing project work.
            process.kill()
            process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()
