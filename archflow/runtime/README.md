# runtime

Runtime: `project_runner.run_project` binds a record to a run, projects the developed-design view, runs the seats through the producers and the compiler, checks relations, writes the stage closure and exit binding, and retains everything through P036. It writes; it never decides what the design is.

`drawing_elevation.freeze_model_axis_elevation` is the drawing boundary: it verifies a retained exact STEP through its CAD receipt, projects one model-axis elevation, and retains the SVG, PNG and `drawing-projection-receipt` in a drawing run on the source run's base. Usage:

```python
from archflow.project.repository import FilesystemProjectRepository
from archflow.runtime.drawing_elevation import ElevationSource, ElevationView, freeze_model_axis_elevation

repository = FilesystemProjectRepository.open(project_root)
source = ElevationSource(run_id=..., step_relative_path="runs/<run>/workspaces/<ws>/<stem>.step", step_sha256=...,
                         cad_receipt_relative_path="runs/<run>/records/seat-occt-execution-<sha>.json", cad_receipt_sha256=...)
view = ElevationView(name="model-minus-y-elevation", origin=(0, y_min - 0.5, 0), look=(0, 1, 0), right=(1, 0, 0), up=(0, 0, 1),
                     crop_uv=(u_min, v_min, u_max, v_max), near_depth=0.0, far_depth=depth)
drawing = freeze_model_axis_elevation(repository, source=source, view=view, drawing_run_id="drawing-...")
```

`read_model_axis_elevation(repository, receipt_ref)` and `list_model_axis_elevations(repository, run_id)` read a drawing back cold with every sha verified.

Modules and owners: `docs/SYSTEM_MAP.md` (rendered from `governance/module_registry.json`; one owner per capability). This README says what the package is for; it does not repeat the map.

Current owners: runtime.drawing_elevation, runtime.project_runner.
