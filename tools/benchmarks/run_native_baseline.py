"""Run the ``stage-a-massing`` task in a native modeller through Codex's own MCP (#419).

The baseline for the construction-first comparison: the same Codex CLI and
the same design task, but the agent scripts a CAD kernel directly - a Python
program against OCCT (the ``OCP`` package), run with its own shell - the way
public cases script Blender (bpy) or SketchUp (Ruby), then exports STEP. The result is judged by the same name-agnostic check as the
Hub paths (``massing_check.check_massing``); the metrics come from the CLI's
own JSONL event stream.

This is an explicitly requested, paid measurement, not a CI test:

    python tools/benchmarks/run_native_baseline.py --output D:/path/outside/the/repo

Why not a desktop modeller on the measuring machine (2026-09-28): SketchUp 2025
started with ``-RubyStartup`` never ran its script within 120 s (the welcome
screen waits for a person); Codex's own Rhino MCP could neither start Rhino 8
nor adopt a running one from inside Codex's process job (CreateProcess error
5 in four attempts, with and without the command sandbox); Blender is not
installed. ``--path rhino`` keeps the Rhino attempt reproducible.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from massing_check import check_massing  # noqa: E402

RHINO_TOOLS = (
    "close_doc", "close_slot", "get_commands", "get_context", "get_selection", "get_viewport_image",
    "list_objects", "list_slots", "open_doc", "run_command", "run_csharp", "run_python", "save_doc",
    "set_camera", "set_layer_material", "set_selection", "spawn_slot", "zoom_to_layer", "zoom_to_object",
)

TASK = (
    "This is an isolated design benchmark. Only use the connected Rhino tools. "
    "In a new, empty Rhino 8 document in metres (plan axes x and y, height along z), add a generic massing study; "
    "do not classify anything as a wall, slab or other building part. "
    "(1) A ground block with its plan corner at (20, 0), 12 m along x and 8 m along y, 3.2 m tall, standing on z = 0. "
    "(2) An upper block 10 m by 8 m and 3 m tall standing on top of the ground block, flush with the ground block's faces at x = 20 and at both y faces, so it spans 20 to 30 along x. "
    "(3) A roof slab 0.3 m thick on top of the upper block, overhanging it by 0.5 m on every side. "
    "(4) Four window recesses cut 0.3 m deep into the ground block from its face at y = 0, each 1.2 m wide along x and 1.5 m tall with the sill 0.9 m above z = 0, their near edges at x = 21.5, 24.5, 27.5 and 30.5. "
    "When the model is done, export exactly the final solids (no helper or cutter geometry) to the STEP file {step}. "
    "Give one short result sentence. Do not use shell tools, and do not read or write files other than that export."
)


def native_codex() -> str:
    """The CLI's own executable, so arguments reach it without a batch-file shim."""

    found = shutil.which("codex")
    if not found:
        raise SystemExit("codex is not on PATH")
    if sys.platform != "win32":
        return found
    modules = Path(found).resolve().parent / "node_modules/@openai"
    for root in (modules / "codex/node_modules/@openai/codex-win32-x64", modules / "codex-win32-x64", modules / "codex"):
        binary = root / "vendor/x86_64-pc-windows-msvc/bin/codex.exe"
        if binary.is_file():
            return str(binary)
    raise SystemExit("the native codex.exe was not found beside the npm shim")


OCP_TASK = (
    "This is an isolated design benchmark. Work only in the current directory. "
    "Write and run a Python program that uses the installed OCP package (the OpenCascade bindings; import from OCP.*, install nothing) "
    "to model, in metres (plan axes x and y, height along z), a generic massing study; do not classify anything as a wall, slab or other building part. "
    "(1) A ground block with its plan corner at (20, 0), 12 m along x and 8 m along y, 3.2 m tall, standing on z = 0. "
    "(2) An upper block 10 m by 8 m and 3 m tall standing on top of the ground block, flush with the ground block's faces at x = 20 and at both y faces, so it spans 20 to 30 along x. "
    "(3) A roof slab 0.3 m thick on top of the upper block, overhanging it by 0.5 m on every side. "
    "(4) Four window recesses cut 0.3 m deep into the ground block from its face at y = 0, each 1.2 m wide along x and 1.5 m tall with the sill 0.9 m above z = 0, their near edges at x = 21.5, 24.5, 27.5 and 30.5. "
    "Write exactly the final solids (no helper or cutter geometry) to the STEP file {step} with the STEP length unit set to metres. "
    "Give one short result sentence. Do not read or write files outside the current directory except that STEP file."
)


