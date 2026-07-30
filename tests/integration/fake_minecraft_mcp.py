"""Deterministic stdio MCP fixture for the Minecraft adapter tests."""

from __future__ import annotations

import json
import sys
from typing import Any, BinaryIO


TOOLS = (
    "minecraft_session",
    "minecraft_preview_build_plan",
    "minecraft_execute_build_plan",
    "minecraft_capture_view",
    "minecraft_undo_last_batch",
)

ONE_PIXEL_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def read_message(stream: BinaryIO) -> dict[str, Any]:
    header = bytearray()
    while not (header.endswith(b"\r\n\r\n") or header.endswith(b"\n\n")):
        char = stream.read(1)
        if not char:
            raise EOFError
        header.extend(char)
    length = None
    for line in header.decode("ascii").splitlines():
        key, separator, value = line.partition(":")
        if separator and key.lower() == "content-length":
            length = int(value.strip())
            break
    if length is None:
        raise ValueError("missing content length")
    return json.loads(stream.read(length).decode("utf-8"))


def write_message(stream: BinaryIO, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    stream.write(f"Content-Length: {len(encoded)}\r\n\r\n".encode())
    stream.write(encoded)
    stream.flush()


def tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "structuredContent": payload,
    }


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "success"
    source = sys.stdin.buffer
    sink = sys.stdout.buffer
    while True:
        try:
            request = read_message(source)
        except EOFError:
            return 0
        request_id = request.get("id")
        method = request.get("method")
        if request_id is None:
            continue
        if method == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "fake-minecraft", "version": "1.0"},
            }
        elif method == "tools/list":
            names = TOOLS
            if mode == "missing-execute":
                names = tuple(name for name in TOOLS if name != TOOLS[2])
            result = {
                "tools": [
                    {"name": name, "description": name, "inputSchema": {}}
                    for name in names
                ]
            }
        elif method == "tools/call":
            params = request["params"]
            name = params["name"]
            arguments = params["arguments"]
            if name == "minecraft_session":
                result = tool_result(
                    {"worldId": "disposable-world", "player": "ArchFlowBot"}
                )
            elif name == "minecraft_preview_build_plan":
                if mode == "preview-failure":
                    result = {
                        "isError": True,
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "code": "BRIDGE_UNAVAILABLE",
                                        "message": "fixture bridge is offline",
                                    }
                                ),
                            }
                        ],
                        "structuredContent": {
                            "code": "BRIDGE_UNAVAILABLE",
                            "message": "fixture bridge is offline",
                        },
                    }
                else:
                    result = tool_result(
                        {
                            "planId": "plan-archflow-001",
                            "previewCommands": ["fill 0 0 0 4 0 4 minecraft:stone"],
                            "issues": [],
                            "sourcePlan": arguments,
                        }
                    )
            elif name == "minecraft_execute_build_plan":
                if arguments != {"executePlanId": "plan-archflow-001"}:
                    result = {
                        "isError": True,
                        "structuredContent": {
                            "code": "PLAN_MISMATCH",
                            "message": "previewed plan was not executed",
                        },
                    }
                else:
                    result = tool_result(
                        {
                            "planId": "plan-archflow-001",
                            "changedBlocks": 25,
                            "undoAvailable": True,
                        }
                    )
            elif name == "minecraft_capture_view":
                result = tool_result(
                    {
                        "imageBase64": ONE_PIXEL_PNG,
                        "mimeType": "image/png",
                    }
                )
            else:
                result = {
                    "isError": True,
                    "structuredContent": {
                        "code": "UNKNOWN_TOOL",
                        "message": name,
                    },
                }
        else:
            result = {}
        write_message(
            sink,
            {"jsonrpc": "2.0", "id": request_id, "result": result},
        )


if __name__ == "__main__":
    raise SystemExit(main())
