# monkeycad

MonkeyCAD runs ArchFlow geometry programs in CAD kernels and hosts and reads model files: Rhino, OCCT and Blender behind one execution interface. It covers the backend-neutral execution interface, binding and export identity (`execution`, with the backend table in `registry`), the object semantics every saved model must carry (`program`) and incremental patches (`patch`); the backends in `backends/`: `occt` (which also projects named STEP shapes into visible/hidden polylines and cut sections), `rhino` (which translates the program into a script) and `blender` (mesh scene save/readback and projection); 3dm readback and model formats in `formats/` (`three_dm_inspector`, `meshes`, `conversion`, `sketchup_reader`, `three_dm_compose`); and integration packs over local software discovery (`integration_packs`, `discovery`). It depends on the ArchFlow kernel only: the workflows, the Runtime and the Hub call it, and it calls none of them. Drawing SVG/PNG expression belongs to `monkeydiagram.drawing_svg`. Rhino is never the source of truth (ADR-002).

Modules and owners: `docs/architecture/system-map.md` (rendered from `governance/module_registry.json`; one owner per capability). This README says what the package is for; it does not repeat the map.

Current owners: monkeycad.execution, monkeycad.occt, monkeycad.rhino, monkeycad.blender, monkeycad.formats, monkeycad.integrations.

## Install

Install the package beside the kernel; the extras name what a backend needs from pip:

```powershell
python -m pip install -e packages/archflow -e 'packages/monkeycad[occt]'
```

- `occt`: the in-process OCCT binding (`cadquery-ocp`) and rhino3dm for its mesh `.3dm` preview.
- `inspection`: rhino3dm alone, for headless `.3dm` reading.
- `blender`: Pillow, which checks the Blender projection's PNG.

The other backends use software on the machine, found by `discovery` and never installed from here: Rhino 8 with PowerShell on Windows (COM), a Blender executable, and a local SketchUp installation for `SketchUpAPI.dll`. `backends/blender/worker.py` runs inside Blender's own Python; `backends/blender/backend.py` starts it by file name, so the two stay side by side.

## Compiled CAD execution

New callers use `monkeycad.registry` and `monkeycad.execution`. `CadProgramBinding` is the
public neutral binding; `RhinoCadProgramBinding` remains the same class under
its historical import name. Its on-disk `RhinoCadProgramBinding@1` shape stays
unchanged, as do `RhinoCadExecutionReceipt@4` and `OcctExecutionReceipt@1`.

```python
from monkeycad.execution import CadExecutionRequest
from monkeycad.registry import get_cad_backend

backend = get_cad_backend("occt")
request = CadExecutionRequest(
    program=program,
    binding=binding,  # exact program record + project/run/base/branch/stage
    speculative_workspace=workspace,  # existing absolute caller-owned directory
    artifact_stem="candidate",  # portable basename, no extension or subdirectory
    provenance=provenance,
    backend_options={"preview": True},
)
result = backend.execute(request)
result.validate(request, backend.backend_id)
```

`CadExecutionResult` states backend, exact binding, status, named artifacts,
stable ArchFlow object/semantic coverage, readback status and failures.
Artifact names `exact`, `preview` and `model` identify the primary exact
delivery, optional viewing projection and native model respectively. Paths
are relative to the supplied workspace and carry the actual file SHA and
verification status. Native receipts remain in `receipt_payload`; the result
is a plain value, not another retained record. The runner alone persists
native receipts through P036 using the backend's existing `record_kind`.

The optional `CadExecutionSource` is a verified source program/model/digest,
not a project writer. OCCT uses it for exact shape reuse; Rhino uses it as a
patch base. The runner chooses the source from retained P036 evidence. A
source cannot change the current request's project/run/base binding.

OCCT accepts `preview` (default true). Rhino accepts `powershell_executable`,
`timeout_seconds`, `host_wait_seconds` and `patch_oracle`; controlled tests can
supply its existing `runner`, `cleanup_runner`, `monotonic` and `sleeper`
hooks. Blender options and its supported geometry are listed below.
`RunOptions.cad_backend_options` carries these values. Existing
`powershell` and `patch_oracle` arguments remain compatible. Unknown options
and unsupported geometry are explicit failures; no backend selects a
different executor. Invalid binding/workspace requests raise before execution.

Add a backend in `backends/` and at the single `CAD_BACKEND_REGISTRY` table in
`monkeycad.registry`, and declare the same implementation under
`compiled-cad-execution` in `module_registry.json`.
Implement `execute(request)` and the native `read_receipt(request, payload)`
reader, plus `backend_id`, `record_kind`, option validation and `patch_rebuild`
(`False` unless it supports the existing patch/full-rebuild policy). The runner
dispatch algorithm stays unchanged. A new retained record kind, if needed,
must use the existing P036 kind contract; backend code receives no repository.
Common tests live in `tests/integration/test_cad_backend_contract.py`; backend-specific
geometry and host checks remain in the existing CAD suites.

