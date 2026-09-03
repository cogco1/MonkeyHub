"""Dump the running app's OpenAPI schema into the gitignored ``.generated/``.

The generator's input is the schema the app itself produces, never a committed
snapshot: there is one description of this API and it is the FastAPI app. The
settings handed to ``create_app`` name a project that does not exist on purpose
--- ``create_app`` touches no filesystem, so describing the API costs nothing
and binds nothing.

Run from ``apps/archflow-studio/web`` (``npm run api:dump``).
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

WEB_DIR = Path(__file__).resolve().parent.parent
API_DIR = WEB_DIR.parent / "api"
OUTPUT = WEB_DIR / ".generated" / "openapi.json"


def main() -> None:
    sys.path.insert(0, str(API_DIR))
    from archflow_studio_api.main import create_app
    from archflow_studio_api.settings import StudioSettings

    app = create_app(StudioSettings(project_dir=Path("unbound")))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Dumped {app.title} OpenAPI to {OUTPUT}")


if __name__ == "__main__":
    main()
