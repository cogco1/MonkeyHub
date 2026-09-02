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

## One canonical abstraction in, one parallel abstraction out

Every time a canonical abstraction is introduced, a parallel abstraction
that expressed the same thing must be retired in the same change or in the
card that lands it. The repository already carries duplicates that were
never retired, and every one of them became a place where production
bypassed the kernel:

- `OperationalMarkovState` (facts, bindings, locks, obligations,
  dependencies) beside `DevelopedDesignState` (components, development
  dependencies) — two vocabularies for one design state.
- `GeometryProducer` / `ProducedAssembly` beside the runner's own producers
  and the per-project `add_*` functions — three ways to emit operations.
- `ArchitecturalRelation` with propagation rules beside the villa script's
  relation tuples and its 967 lines of hand-coded checks.
- `DependencyEdge.effect` beside `DevelopmentDependency.impact`.
- Levels as `ProjectLevels` beside per-side `{side}-landing-top` datums.

Rules:

1. A card that introduces a canonical record, protocol, resolver, or
   vocabulary names the parallel it retires under a `Retires:` line, and
   its acceptance includes the retirement (deleted, or reduced to an
   adapter that forwards to the canonical one with a typed lineage note).
2. "Retire" means no production path can reach the parallel any more;
   keeping it "for compatibility" without a caller is not retirement.
3. If nothing can be retired, the abstraction is not canonical yet: write
   it as a candidate under the card, not into `archflow/`.
4. Migration steps that must keep both alive prove equivalence between
   them with a recorded receipt (bounds, bindings, digests) before the old
   one is removed; the receipt is the retirement evidence.
5. Reviewers reject a change that adds a second way to say something the
   kernel already says, unless the card shows the first way leaving.