def summarize(events: list[dict]) -> dict:
    """Counts from the CLI's own events: tool calls, failures, model turns, tokens."""

    completed = [event["item"] for event in events
                 if event.get("type") == "item.completed" and isinstance(event.get("item"), dict)]
    tools = [item for item in completed if item.get("type") in ("mcp_tool_call", "command_execution", "file_change", "web_search")]
    failed = [item for item in tools
              if item.get("status") == "failed" or item.get("error")
              or (item.get("type") == "command_execution" and item.get("exit_code") not in (0, None))]
    usage = next((event.get("usage") for event in reversed(events) if event.get("type") == "turn.completed"), None)
    by_tool: dict[str, int] = {}
    for item in tools:
        name = f"{item.get('server', '')}.{item.get('tool', '')}" if item.get("type") == "mcp_tool_call" else item["type"]
        by_tool[name] = by_tool.get(name, 0) + 1
    return {
        "tool_calls": len(tools),
        "failed_tool_calls": len(failed),
        "tool_calls_by_name": by_tool,
        # Each tool call is answered by one model step, and one more step ends the turn.
        "model_steps": len([item for item in completed if item.get("type") in ("reasoning", "agent_message")]),
        "agent_messages": len([item for item in completed if item.get("type") == "agent_message"]),
        "usage": usage,
        "turn_failed": any(event.get("type") == "turn.failed" for event in events),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Explicit directory outside the repository")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--model", help="Pin the Codex model for comparable runs")
    parser.add_argument("--path", choices=("ocp", "rhino"), default="ocp")
    parser.add_argument("--sandbox", choices=("workspace-write", "danger-full-access"), default="workspace-write",
                        help="Codex command sandbox for the ocp path")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    workdir = output / "work"
    workdir.mkdir(exist_ok=True)
    # The command sandbox writes only inside the working directory.
    step = workdir / "study.step"
    if step.exists():
        raise SystemExit(f"{step} already exists; use a new output directory")
    overrides = ["-c", "mcp_servers.openaiDeveloperDocs.enabled=false", "-c", "mcp_servers.node_repl.enabled=false"]
    if args.path == "rhino":
        for tool in RHINO_TOOLS:
            overrides += ["-c", f'mcp_servers.rhino.tools.{tool}.approval_mode="approve"']
        sandbox, task = "read-only", TASK
    else:
        overrides += ["-c", "mcp_servers.rhino.enabled=false"]
        # Codex's Windows command sandbox runs commands as a user that may not
        # start the installed Python ("Access denied", 2026-09-28); the task
        # itself confines the agent to its working directory.
        sandbox, task = args.sandbox, OCP_TASK
    command = [native_codex(), "exec", "--json", "--skip-git-repo-check", "-C", str(workdir), "-s", sandbox,
               *overrides, *(["-m", args.model] if args.model else []),
               task.format(step=str(step).replace("\\", "/"))]
    started = time.monotonic()
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=args.timeout)
    wall_ms = round((time.monotonic() - started) * 1000)
    (output / "events.jsonl").write_text(completed.stdout, encoding="utf-8")
    (output / "stderr.txt").write_text(completed.stderr, encoding="utf-8")
    events = []
    for line in completed.stdout.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            events.append(value)
    metrics = summarize(events)
    geometry = check_massing(step) if step.exists() else {"ok": False, "reason": "no STEP exported"}
    report = {"benchmark": {"scenario": "stage-a-massing", "path": f"native-{args.path}", "model": args.model, "sandbox": args.sandbox},
              "task_success": bool(completed.returncode == 0 and geometry.get("ok")),
              "returncode": completed.returncode, "wall_ms": wall_ms, "metrics": metrics, "geometry": geometry}
    (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["task_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
