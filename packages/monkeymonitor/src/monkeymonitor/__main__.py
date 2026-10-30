"""Run ``python -m monkeymonitor --help``."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from threading import Thread
from uuid import UUID

from .server import MonitorData, make_server
from .sources import AGENT_SOURCES
from .usage_file import SCHEMA

REPORT_FILE = "usage-events.json"


def _stop_from_stdin(server) -> None:
    for line in sys.stdin:
        if line.strip() == "stop":
            break
    server.shutdown()


def _agent_session(value: str) -> tuple[str, Path]:
    agent, separator, path = value.partition("=")
    if not separator or not path:
        raise argparse.ArgumentTypeError("expected <agent>=<path>")
    if agent not in AGENT_SOURCES:
        raise argparse.ArgumentTypeError(f"unknown agent {agent!r}; expected one of {', '.join(AGENT_SOURCES)}")
    return agent, Path(path)


def _instant(value: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO-8601 time with a zone") from exc
    if moment.tzinfo is None:
        raise argparse.ArgumentTypeError("expected an ISO-8601 time with a zone")
    return moment


def _window(snapshot: dict, since: datetime | None, until: datetime | None) -> dict:
    """Keep events whose started_at is at or after since and before until."""
    kept, unplaced = [], 0
    for row in snapshot["events"]:
        try:
            started = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
            inside = (since is None or started >= since) and (until is None or started < until)
        except (ValueError, TypeError):
            unplaced += 1
            continue
        if inside:
            kept.append(row)
    if unplaced:
        snapshot["warnings"].append(f"{unplaced} 条记录的开始时间没有时区，无法判断是否落在时间窗内；未计入本次切片。")
    snapshot["events"] = kept
    return snapshot


def _write_report(directory: Path, report: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = (directory / REPORT_FILE).resolve()
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MonkeyMonitor usage, trace and pricing diagnostics")
    parser.add_argument("command", choices=("serve", "report"))
    parser.add_argument("--data-dir", type=Path, help="Explicit Studio diagnostic directory (read only)")
    parser.add_argument("--agent-session", action="append", type=_agent_session, default=[], metavar="AGENT=PATH",
                        help=f"One explicit agent usage file; repeat as needed (no discovery). AGENT is one of {', '.join(AGENT_SOURCES)}")
    parser.add_argument("--codex-session", action="append", type=Path, default=[], help="Same as --agent-session codex=PATH")
    parser.add_argument("--codex-bindings-url", help="Loopback Hub usage-sources endpoint for exactly bound Codex sessions")
    parser.add_argument("--since", help="report only: keep events that started at or after this zoned ISO-8601 time")
    parser.add_argument("--until", help="report only: keep events that started before this zoned ISO-8601 time")
    parser.add_argument("--output-dir", type=Path, help=f"report only: write {REPORT_FILE} into this explicit directory instead of stdout")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--managed-stdin", action="store_true", help="Stop this managed API server on stdin stop or EOF")
    parser.add_argument("--managed-instance-id", type=UUID, help="Hub-owned instance UUID (requires --managed-stdin)")
    args = parser.parse_args(argv)
    if args.managed_stdin != (args.managed_instance_id is not None):
        parser.error("--managed-stdin and --managed-instance-id must be used together")
    if args.command != "serve" and args.managed_stdin:
        parser.error("managed options require serve")
    if args.command != "report" and (args.since or args.until or args.output_dir):
        parser.error("--since, --until and --output-dir require report")
    try:
        since = _instant(args.since) if args.since else None
        until = _instant(args.until) if args.until else None
    except argparse.ArgumentTypeError as exc:
        parser.error(f"--since/--until: {exc}")
    if since and until and since >= until:
        parser.error("--since must be earlier than --until")
    sessions: dict[str, list[Path]] = {"codex": list(args.codex_session)}
    for agent, path in args.agent_session:
        sessions.setdefault(agent, []).append(path)
    try:
        data = MonitorData(args.data_dir, codex_bindings_url=args.codex_bindings_url, agent_sessions=sessions)
    except ValueError as exc:
        parser.error(str(exc))
    if args.command == "report":
        snapshot = data.snapshot()
        report = {"schema": SCHEMA, "events": snapshot["events"], "warnings": snapshot["warnings"]}
        if since or until:
            report = _window(report, since, until)
            report["window"] = {"since": args.since, "until": args.until}
        if args.output_dir is not None:
            target = _write_report(args.output_dir, report)
            print(json.dumps({"output": target.as_posix(), "events": len(report["events"]),
                              "warnings": len(report["warnings"])}, ensure_ascii=False))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    instance_id = str(args.managed_instance_id) if args.managed_instance_id is not None else None
    with make_server(data, args.port, managed_instance_id=instance_id) as server:
        if args.managed_stdin:
            Thread(target=_stop_from_stdin, args=(server,), daemon=True).start()
        print(f"MonkeyMonitor API: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
