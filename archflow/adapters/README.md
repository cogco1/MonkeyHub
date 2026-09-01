# Adapters

Owns boundary integrations for MCP, CLI, models, storage, and other external
systems. Adapters translate external contracts without deciding the design
workflow or owning canonical state transitions.

Capabilities may reference adapters through the open registry described in
[`docs/DYNAMIC_MAP.md`](../../docs/DYNAMIC_MAP.md).

Current status: the repository includes the deterministic fake voxel boundary,
the explicit Minecraft MCP boundary, bounded CLI retrieval providers, and the
P029 asynchronous JSON-command model boundary. No model command or credential
is enabled by default; real invocation remains an explicit opt-in smoke.

## Minecraft MCP boundary

P2 adds an experimental `MinecraftMcpAdapter`. It deliberately does not treat
successful block placement as architectural acceptance:

- the MCP backend supplies world reads, plan preview, exact-plan execution, a
  captured view, and undo capability;
- the adapter allow-lists the transaction-oriented tools and binds every
  artifact to the workspace's exact base state;
- world mutation is disabled unless explicitly authorized;
- plan, preview, execution, session, and visual evidence are written inside the
  speculative workspace;
- external failure produces a bounded receipt and never writes canonical state.

`voxel_summary.loadable` means that the artifact can be inspected or replayed.
It does **not** mean that the building is usable. Usability remains a separate
hard-validation and evaluation responsibility.

M003 makes the non-atomic world boundary explicit. Before execute, the adapter
writes a hash-linked prepare receipt and an execute-started receipt into the
supplied speculative workspace. An acknowledged execution, observation,
transport-evidence check, compensation attempt, and finalization each append a
phase bound to the same plan, world/dimension, session, base, and workspace.

An execute call with no acknowledgement is `write_unknown`; an acknowledged
write with failed observation is `world_changed_unobserved`. Neither means
unchanged or accepted. Compensation is opt-in and requires both the same world
identity and an exact undo token returned by execution. Even an acknowledged
undo is recorded as compensation rather than an atomic rollback claim.

The adapter still owns no project repository or canonical committer. Its
workspace journal is loaded and archived by the separate recovery runtime;
P005 hard usability, commitment monitoring, aesthetics, player approval, and
canonical promotion remain downstream.

## Pascal Editor execution boundary

`pascal_execution.py` treats Pascal Editor as a replaceable speculative scene
backend, never as design or persistence authority. The adapter accepts only a
`CompiledGeometryProgram` plus its matching successful
`GeometryCompilationReceipt`; a raw model message or uncompiled geometry
proposal cannot reach Pascal through this boundary.

The topology lowerer compiles box-like `solid`, polygon `extrusion`, axial
frustum `revolve`, and equal-ring `loft` operations into Pascal `block` nodes.
Stable block ids and node metadata bind project, run, branch, exact canonical
base, proposal, compiled-program, compilation-receipt, object, stage, and
coordinate mapping. The caller must explicitly select either a right-handed
Z-up source mapping or a right-handed Y-up source mapping into Pascal's
X/Z-ground, Y-up metre frame; source-axis inference is not permitted.

High-level booleans still require a neutral compiled boundary mesh for exact
translation. A caller may explicitly enable the non-accepting open-boolean
preview used for visual prototyping; the plan and receipt then retain a named
translation issue and remain `candidate_evidence_with_issues`. Unsupported
operations otherwise fail before external mutation. Programs are evaluated in
their compiled `operation_order`. During an exact predecessor transition,
existing managed objects with the same object digest are verified and treated
as no-ops; revisions and retirements still require their exact preconditions.

Execution requires an existing saved Pascal scene plus its exact scene version
and graph SHA-256. The adapter checks `get_project_status`, reloads that exact
scene, reads it, prepares one deterministic `apply_patch`, applies at most once,
then reads back the graph and calls `validate_scene`, `verify_scene`, and
`get_project_status`. A stale scene fails before apply. Timeout or transport
failure after apply starts remains `outcome_unknown` and is never replayed
automatically.

Pascal's general layout verification is retained as evidence, not interpreted
as a stage gate. For example, a valid Stage 1 massing block may still be called
an empty level by a wall/room-oriented Pascal check; that produces
`candidate_evidence_with_issues`, not design rejection or acceptance. ArchFlow
stage validators and the human acceptance boundary remain downstream.

`allow_scene_write` is false by default. With writes disabled, `execute()`
performs the exact-base read and returns the prepared patch receipt without
calling `apply_patch`; `preview()` exposes the same read-only preparation. The
adapter writes no files, receives no repository or committer, and every receipt
states that design-acceptance, hard-gate, and canonical-write authority are
false. A caller that retains the receipt must use the generic project ports.

