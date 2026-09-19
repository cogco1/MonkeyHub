"""``python -m monkeycontrol``: inspect a window, run a script, render frames.

The CLI is the smallest honest caller of this package: it supplies the trace
directory and the allow-list rather than defaulting them, prints one line per
action so a demo can be read as it runs, and exits non-zero unless every
action succeeded. It decides nothing about what an action means.

    python -m monkeycontrol inspect --app notepad --trace-dir DIR
    python -m monkeycontrol run SCRIPT.json --trace-dir DIR --mode demo
    python -m monkeycontrol render RECORDING_DIR --projection presentation

It exits 0 when every action succeeded, 1 when one did not, 2 when the action
or the script is invalid, and 3 when the runtime refused the request itself.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

from .contract import ContractError
from .overlay import PROJECTIONS, render_overlays
from .runtime import (
    MODES,
    REGIONS,
    RuntimePolicy,
    RuntimeRefusal,
    build_runtime,
    process_name,
)

SCRIPT_SCHEMA = "ComputerActionScript@1"
#: The one substitution a script may ask the CLI for, so a demo script can name
#: a scratch file without hard-coding somebody's user directory. It is a plain
#: text replacement of this token, not shell or environment expansion.
TEMP_TOKEN = "${TEMP}"
#: 0 every action succeeded, 1 one did not, 2 the action or script is invalid,
#: 3 the runtime refused to start or stop what was asked of it.
OK, FAILED, INVALID, REFUSED = 0, 1, 2, 3


def expanded(payload: dict) -> dict:
    """One action payload with ``${TEMP}`` replaced wherever a path may live."""

    temp = tempfile.gettempdir()
    body = json.loads(json.dumps(payload))
    verb = body.get("action")
    if isinstance(verb, dict):
        if isinstance(verb.get("text"), str):
            verb["text"] = verb["text"].replace(TEMP_TOKEN, temp)
        if isinstance(verb.get("command"), list):
            verb["command"] = [
                item.replace(TEMP_TOKEN, temp) if isinstance(item, str) else item
                for item in verb["command"]
            ]
    expectation = body.get("verification")
    if isinstance(expectation, dict) and isinstance(expectation.get("path"), str):
        expectation["path"] = expectation["path"].replace(TEMP_TOKEN, temp)
    return body


def read_script(path: Path) -> dict:
    """One ComputerActionScript@1 file, refused by name when it is not one."""

    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict) or body.get("schema") != SCRIPT_SCHEMA:
        raise ValueError(f"{path} must declare \"schema\": \"{SCRIPT_SCHEMA}\"")
    actions = body.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError(f"{path} lists no action to run")
    if not isinstance(body.get("policy", {}), dict):
        raise ValueError(f"{path} has a policy that is not an object")
    return body


def summary_line(receipt: dict) -> str:
    """``s-0001 click Save — succeeded — VERIFY passed``."""

    target = receipt.get("target") or {}
    resolved = target.get("resolved") or {}
    requested = target.get("requested") or {}
    window = receipt.get("window") or {}
    name = (
        resolved.get("name")
        or requested.get("name")
        or requested.get("nameRegex")
        or requested.get("automationId")
        or window.get("title")
        or receipt.get("application")
        or "-"
    )
    expectation = receipt.get("verification")
    verdict = (
        f"VERIFY {expectation['status']}" if expectation else "no verification"
    )
    line = (
        f"{receipt.get('step_id')} {(receipt.get('action') or {}).get('type')} "
        f"{name} — {receipt.get('status')} — {verdict}"
    )
    refusal = receipt.get("refusal")
    if refusal:
        line += f"\n    {refusal.get('code')}: {refusal.get('message', '')}"
    return line


def _policy(script: dict, args) -> RuntimePolicy:
    declared = script.get("policy", {})
    allowed = [str(name).lower() for name in declared.get("allowed_processes", ())]
    allowed += [str(name).lower() for name in (args.allow or ())]
    return RuntimePolicy(
        allowed_processes=tuple(dict.fromkeys(allowed)),
        mode=args.mode or str(declared.get("mode") or "fast"),
        highlight_ms=int(declared.get("highlight_ms") or 600),
        focus_guard=bool(declared.get("focus_guard", True)),
    )


def _run(args) -> int:
    try:
        script = read_script(Path(args.script))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return INVALID
    runtime = build_runtime(Path(args.trace_dir), _policy(script, args))
    code = OK
    try:
        if args.record:
            started = runtime.record_start(
                args.record,
                interval_ms=args.record_interval,
                region=args.record_region,
            )
            print(
                f"recording {started['name']} every {started['interval_ms']}ms "
                f"over the {started['region']} region {started.get('bounds')}"
            )
        code = _actions(runtime, script["actions"], args)
        if args.record:
            manifest = runtime.record_stop()
            print(
                f"recording {manifest['name']}: {manifest['frame_count']} frames, "
                f"video {json.dumps(manifest['video'], ensure_ascii=False)}"
            )
    except RuntimeRefusal as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        code = REFUSED
    except ContractError as exc:  # --record takes a name a recording can have
        # Narrow on purpose: this spans the whole run, and a ValueError from
        # somewhere inside it is a fault to see, not an exit code to print.
        print(str(exc), file=sys.stderr)
        code = INVALID
    finally:
        runtime.close()
    return code


def _actions(runtime, actions: list, args) -> int:
    tally = {"succeeded": 0, "failed": 0, "refused": 0}
    for payload in actions:
        try:
            receipt = runtime.execute(expanded(payload), mode=args.mode)
        except ContractError as exc:
            print(str(exc), file=sys.stderr)
            return INVALID
        print(summary_line(receipt), flush=True)
        tally[receipt.get("status", "failed")] = (
            tally.get(receipt.get("status", "failed"), 0) + 1
        )
        if args.stop_on_failure and receipt.get("status") != "succeeded":
            break
    print(
        f"{sum(tally.values())} actions: {tally['succeeded']} succeeded, "
        f"{tally['failed']} failed, {tally['refused']} refused"
    )
    return OK if tally["succeeded"] == len(actions) else FAILED


def _inspect(args) -> int:
    # Inspecting is held to the same allow-list as acting, so asking for one
    # application is what allows it: the list is this one call's own.
    runtime = build_runtime(
        Path(args.trace_dir),
        RuntimePolicy(allowed_processes=(process_name(args.app),), mode="fast"),
    )
    try:
        answer = runtime.inspect(args.app, args.window, depth=args.depth)
    except re.error as exc:
        # --window is a Python regex, and re.error is not a ValueError: without
        # this, one unbalanced bracket prints a traceback instead of an answer.
        print(f"--window is not a Python regular expression: {exc}", file=sys.stderr)
        return INVALID
    except ValueError as exc:  # ContractError is one of these
        print(str(exc), file=sys.stderr)
        return INVALID
    finally:
        runtime.close()
    print(json.dumps(answer, ensure_ascii=False, indent=2))
    return OK


def _render(args) -> int:
    try:
        answer = render_overlays(
            Path(args.recording),
            args.projection,
            out_dir=Path(args.out) if args.out else None,
        )
    except RuntimeRefusal as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return REFUSED
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return INVALID
    print(
        f"{answer['frames']} frames rendered as {answer['projection']} "
        f"into {answer['out_dir']}"
    )
    return OK


def parser() -> argparse.ArgumentParser:
    built = argparse.ArgumentParser(
        prog="python -m monkeycontrol",
        description=__doc__.splitlines()[0],
        epilog=(
            "exit codes: 0 every action succeeded, 1 an action failed or was "
            "refused, 2 the action or the script is invalid, 3 the runtime "
            "refused the request itself (a recording already running, or a "
            "backend that is not installed)."
        ),
    )
    commands = built.add_subparsers(dest="command", required=True)
    look = commands.add_parser("inspect", help="print one window's element tree")
    look.add_argument("--app", required=True, help="process name, without .exe")
    look.add_argument("--window", help="a Python regex the window title must match")
    look.add_argument("--depth", type=int, default=6)
    look.add_argument("--trace-dir", required=True)
    look.set_defaults(handler=_inspect)
    play = commands.add_parser("run", help="run one ComputerActionScript@1 file")
    play.add_argument("script")
    play.add_argument("--trace-dir", required=True)
    play.add_argument("--mode", choices=MODES)
    play.add_argument(
        "--allow", action="append", default=[], help="an extra allowed process"
    )
    play.add_argument("--record", help="record frames under this name while it runs")
    play.add_argument("--record-interval", type=int, default=250)
    play.add_argument(
        "--record-region",
        choices=REGIONS,
        default=REGIONS[0],
        help="what a recording keeps in frame: the monitor the acted-on window "
        "is on, or the whole virtual desktop",
    )
    play.add_argument("--stop-on-failure", action="store_true")
    play.set_defaults(handler=_run)
    draw = commands.add_parser("render", help="redraw a recording's frames")
    draw.add_argument("recording", help="a recordings/<name> directory")
    draw.add_argument("--projection", choices=PROJECTIONS, default="presentation")
    draw.add_argument("--out")
    draw.set_defaults(handler=_render)
    return built


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # A receipt line carries an em dash and a tick; the Windows console
        # would otherwise refuse the whole line over one character.
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):  # pragma: no cover - a closed stream
                pass
    args = parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
