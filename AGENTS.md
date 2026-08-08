# ArchFlow V4 project rules

These rules are specific to `D:\ARCHFLOW_V4` and supplement the global Codex
instructions.

## Framework versus project data

- `archflow/` contains reusable mechanisms only.
- `probes/<project_id>/` contains explicitly promoted, committed building
  inputs and framework-produced evidence used for regression or publication.
- Active, unpromoted projects may use an explicitly configured external
  `workspace/projects/<project_id>/` root. They keep the exact same P036
  project envelope and never acquire a second persistence authority.
- `tests/` may create disposable temporary output, but committed building-run
  evidence belongs to a probe.
- `docs/` contains human-facing documentation and governance evidence, not
  live project state.

## Persistence authority

- New domain, compiler, capability, validation, evaluation, and controller
  modules return typed values or receipts; they do not choose filesystem paths.
- Persistent project writes, whether external or in a promoted probe, must go
  through the generic `archflow.project` ports implemented by P036.
- Adapters may write only inside an explicitly supplied speculative workspace.
- Never restore a repository-level `.runs/` default or persist an absolute
  machine path as the stable identity of a project artifact.
- Cache and temp roots are external and non-canonical. Codex Cloud `/tmp`
  storage is disposable and must never be described as durable evidence.
- `project.json` owns immutable identity and format metadata. Mutable canonical
  position belongs to `HEAD`; candidate work belongs under its named run.

If a new artifact, receipt, state, trace, cache, screenshot, export, or recovery
record does not have one unambiguous destination in the project layout, stop
before writing it and ask Kevin to decide its ownership. Do not temporarily put
it in `archflow/`, `docs/`, `tests/`, the repository root, or an improvised
folder.