The transport command is explicit and dependency-free from ArchFlow's point of
view. Pin the Pascal source/runtime used by that command. During the initial
integration check, Pascal main commit `67ea40efdf4a03f5897005def56e45189f2c1b69`
and its frozen lockfile passed the relevant upstream patch/save tests; the
independently published `1.0.0-beta.6` npm tree did not provide the same working
Windows runtime and must not be treated as equivalent evidence.

## Read-only voxel observation

`VoxelObservationExtractor` consumes a saved, workspace-owned `VoxelScan@1`
without receiving an MCP client, state store, or committer. It binds the exact
artifact digest, workspace, and base state, then deterministically reports the
architectural envelope, occupied and walkable cells, openings, connected
regions, face-adjacency support relations, unsupported components, and bounded
unknown cells.

This observation is evidence for later validators and experts. It is not a
building approval, structural analysis, or world-edit capability.

`minecraft_volume.py` supplies the live read boundary that P004 intentionally
did not invent. It requests one absolute inclusive volume from a selected
provider, limits its size, and requires every coordinate exactly once. Unloaded
chunks remain explicit unknown cells; the adapter never asks the provider to
load them. A response with drifted bounds, missing or duplicate coordinates,
invalid cell kinds, or inconsistent completeness counts is rejected before
deterministic `VoxelScan@1` bytes are returned. The adapter does not write them;
the caller must pass those bytes to the project persistence boundary or, in an
explicit live test, its disposable workspace. The adapter has no build,
command, undo, save, commit, or canonical state authority.

The resulting observation may run the same deterministic P030 route evaluator
as comparison evidence. It cannot instantiate P030's primary
`ScenarioObservationBinding@1`, which requires an exact deterministic sandbox
realization receipt. A successful external comparison therefore carries no
hard-validation or accepted-building authority.

## Building-scoped CLI retrieval

`cli_retrieval.py` calls one explicitly configured local command with
`shell=False`. The JSON query binds project, run, exact canonical base, query
text, and prior evidence. The receipt retains provider id, declared version,
command, fingerprint, bounded-output digest, and cited excerpts.

Provider output is accepted only as project-scoped hypothesis evidence.
Timeout, unavailable executable, non-zero exit, malformed schema, and
oversized output produce named failure receipts. The adapter never chooses
another provider, writes design/canonical state, or invokes a world tool.
Provider commands must not embed credentials or upload private project data.

## Asynchronous model provider

`model_provider.py` invokes one explicitly configured model command with an
immutable JSON request, no shell parsing, and no fallback. The request contains
one bounded `ContextSlice`, dynamic capability ids, or detached expert
receipts; it excludes raw history, canonical writers, workspace paths, and
world handles.

The command adapts its real model surface to `ArchFlowModelOutput@1`. Exact
request and phase identity, input/output bytes, provider-reported token use,
timeout, exit status, and output digest become a reloadable receipt. Oversized,
malformed, unavailable, timed-out, or budget-exhausted calls return typed
failure evidence without advancing design state.

`create_codex_cli_model_provider()` is the temporary concrete Agent CLI route.
It runs `codex exec` as an ephemeral, read-only, non-interactive JSONL process
inside a fresh temporary directory. User configuration and exec rules are not
loaded, the prompt forbids tools and file inspection, and trusted token usage
comes from the CLI completion event rather than model-authored JSON. The final
agent message remains a detached proposal and cannot receive a project writer.

The runtime continues to depend only on `AsyncModelProvider`. A future API
adapter implements the same `invoke(request) -> receipt` protocol; replacing
the CLI therefore does not change Primary Architect acceptance or commit logic.

The adapter owns no prompt interpretation, capability choice, design
acceptance, persistence, canonical commit, or world mutation. A live provider
is tested only when `ARCHFLOW_MODEL_SMOKE_COMMAND_JSON` explicitly names an
authorized command whose stdin/stdout follow this JSON contract.

## Quarantined V3 capability CLI

`v3_legacy_cli.py` exposes one explicitly configured V3 provider and one
capability per subprocess command. It is a process boundary, not a compatibility
import: the request contains only an immutable exact-base JSON snapshot, one
current obligation, and evidence references.

The response must repeat the request, provider, capability, base, and frozen V3
fingerprints exactly. The receipt records duration and output digest while
explicitly denying fallback, persistence, canonical-write, and live-world
authority. Missing commands, timeouts, non-zero exits, schema or identity drift,
and oversized input/output remain named failures. The caller must make a new
Architect decision to select any different provider.

The bridge rejects the generic V3 CLI, `archflow.examples`, Pack registration,
`compose_building`, `decision.v2_stages`, and the legacy Pack adapter at
configuration time. P012 connects no real V3 provider; P013 must prove that its
dedicated read-only entrypoint does not transitively load those surfaces.

## Web evidence snapshots (P067)

`web_evidence.py` retains one fetched page as a content-addressed
snapshot: URL, retrieval time, raw and extracted-text digests, bounded
size. Snapshots carry no adoption or design authority and are never fed
raw into a provider prompt; only quoted facts promoted by a typed
adoption record reach generation.
