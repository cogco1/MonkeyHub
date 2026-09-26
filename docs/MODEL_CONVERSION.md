# Conversational model conversion (#256)

Implementation plan: extend `studio.artifacts` with a conversion coordinator and
format adapters; use the existing `JobRegistry` and P036 object/run-record ports.
Extend Hub's bound `studio_request` flow for model attachments and downloads.
Never change HEAD or treat an uploaded file as the current project state.

## Capability matrix

| Source | Target | Capability |
|---|---|---|
| 3DM | GLB | Bounded, lossy mesh conversion; requires rhino3dm. Exact surfaces without render meshes are refused. |
| GLB | 3DM | Bounded triangle-mesh conversion; requires rhino3dm. No reconstruction of CAD solids. |
| 3DM | SKP | 当前没有配置可用的执行器 (`blocked-runtime`); native bridge/validator not configured. |
| 3DM | DWG | 当前没有配置可用的执行器 (`blocked-runtime`); native bridge/validator not configured. |
| GLB | SKP | 当前没有配置可用的执行器 (`blocked-runtime`); native bridge/validator not configured. |
| GLB | DWG | 当前没有配置可用的执行器 (`blocked-runtime`); native bridge/validator not configured. |
| SKP | 3DM | 当前没有配置可用的执行器 (`blocked-runtime`); native bridge/validator not configured. |
| SKP | GLB | 当前没有配置可用的执行器 (`blocked-runtime`); native bridge/validator not configured. |
| SKP | DWG | 当前没有配置可用的执行器 (`blocked-runtime`); native bridge/validator not configured. |
| DWG | 3DM | 当前没有配置可用的执行器 (`blocked-runtime`); native bridge/validator not configured. |
| DWG | GLB | 当前没有配置可用的执行器 (`blocked-runtime`); native bridge/validator not configured. |
| DWG | SKP | 当前没有配置可用的执行器 (`blocked-runtime`); native bridge/validator not configured. |

This is not general CAD interchange. Unsupported entities, animation, compression,
and other unsupported GLB features are refused rather than silently discarded.
Supported mesh conversions report loss of materials, textures, hierarchy and CAD
semantics. GLB uses meters/Y-up; the adapter's common mesh uses meters/Z-up.
Same-format 3DM/GLB requests validate and return original bytes without conversion.
SKP/DWG same-format requests are also refused until a real validator exists.

