"""Run ``python -m monkeymonitor --help``."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .server import MonitorData, make_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MonkeyMonitor local usage and pricing calculator")
    parser.add_argument("command", choices=("serve", "report"))
    parser.add_argument("--data-dir", type=Path, help="Explicit Studio diagnostic directory (read only)")
    parser.add_argument("--codex-session", action="append", type=Path, default=[], help="One explicit Codex JSONL session; repeat to include subagents")
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args(argv)
    data = MonitorData(args.data_dir, tuple(args.codex_session))
    if args.command == "report":
        print(json.dumps(data.snapshot(), ensure_ascii=False, indent=2))
        return 0
    with make_server(data, args.port) as server:
        print(f"MonkeyMonitor: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
