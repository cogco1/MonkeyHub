# ArchFlow V4

ArchFlow V4 explores **bounded architectural agency**: one primary Architect
Agent may design freely with MCP, CLI, and discoverable capabilities inside an
isolated workspace, while only validated submissions can advance canonical
state.

Start here:

- [Dynamic map](docs/DYNAMIC_MAP.md) — current modules, status, next bounded
  transition, and change rules.
- [Architecture](docs/ARCHITECTURE.md) — the canonical/working-state boundary
  and controlled-promotion model.
- Architecture diagram —
  [Chinese](docs/diagrams/v4-bounded-agency.svg) ·
  [English](docs/diagrams/v4-bounded-agency.en.svg): phase-gated expert
  reasoning, bounded Architect agency, controlled promotion, and dual
  evaluation.
- [Building probes](probes/README.md) — concrete building derivations and
  evidence that never acquire framework generation authority.
- [Project document boundary](archflow/project/README.md) — project identity,
  layout, logical references, and the fail-closed persistence ports.
- [External runtime configuration](config/README.md) — explicit workspace,
  cache, and temp roots for active projects outside the checkout.

Current phase: **P3 dual-state architecture realignment**. The repository
proves the bounded control skeleton, one Minecraft MCP adapter, read-only voxel
observation, minimal deterministic usability gates, dynamic expert discovery,
soft multi-objective evaluation, and bounded repair. Raw-brief program
derivation, a real model-backed Architect, durable cross-process state, and an
accepted prompt-to-usable building remain planned or blocked.

Run the deterministic proof:

```powershell
python -m unittest discover -s tests -v
```

Install the optional OpenNURBS reader and build a Rhino-free Pantheon progress
snapshot:

```powershell
py -3.12 -m pip install -e ".[cad-inspection]"
py -3.12 tools/build_pantheon_progress_snapshot.py --no-persist
```

This reads the retained `.3dm` directly; it does not start Rhino. The snapshot
checks file digest, document units, object/layer counts, user strings, bounding
box, and geometry-program alignment. It is a non-authoritative view and never
turns a candidate or passing local check into an accepted stage.

Run the explicit probe-rooted framework smoke:

```powershell
python tools/probe_smoke.py probes/test_library --run-id <new-run-id>
```

The case input and every generated state, receipt, workspace artifact, and
manifest stay under `probes/test_library/`. The command is synthetic and does
not claim that a library was architecturally designed or found usable.

For an active project, copy the credential-free runtime example and initialize
an external project root:

```powershell
Copy-Item config/runtime.example.json config/runtime.json
archflow-runtime --config config/runtime.json init
archflow-runtime --config config/runtime.json bootstrap-project `
  --project-id my-building `
  --prompt "Design a building from the supplied brief."
```

Active project records then live under the configured
`workspace/projects/<project_id>/`. Explicitly promoted regression or paper
evidence belongs under `probes/<project_id>/`; tests keep disposable output in
temporary directories. All project writes use P036 and the same project
layout, regardless of physical root. If an output does not have one named
destination, development stops for an ownership decision instead of creating
an improvised path.
