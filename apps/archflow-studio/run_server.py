from __future__ import annotations

import argparse
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = APP_ROOT.parents[1]

for path in (REPOSITORY_ROOT, APP_ROOT):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from backend.server import run  # noqa: E402 - paths are established first


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ArchFlow Studio locally")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    run(host=args.host, port=args.port, static_dir=APP_ROOT / "dist")


if __name__ == "__main__":
    main()

