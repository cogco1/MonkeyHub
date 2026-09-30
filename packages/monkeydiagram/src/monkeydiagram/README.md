# MonkeyDiagram

Drawing and diagram workflow. The Python modules verify a retained model source, project it and write the drawing as SVG/PNG, and render paper scenes as PDF, DXF and SVG; PDF/image annotation lives in the peer Web workspace. They consume ArchFlow source, CAD and project contracts without importing MonkeyArch.

- `sources`: the retained STEP (certified by its CAD receipt) or registered 3DM a drawing is derived from, verified read-only (`ElevationSource`, `NativeModelSource`, `read_elevation_source`).
- `projection/views`: view frames and in-memory projections that write nothing: model-axis elevations and axonometrics (`ElevationView`, `project_model_axis_elevation`) and section perspectives (`SectionPerspectiveView`, `project_section_perspective`). `projection/mesh_views`: look-only line views from triangles for the model view and thumbnails.
- `rendering/svg`: the deterministic SVG of drawn lines and the PNG rendered from it. `rendering/paper`: one paper scene as PDF, DXF and paper SVG.
- `drawing_runs`: the package's only project writer. It retains a drawn view's SVG, PNG and `drawing-projection-receipt` in a drawing run on the source run's base, through the repository's ports, and reads drawings back cold.
- `documentation/styles`: sheet styles and view-sheet composition. `study`: polygon observations for Study evidence.

`drawing_runs.freeze_model_axis_elevation` is the drawing boundary: it verifies a retained exact STEP through its CAD receipt, projects one model-axis elevation, and retains the SVG, PNG and `drawing-projection-receipt` in a drawing run on the source run's base. Usage:

```python
from archflow.project.repository import FilesystemProjectRepository
from monkeydiagram.drawing_runs import freeze_model_axis_elevation
from monkeydiagram.projection.views import ElevationView
from monkeydiagram.sources import ElevationSource

repository = FilesystemProjectRepository.open(project_root)
source = ElevationSource(run_id=..., step_relative_path="runs/<run>/workspaces/<ws>/<stem>.step", step_sha256=...,
                         cad_receipt_relative_path="runs/<run>/records/seat-occt-execution-<sha>.json", cad_receipt_sha256=...)
view = ElevationView(name="model-minus-y-elevation", origin=(0, y_min - 0.5, 0), look=(0, 1, 0), right=(1, 0, 0), up=(0, 0, 1),
                     crop_uv=(u_min, v_min, u_max, v_max), near_depth=0.0, far_depth=depth)
drawing = freeze_model_axis_elevation(repository, source=source, view=view, drawing_run_id="drawing-...")
```

`read_model_axis_elevation(repository, receipt_ref)` and `list_model_axis_elevations(repository, run_id)` read a drawing back cold with every sha verified.

Owners and public contracts: [system map](../../../../docs/architecture/system-map.md). Retained record schemas and P036 destinations are unchanged by this namespace migration and by the module split (#517).
