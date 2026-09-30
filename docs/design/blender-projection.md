# Blender Projection V1

MonkeyHub's StateRecord/compiled program and OCCT remain the design and geometry
authority. A verified OCCT STEP is cold-read and tessellated by the existing OCCT
adapter, then mirrored into Blender. Blender receives vertices and triangles; it
does not interpret walls, openings, columns, lofts or architectural constraints.

This extends `adapters.cad_execution` and `runtime.project_runner`. The existing
legacy Blender CAD backend remains available for its current consumers; selecting
this projection never calls that builder. Rhino is not launched or required.
The normal OCCT preview still uses the existing rhino3dm file library; that is
not a Rhino application dependency. Huaguoshan is reference evidence only:
[handoff PR](https://github.com/cogco1/huaguoshan-digital-infrastructure/pull/5).

## Run and read

Install the existing `cad-occt` optional dependencies and an explicit Blender
executable. Blender 4.3.0 on Windows, OCCT binding `cadquery-ocp==7.9.3.1.1` and
Pillow 12.3.0 were exercised. No GPU, Hunyuan model, network or Rhino host is
needed after setup. Other Blender versions require a fresh acceptance run.

For an existing bound project/run/workflow, use the existing runner CLI:

```powershell
python tools/run_project.py --project <project-root> --run <existing-run> --workflow-ref <project-uri> --stage-envelope-ref <project-uri> --export --cad-backend occt --blender-projection "<absolute-path-to-blender-executable>"
```

Python callers supply `RunOptions(blender_projection={"blender_executable": ...,
"timeout_seconds": 120, "presentation": {"resolution": 512, "samples": 16,
"azimuth": -55.0, "elevation": 30.0}})` alongside enabled OCCT export. Invalid
backend combinations are refused. The runtime returns `cad.projection` with
status, receipt URI, artifacts and failures. Projection failure does not rewrite
the verified CAD result or canonical HEAD; the CLI returns failure when a
requested projection fails.

Files live beside the OCCT source in the caller's existing speculative CAD
workspace. With normal project paths this is
`runs/<run-id>/workspaces/cad-<stage-seat>/`. Only the runner writes the
`blender-projection` receipt through P036 RUN_RECORD. This is the same storage
ownership as CAD export, with no new database, job server or project state.
The CLI prints the receipt and artifact names. Read the receipt through
`repository.load_json(record_ref_from_uri(uri, project_id))`; verify retained
bytes with `verify_projection_artifacts(request, source_result, receipt)`.
Reading/verifying a receipt does not rerun Blender.

## Boundary and verification

`execute_blender_projection` accepts the existing exact `CadExecutionRequest`,
successful OCCT `CadExecutionResult`, explicit Blender executable, timeout and
`BlenderPresentation`. It revalidates the native OCCT receipt, binding, semantics
and artifact bytes before reading the STEP. Unknown/missing physical IDs fail.
Coordinates remain source-unit Z-up; cold read uses the request's absolute
readback tolerance and does not relax it for large coordinates. The adapter does not normalize, rescale,
decimate or model geometry.

Source object identity uses `archflow:object_ref`; component/semantic bindings,
material identity and object content digest travel in custom properties. Layer
collections retain `archflow_layer`. Blender display names never establish
identity. Material semantics select a minimal Principled BSDF material; explicit
colors come from the existing request material map. An unassigned material gets
a neutral presentation material without inventing architectural material identity.

One Blender process builds/saves. A second opens the saved scene and reports its
geometry, custom properties and visual settings. A third independently opens
the same saved bytes and renders them. Host-side validation compares the cold
readback with the OCCT-derived plan, checks the saved scene hash did not change,
decodes/verifies the PNG and checks the source bytes again. Timeout, nonzero exit,
missing output or mismatched source/readback never returns successful artifacts.
Logs are retained for each launched stage, including failures. A new attempt
uses a new output stem; previous artifacts are never overwritten.

The receipt binds project, run, canonical base version/hash, branch/epoch, stage,
program record URI/hash, design/program digest, exact STEP hash, Blender version,
resolved presentation settings, observed visual state, object readback and
scene/image byte hashes. No third design identity is introduced.

Repeated full rebuilds must preserve geometry, IDs and resolved presentation.
The tested same-machine CPU runs also match decoded PNG pixels. Blender file
metadata can differ; actual per-run byte hashes are always recorded rather than
assuming cross-version or byte-identical output.

## Presentation and geometry edits

Explicit presentation settings can be reused unchanged when the source revision
changes. Purely visual work may remain in a separately saved Blender scene; it
does not become design truth. V1 does not automatically extract arbitrary manual
shader/camera/light edits from an older blend during a full rebuild. A modified
file also no longer matches the prior certified artifact hash.

Architectural mesh changes are rejected as source mismatches. A later edit
workflow must submit an explicit MonkeyHub patch/reconcile request naming the
project, exact source revision/hash, source object IDs, proposed changes and actor
provenance. MonkeyHub validates/applies it and emits a new source revision before
reprojection. This V1 has no Blender-to-canonical writer or automatic promotion.

## Reproduce the automated architectural demo

```powershell
$env:ARCHFLOW_BLENDER_EXECUTABLE = '<absolute-path-to-blender-executable>'
$env:ARCHFLOW_PROJECTION_DEMO_ROOT = '<absolute-path-to-new-output-directory>'
python -m unittest tests.test_blender_projection_runner -v
python -m unittest tests.test_blender_projection tests.test_blender_cad -v
python tools/archcheck.py
```

Choose an unused demo output directory. With no demo-root variable the test uses
disposable temporary storage. It initializes one P036 `demo` project and runs its
real MonkeyHub column/wall/opening producers and OCCT execution. In `before` the
door is at 6.0 m along the wall; in `after` it is at 6.1 m. Both use the same camera
and lighting parameters. The test reopens the repository, verifies artifact
bytes, changed wall geometry/source identity, stable IDs, unchanged column
geometry and unchanged canonical HEAD. It refuses calls to Rhino and to the
legacy Blender modeling backend.

This is a real geometry/runtime test of an architectural fixture, not an actual
client building acceptance. Existing seat partitioning produces separate wall
and column scenes. Screenshots and the exact run hashes are in
[the published probe](../probes/blender-projection-v1/README.md).

Current limits: no Hub UI button, installed package, multi-seat scene composition,
incremental Blender sync, external assets, animation, bidirectional edit UI or
manual presentation merge. Face-bearing OCCT solids/surfaces are supported;
curve-only STEP shapes fail tessellation explicitly. CPU low-sample renders are
verification previews and can be noisy. Future work should start with a real
whole-project visualization consumer before adding asset or edit frameworks.
