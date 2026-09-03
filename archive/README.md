# Archive

Code that is not on the spine (see `docs/CANONICAL_SPINE.md`). Moved here whole with `git mv` on
2026-09-03 from tag `pre-spine` (c6c97d4), where every lane last ran green on `main`. Relative
paths are preserved: `archflow/control/baseline.py` is now `archive/archflow/control/baseline.py`.
Imports among archived modules read `archive.archflow.…`; imports of spine modules stay
`archflow.…`. Nothing under `archflow/`, `tools/` or `apps/` may import `archive` (archcheck).

Lanes and how to run them from the repository root:

- **portfolio** (agent-driven production, P053): `python -m archive.archflow.project.runtime --config <cfg> run-project`.
  The P053 control plane joined it on 2026-09-03: `archive/archflow/production/responsibility.py`,
  `archive/archflow/production/provider_runtime.py` and the subprocess adapter
  `archive/archflow/adapters/model_provider.py` had no caller on the spine, and a live model now
  enters through the Studio's intent compiler, which signs its call with the same
  `archflow/ports/model.py` receipt and no authority envelope.
- **monuments** (Pantheon P058/P064/P065/P069, Parthenon): `python archive/tools/run_pantheon_reconstruction.py …`,
  `python archive/tools/run_parthenon_reconstruction.py …`, `python archive/tools/build_pantheon_progress_snapshot.py …`
- **research** (experiments P062/P063, web precedent, basis index): `python archive/tools/run_experiment.py …`
- **sandbox** (voxel / Minecraft P026/P027/P030): `archive/archflow/realization`, `archive/archflow/adapters/minecraft_mcp.py`
- **controller** (design controller and its loops, P042–P060): `archive/archflow/runtime/design_controller.py`
- **v3** (P013 diagnostic): `archive/archflow/adapters/v3_legacy_cli.py`

Retained data under `probes/` and the external workspace did not move. Tests of archived code
live in `archive/tests/` and run on demand: `python -m unittest discover -s archive/tests -t .`
(a test that spawns a tool by its old path needs that path updated when it is next needed).

A lane comes back only through a card that lands it on the spine as a fold; nothing here is a
work-card target.
