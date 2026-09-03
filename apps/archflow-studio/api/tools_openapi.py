"""Dump the Studio API OpenAPI schema to ``openapi.json`` beside this script.

The snapshot is committed so a change to a route or a transport shape arrives
as a reviewable schema diff instead of as a surprise in the web client.

Run it from the repository root:

    py -3.12 apps/archflow-studio/api/tools_openapi.py
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

API_ROOT = Path(__file__).resolve().parent
SNAPSHOT_PATH = API_ROOT / "openapi.json"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from archflow_studio_api.main import create_app  # noqa: E402
from archflow_studio_api.settings import StudioSettings  # noqa: E402


def render() -> str:
    """Render the schema of an app that is deliberately bound to nothing."""

    app = create_app(StudioSettings(project_dir=Path("unbound-placeholder")))
    return (
        json.dumps(
            app.openapi(),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    )


def main() -> None:
    SNAPSHOT_PATH.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