SKP ownership remains [#53](https://github.com/cogco1/MonkeyHub/issues/53).
Its local C API spike is not a distributable product adapter. No SketchUp DLL is
copied or loaded by this feature. DXF support does not establish DWG support.

## Provider contract and selection

`ConversionProvider` supplies name/version, declared input/output formats,
execution mode (local desktop, headless, cloud, SDK), per-route availability and
verification/reason, `convert()` and `validate()`. The coordinator requires both
available and verified, prioritizes local application executors, then in-process,
SDK and cloud implementations. Execution failure is recorded without silently
retrying elsewhere. Future SDK/ODA/RealDWG/APS providers can be explicitly injected;
there is no plugin loader, additional queue or settings database.

SketchUp, AutoCAD and Core Console currently have **discovery-only** providers.
Read-only known-directory/App Paths probes inspect the Runtime host, which must
be the user's machine for desktop execution; remote deployments do not probe a
browser user's computer. Executable presence and directory version hints never
establish compatibility, entitlement, session readiness or conversion support.
Only the mesh provider currently declares implemented routes. Native absence,
incompatibility or missing automation all return 当前没有配置可用的执行器.
See [vendor investigation and Kevin's local experiments](MODEL_PROVIDER_RESEARCH.md).

Reports retain actual provider/version/mode/host, candidate availability reasons,
source/target, intermediate formats, units/layer/material/structure/geometry
losses and validation checks. `sourceArtifact` is the preserved original;
`outputArtifact` is the delivery; `nativeOutputArtifact` identifies a successful
SKP/DWG delivery if a future executor qualifies. `previewArtifacts` is separate
and currently empty. No preview generator or universal GLB preview is added.
2D DWG keeps a 2D PDF/SVG/raster preview policy. Unknown DWG dimensionality is
never evidence of 3D; providers must validate dimensionality before success.

## Chat and backend contract

Attach a small mesh `.3dm` or a self-contained triangle `.glb` and say
“Convert the uploaded file to GLB” or “Convert this upload to 3DM”. The chat
agent selects an exact attachment ID and calls `studio_request` with
`POST /api/exports`, `{targetFormat, attachmentId}`. Hub transfers the original
session attachment to the bound runtime without putting base64 into model context.
The backend stores the original source even when conversion fails.

For “Export the current project as GLB”, the agent reads the current project
context and supplies `projectRevision: {runId, stateDigest, assetSha256}` instead.
A complete, exact retained 3DM projection is required; unavailable projections
are refused, never substituted with an upload or a single partial seat export.
If “this model” could identify either source, the agent asks which one.

Runtime `POST /api/exports` accepts exactly one of `projectRevision`,
`sourceArtifactId` (the SHA-256 of an original retained conversion source), or
`upload: {fileName, contentBase64, attachmentId?}`. Hub's existing operation
admission supplies idempotency; direct Runtime POSTs create new jobs.
The response has `jobId`, `exportId`, and `statusPath`. Read `statusPath` until
success/failure; the chat tool supplies a `downloadUrl` only after success, and
the agent returns that link and loss warnings in the conversation. No UI picker
or export button is involved. Model interpretation uses the existing conversation
agent, not a second language model or a regex command parser.

Jobs share the existing worker pool and publish `export.*` lifecycle events.
P036 owns original/output objects and immutable `StudioModelExport@1` lifecycle
records in `runs/export-<id>/records`; the manifest includes source identity,
revision, attachment, formats, converter versions, units, measured counts/bounds,
loss warnings, output hash and failure reason. HEAD and design records are unchanged.
Completed reports/downloads survive restart. A queued/running job from a stopped
process is reported interrupted and is never silently restarted.

Current geometry scope: static indexed/non-indexed GLB triangle primitives with
identity node transforms; uncompressed buffers embedded in one GLB; 3DM meshes
or BREPs with saved render meshes. Other entities and transformed/deformed GLB
nodes fail explicitly. Geometry is exported in meters with the appropriate axis
conversion. A written 3DM is ready for shaded display: each edge-connected patch
is wound consistently and closed shells face outward; valid source vertex
normals are kept and missing or contradicting ones are computed from the
triangles; a GLB base colour material and vertex colours are kept, and a mesh
without a material receives the neutral `MonkeyHub neutral` material instead of
the black a new 3DM layer draws with. The job's `warnings` and `display` record
say what was kept, computed, reversed or defaulted, and output validation
reopens the 3DM to check normals and material. Textures, UVs and PBR maps are
not transferred, and the GLB writer carries no normals or materials. These
routes do not establish complete SketchUp, Rhino or DWG round-trip fidelity.

## Verification

Generated fixtures include an offset triangle, millimeter 3DM, and curved 3DM
without a render mesh (expected refusal). Python unittest coverage is in
`tests/test_model_formats.py`, the Runtime's `tests/test_model_exports.py`, and
Hub's `tests/test_model_export_chat.py`. The native rhino3dm reader reopens 3DM;
the optional `tests/model_export_glb_reader.mjs` reopens the generated triangle
with the frontend's independent Three.js GLTFLoader and asserts mesh/triangle
counts and Y-up meter bounds. Invoke it with the installed Three.js package
directory and generated GLB path. No SKP/DWG fixtures are fabricated.

This change has automated agent-tool routing coverage, not a live model-provider
conversation acceptance run. Tests exercised rhino3dm 8.32.2 on this workstation;
the repository's pinned 8.32.1 runtime was not independently exercised here.

Original PR baseline (83 tests): 71 Runtime export/job/event/artifact tests, 7 format
tests, and 5 Hub conversion-tool tests passed; Three.js readback, `archcheck.py`
and `git diff --check` passed. The broader 82-test Hub chat suite had 4 failures:
`test_an_interrupted_tool_call_does_not_stay_pending`,
`test_cross_project_chats_run_together_and_stop_is_scoped`,
`test_preparing_the_context_is_taken_out_of_the_cli_turns_own_limit`, and
`test_running_chat_cannot_be_archived_or_cancelled_by_archiving`.
All four reproduced when the test process loaded `chat.py` from unchanged HEAD;
they concern fake-CLI stopping/timeouts, not the new conversion routes.

Provider architecture follow-up: **107 related tests passed** (75 Runtime,
27 format/provider/discovery, 5 Hub conversion-tool). Independent Three.js
readback and architecture checks passed again. Native selection tests use
explicit test doubles and do not certify SketchUp/AutoCAD execution. The four
broader chat failures above were established in the original PR baseline, not
re-run for this follow-up. Live model-provider conversation remains unverified.
