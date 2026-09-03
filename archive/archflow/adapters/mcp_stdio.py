"""Small, dependency-free stdio MCP client used by boundary adapters."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, BinaryIO


class McpClientError(RuntimeError):
    """A bounded MCP transport or protocol failure."""


class McpClientTimeout(McpClientError):
    """The external MCP process did not answer within the configured deadline."""


@dataclass(frozen=True, slots=True)
class McpServerInfo:
    name: str
    version: str
    protocol_version: str


class StdioMcpClient:
    """Synchronous MCP client with a daemon reader and bounded waits.

    ``content-length`` is used by gemini-minecraft's Node sidecar. ``json-lines``
    is available for SDK-based servers such as Mineflayer implementations.
    """

    def __init__(
        self,
        command: tuple[str, ...],
        *,
        timeout_seconds: float = 8.0,
        framing: str = "content-length",
    ) -> None:
        if not command or any(not item.strip() for item in command):
            raise ValueError("command must contain non-empty arguments")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if framing not in {"content-length", "json-lines"}:
            raise ValueError("framing must be content-length or json-lines")
        self._command = command
        self._timeout = timeout_seconds
        self._framing = framing
        self._process: subprocess.Popen[bytes] | None = None
        self._messages: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=24)
        self._next_id = 1

    def __enter__(self) -> StdioMcpClient:
        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        assert self._process.stdout is not None
        assert self._process.stderr is not None
        threading.Thread(
            target=self._read_messages,
            args=(self._process.stdout,),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_stderr,
            args=(self._process.stderr,),
            daemon=True,
        ).start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def stderr_tail(self) -> tuple[str, ...]:
        return tuple(self._stderr_tail)

    def initialize(self) -> McpServerInfo:
        response = self.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "archflow-v4", "version": "0.1.0"},
            },
        )
        self.notify("notifications/initialized", {})
        server = response.get("serverInfo", {})
        return McpServerInfo(
            name=str(server.get("name", "unknown")),
            version=str(server.get("version", "unknown")),
            protocol_version=str(response.get("protocolVersion", "unknown")),
        )

    def list_tools(self) -> tuple[str, ...]:
        result = self.request("tools/list", {})
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise McpClientError("tools/list returned no tool array")
        names = []
        for item in tools:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.append(item["name"])
        return tuple(sorted(set(names)))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        while True:
            try:
                item = self._messages.get(timeout=self._timeout)
            except queue.Empty as exc:
                raise McpClientTimeout(
                    f"MCP request timed out: {method}"
                ) from exc
            if isinstance(item, BaseException):
                detail = " | ".join(self.stderr_tail[-4:])
                suffix = f"; stderr: {detail}" if detail else ""
                raise McpClientError(f"MCP reader stopped: {item}{suffix}") from item
            if item.get("id") != request_id:
                continue
            error = item.get("error")
            if error is not None:
                raise McpClientError(f"MCP error response for {method}: {error}")
            result = item.get("result")
            if not isinstance(result, dict):
                raise McpClientError(f"MCP result for {method} is not an object")
            return result

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
        )

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _send(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise McpClientError("MCP client is not running")
        if process.poll() is not None:
            detail = " | ".join(self.stderr_tail[-4:])
            raise McpClientError(
                f"MCP process exited with code {process.returncode}: {detail}"
            )
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if self._framing == "content-length":
            frame = f"Content-Length: {len(encoded)}\r\n\r\n".encode() + encoded
        else:
            frame = encoded + b"\n"
        try:
            process.stdin.write(frame)
            process.stdin.flush()
        except OSError as exc:
            raise McpClientError(f"could not write MCP request: {exc}") from exc

    def _read_messages(self, stream: BinaryIO) -> None:
        try:
            while True:
                message = _read_message(stream)
                self._messages.put(message)
        except BaseException as exc:
            self._messages.put(exc)

    def _read_stderr(self, stream: BinaryIO) -> None:
        for raw_line in iter(stream.readline, b""):
            line = raw_line.decode("utf-8", errors="replace").strip()
            if line:
                self._stderr_tail.append(line[:1000])


def _read_message(stream: BinaryIO) -> dict[str, Any]:
    first = stream.read(1)
    if not first:
        raise EOFError("MCP process closed stdout")
    if first in {b"{", b"["}:
        raw = first + stream.readline()
        payload = json.loads(raw.decode("utf-8"))
    else:
        header = bytearray(first)
        while not (header.endswith(b"\r\n\r\n") or header.endswith(b"\n\n")):
            char = stream.read(1)
            if not char:
                raise EOFError("MCP process closed during header")
            header.extend(char)
            if len(header) > 8192:
                raise McpClientError("MCP header exceeded 8192 bytes")
        content_length = None
        for line in header.decode("ascii", errors="replace").splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip().lower() == "content-length":
                content_length = int(value.strip())
                break
        if content_length is None or content_length < 0:
            raise McpClientError("MCP frame has no valid Content-Length")
        raw = stream.read(content_length)
        if len(raw) != content_length:
            raise EOFError("MCP process closed during message body")
        payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise McpClientError("MCP message is not a JSON object")
    return payload
