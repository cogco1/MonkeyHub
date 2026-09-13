# adapters

Adapters: shared interfaces to Rhino and OCCT. They cover CAD program text (`cad_program`), execution and export identity (`cad_execution`, with `occt_backend` also projecting named STEP shapes into visible/hidden polylines), incremental patches (`cad_patch`) and 3dm readback (`three_dm_inspector`). Drawing SVG/PNG expression belongs to `monkeydiagram.drawing_svg`. Rhino is never the source of truth (ADR-002).

Modules and owners: `docs/SYSTEM_MAP.md` (rendered from `governance/module_registry.json`; one owner per capability). This README says what the package is for; it does not repeat the map.

Current owners: adapters.cad_execution, adapters.cad_patch, adapters.cad_program, adapters.three_dm_inspector.

## Compiled CAD execution

New callers use `archflow.adapters.cad_backend`. `CadProgramBinding` is the
public neutral binding; `RhinoCadProgramBinding` remains the same class under
its historical import name. Its on-disk `RhinoCadProgramBinding@1` shape stays
unchanged, as do `RhinoCadExecutionReceipt@4` and `OcctExecutionReceipt@1`.

```python
from archflow.adapters.cad_backend import CadExecutionRequest, get_cad_backend

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
hooks. `RunOptions.cad_backend_options` carries these values. Existing
`powershell` and `patch_oracle` arguments remain compatible. Unknown options
and unsupported geometry are explicit failures; neither backend selects a
different executor. Invalid binding/workspace requests raise before execution.

Add a backend at the single `CAD_BACKEND_REGISTRY` table and declare the same
implementation under `compiled-cad-execution` in `module_registry.json`.
Implement `execute(request)` and the native `read_receipt(request, payload)`
reader, plus `backend_id`, `record_kind`, option validation and `patch_rebuild`
(`False` unless it supports the existing patch/full-rebuild policy). The runner
dispatch algorithm stays unchanged. A new retained record kind, if needed,
must use the existing P036 kind contract; backend code receives no repository.
Common tests live in `tests/test_cad_backend_contract.py`; backend-specific
geometry and host checks remain in the existing CAD suites.

For #13, this contract is Phase 0. Production Blender starts only from a main
commit containing its merged PR. The first Blender lane should save/read one
scene, preserve ArchFlow identities in object metadata and return its native
evidence through this result. Multi-contributor tooling and the actual Blender
lane follow in Phases 1 and 2; they are not implemented by this contract PR.
