"""Run ``python -m monkeymonitor --help``."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from threading import Thread
from uuid import UUID

from .server import MonitorData, make_server


def _stop_from_stdin(server) -> None:
    for line in sys.stdin:
        if line.strip() == "stop":
            break
    server.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MonkeyMonitor usage, trace and pricing diagnostics")
    parser.add_argument("command", choices=("serve", "report"))
    parser.add_argument("--data-dir", type=Path, help="Explicit Studio diagnostic directory (read only)")
    parser.add_argument("--codex-session", action="append", type=Path, default=[], help="One explicit Codex JSONL source; repeat for known related sources (no discovery)")
    parser.add_argument("--codex-bindings-url", help="Loopback Hub usage-sources endpoint for exactly bound Codex sessions")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--managed-stdin", action="store_true", help="Stop this managed API server on stdin stop or EOF")
    parser.add_argument("--managed-instance-id", type=UUID, help="Hub-owned instance UUID (requires --managed-stdin)")
    args = parser.parse_args(argv)
    if args.managed_stdin != (args.managed_instance_id is not None):
        parser.error("--managed-stdin and --managed-instance-id must be used together")
    if args.command != "serve" and args.managed_stdin:
        parser.error("managed options require serve")
    try:
        data = MonitorData(args.data_dir, tuple(args.codex_session), codex_bindings_url=args.codex_bindings_url)
    except ValueError as exc:
        parser.error(str(exc))
    if args.command == "report":
        print(json.dumps(data.snapshot(), ensure_ascii=False, indent=2))
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
