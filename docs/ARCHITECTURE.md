# ArchFlow V4 architecture

One production spine. Who owns what is in [SYSTEM_MAP.md](SYSTEM_MAP.md) (generated from
`governance/module_registry.json`); why it is one spine is in
[CANONICAL_SPINE.md](CANONICAL_SPINE.md); the decisions a later session would be tempted to
reverse are in [adr/](adr/README.md). Live work is in [DYNAMIC_MAP.md](DYNAMIC_MAP.md).

## The spine

```text
StateRecord@1                     archflow/state/state_record.py      the design, content-addressed
  │ developed_design_view          the bound projection (run, base) — binding identity
  ▼
element producers                 archflow/capabilities/element_producers.py
  │ reference_resolver             levels, grids, datums, derivations resolve here
  ▼
GeometryProgramProposal           archflow/state/geometry_program.py
  │ compile_geometry_program       archflow/compilers/geometry.py — the one compiler
  ▼
CAD                               archflow/adapters/cad_program.py → cad_execution.py → cad_patch.py
  │ three_dm_inspector             readback is evidence, never intent
  ▼
relation checks                   archflow/capabilities/relation_checks.py — plain domain values
  ▼
stage workflow                    archflow/state/stage_workflow.py + StageExecutionGuard (project_runner)
  ▼
P036 repository                   archflow/project/repository.py — one HEAD, compare-and-swap, content-addressed records
```

Entry points: `tools/run_project.py` (a run of one project), `tools/verify_state_record.py`
(replay equivalence), `tools/freeze_project_stage_workflow.py`, and the Studio
(`apps/archflow-studio`: FastAPI `api/` as a thin shell over the kernel, `web/` as the client).

Shared foundations: `archflow/project/refs.py` (the four references), `archflow/contracts/`
(canonical JSON, digests, field parsing), `archflow/validation/{model,engine}.py`
(`validate_submission`, `Finding`, `ValidationReceipt`), `archflow/ports/model.py` (the one
model invocation request and receipt; whoever crosses the boundary signs one).

## Rules that hold the shape

- **Two identities.** `StateRecord.digest` is content; `state_digest` is binding (ADR-003).
- **Values, not receipts.** A deterministic in-process transformation returns a plain value; a
  receipt exists only where a write, an external call, an irreversible commit or an explicit
  acceptance crossed a boundary (AGENTS.md).
- **One owner per capability.** The registry names it; `archcheck` fails a second definition,
  a copied helper body, a missing public symbol or a missing test.
- **The spine never imports `archive/`.** Archived lanes run from the archive and return only as
  folds (CANONICAL_SPINE.md §3).
- **Layer imports and write sites** are fenced by `governance/architecture_policy.json`.

## What is not on the spine

`archive/` holds the agent-portfolio lane, the Pantheon and Parthenon tools, the design
controller and its loops, the sandbox/voxel lane, the research and experiment lane and the v3
diagnostic, each runnable from there with its own tests. Retained data under `probes/` and the
external workspace never moved.