## Rhino host acceptance

The default common suite uses a controlled Rhino worker and inspection. To
exercise the same contract with real geometry, explicitly enable Rhino 8 COM
on a licensed Windows host with PowerShell:

```powershell
$env:ARCHFLOW_RHINO_ACCEPTANCE = '1'
python -m unittest tests.integration.test_cad_backend_contract -v
Remove-Item Env:ARCHFLOW_RHINO_ACCEPTANCE
```

The three opt-in cases use caller-owned temporary workspaces; the conformance
and runner cases also use disposable P036 projects. Each export starts a new
hidden `Rhino.Application.8` host, confirms
its new PID/path/start time before executing geometry, and independently cleans
up only that host. Do not attach the test to an open user document. The native
`.3dm` is cold-read after cleanup; the common binding/artifact/identity checks,
native material assignment, changed layer/material rejection and P036 restart
reuse run against those saved files. An exact restart must reuse the retained
receipt and model without starting another host or changing project `HEAD`.

Without the variable these three cases skip; the controlled Rhino tests still
run. A requested real-host run fails if its host cannot execute; a skip or
controlled fixture is not real-host acceptance. Contributor rehearsal uses
`python -m pytest tools/tests/test_devctl_work.py tools/tests/test_archcheck_scopes.py -v` for
independent simulated lanes, intentional overlap and ordered handoff. A new
teammate must still reproduce the agreed task from their own checkout.

## Blender scene execution

`get_cad_backend("blender")` implements the same request/result contract through
the existing registry. It supports positive-size box `SOLID` operations and
straight `EXTRUSION` of a simple planar polygon, including `base_level` and
`base_offset`, with any non-zero vector outside the profile plane. Other
operations return `unsupported` before launching Blender. This first slice
produces closed meshes; it does not provide full OCCT/Rhino feature parity.

With an existing compiled `program`, exact `binding` and absolute caller-owned
`workspace`, execute a scene as follows. The artifact stem must be unused.

```python
from monkeycad.execution import CadExecutionRequest
from monkeycad.registry import get_cad_backend

backend = get_cad_backend("blender")
request = CadExecutionRequest(
    program=program,
    binding=binding,
    speculative_workspace=workspace,
    artifact_stem="candidate",
    backend_options={"blender_executable": "blender", "timeout_seconds": 120},
)
result = backend.execute(request)
result.validate(request, backend.backend_id)
```

`blender_executable` accepts a command name, string path or `Path`; omission
discovers `blender` on `PATH`. `timeout_seconds` defaults to 120 per process.
The legacy `powershell_executable` option is accepted but unused, as with OCCT;
`patch_oracle` remains Rhino-only. Each execution starts two fresh Blender
processes with `--background --factory-startup --disable-autoexec`: one builds
and saves the scene, the next opens the saved file and inspects binding,
ArchFlow object/semantic identities, units, geometry and materials. Vertex and
bounds checks use the requested absolute tolerance; large coordinates do not
relax it. Face connectivity is checked while allowing equivalent index order
and face winding. Blender float precision that exceeds the tolerance fails
readback instead of silently moving geometry.

The common result exposes a native `model` artifact with format `blend`.
`inspection` is `None`; Blender readback stays in the native
`BlenderExecutionReceipt@1`, retained by the runner as `seat-blender-execution`
through existing project ports. There is no new project state or writer, and
runner dispatch stays unchanged. This slice does not add Hub model display.

An explicit `source` is digest-checked and left untouched; Blender rebuilds the
scene from the current compiled program. The runner can reuse an exact retained
output, but there is no incremental Blender patch. A `.blend` hash identifies
that saved file; fresh saves are not promised to reproduce identical bytes.

Run from the repository root with its development environment. Host tests only
start Blender when `ARCHFLOW_BLENDER_EXECUTABLE` is explicitly set:

```powershell
$env:ARCHFLOW_BLENDER_EXECUTABLE = (Get-Command blender -CommandType Application).Source
python -m unittest tests.integration.test_blender_cad tests.integration.test_cad_backend_contract -v
```

If Blender is not on `PATH`, assign its actual executable path to that variable.
Without it, host tests skip while rejection tests still run; CI without a host
does not establish Blender acceptance. Use the host suite to verify save/cold
readback and the runner's retained receipt, restart/reuse and unchanged project
`HEAD`. New teammate replay remains separate; Rhino has its own opt-in suite
above.
