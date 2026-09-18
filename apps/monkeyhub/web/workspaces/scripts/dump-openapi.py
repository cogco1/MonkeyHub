"""Generate the Hub and project-runtime schemas used by the single Hub frontend."""
from __future__ import annotations
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = WORKSPACE_DIR.parent
ROOT = WEB_DIR.parents[2]

def main() -> None:
    sys.path[:0] = [str(ROOT), str(ROOT / "apps/archflow-studio/api"), str(ROOT / "apps/monkeyhub/api")]
    from archflow_studio_api.main import create_app as runtime_app
    from archflow_studio_api.settings import StudioSettings
    from monkeyhub_api.main import create_app as hub_app, HubSettings
    with TemporaryDirectory(prefix="monkeyhub-schema-") as directory:
        applications = (
            (runtime_app(StudioSettings(project_dir=Path(directory) / "unbound")), WORKSPACE_DIR),
            (hub_app(HubSettings(runtime_root=Path(directory))), WEB_DIR),
        )
        for app, target in applications:
            output = target / ".generated/openapi.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"Dumped {app.title} OpenAPI to {output}")

if __name__ == "__main__":
    main()
