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

Run the explicit probe-rooted framework smoke:

```powershell
python tools/probe_smoke.py probes/test_library --run-id <new-run-id>
```

The case input and every generated state, receipt, workspace artifact, and
manifest stay under `probes/test_library/`. The command is synthetic and does
not claim that a library was architecturally designed or found usable.

Concrete project requests and framework-produced design/run evidence belong
under `probes/<project_id>/`, not in `archflow/` or `tests/`.
P035 currently defines this ownership without writing files. If an output does
not have one named project destination, development stops for an ownership
decision instead of creating an improvised framework path.
