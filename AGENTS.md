# ArchFlow V4 project rules

These rules are specific to `D:\ARCHFLOW_V4` and supplement the global Codex
instructions.

## Long-term direction

`docs/VISION.md` defines the direction: remove work imposed by tools so architects
can develop, inspect and revise ideas more directly with AI. Start from a real
user action and identify what they should no longer have to explain, transfer or
repair. Direct modeling can itself be design thinking; do not count every human
action as waste. Existing tools and ArchFlow already propagate defined dependencies.
Tests, gates, hashes and module counts are implementation checks, not product progress.
Editable-object counts likewise do not establish architectural usability. Work on a whole
building task: its necessary functions and relationships, the conditions governing a change,
and whether the result can be revised and handed over. Following declared dependencies does
not prove those dependencies are complete or architecturally correct.

MonkeyArch tests representation and interaction. An independent application or
geometry engine needs a demonstrated reason. Extend existing owners for concrete
needs, not one schema or module per vision term. Keep human judgments attributable
and distinguish continuing a candidate, endorsing a direction and formally issuing
a project version. Live development is indexed by `governance/work_registry.json`;
the vision and `docs/RESEARCH_POSITIONING.md` do not themselves authorize new mechanisms.
Deliver one usable change, then use its results to choose the next. Do not make a
local repair wait for a full research comparison, another building or formal issue.

## Framework versus project data

- `archflow/` contains reusable mechanisms only.
- `probes/<project_id>/` contains explicitly promoted, committed building
  inputs and framework-produced evidence used for regression or publication.
- Active, unpromoted projects may use an explicitly configured external
  `workspace/projects/<project_id>/` root. They keep the exact same P036
  project envelope and never acquire a second persistence authority.
- `tests/` may create disposable temporary output, but committed building-run
  evidence belongs to a probe.
- `docs/` contains human-facing documentation, not live project state.

## Values, receipts and authority

- A deterministic in-process transformation returns a plain domain value: a
  dataclass, a tuple, a finding list. It does not mint a receipt, a digest of
  itself, or a schema string.
- A receipt exists only where something crossed a boundary that a value cannot
  prove on its own: a persistent write, an external call, an irreversible
  commit, a non-deterministic output, or an explicit acceptance decision. The
  receipt names what crossed and what it was bound to; nothing else.
- Authority is expressed by capability, not by flags. An object that cannot
  write has no writer. Explicit authority data appears only on objects that
  cross a trust boundary (a grant, a token, a lock). Existing record kinds
  still serialise their historical `*_authority: false` block because the
  digests retained data binds were computed over it; that block is written by
  `no_authority()` and never re-checked on read, and a new record kind does
  not add one.
- Two identities, never three: content identity (the design content itself)
  and binding identity (the run, base and project it is executed against).
  Binding a record to another run does not change its content identity.
- Canonical JSON and digests come from `archflow.contracts.canonical`; no
  module carries its own copy.

## Architectural semantics

- A record's semantic fields name ids from `archflow/semantics/` (`role.*`, `condition.*`)
  or an alias that resolves to them; the record refuses anything else and names the nearest
  ids (ADR-006). Entity, role and condition are three things and never one hierarchy.
- A new term goes into the table with a written reason why existing terms cannot be composed
  to say it; a compound phrase is registered only for records that already carry it.

## Persistence authority

- Domain, compiler, capability, validation, evaluation and controller modules
  do not choose filesystem paths.
- Persistent project writes, whether external or in a promoted probe, must go
  through the generic `archflow.project` ports implemented by P036.
- Adapters may write only inside an explicitly supplied speculative workspace.
- Never restore a repository-level `.runs/` default or persist an absolute
  machine path as the stable identity of a project artifact.
- Cache and temp roots are external and non-canonical.
- `project.json` owns immutable identity and format metadata. Mutable canonical
  position belongs to `HEAD`; candidate work belongs under its named run.

If a new artifact, state, trace, cache, screenshot, export or recovery record
does not have one unambiguous destination in the project layout, stop before
writing it and ask Kevin to decide its ownership.

## Before implementing anything

The repository has one production spine (`docs/ARCHITECTURE.md`) and a module contract
registry (`governance/module_registry.json`, rendered as `docs/SYSTEM_MAP.md`). Every
capability has exactly one owner there. `docs/CANONICAL_SPINE.md` records the earlier
consolidation decision, not a migration to rerun. Before writing code:

1. Locate the relevant owner in `docs/SYSTEM_MAP.md`; read that entry, not the whole tree.
2. Find the capability the request needs in the registry: which module `owns` it, what that
   module `does_not_own`, its contract (`inputs`, `outputs`, `public_api`, `invariants`).
3. Check that owner's public API and real callers for an existing implementation.
4. Decide EXTEND (default), REFACTOR, or CREATE. CREATE needs a written reason why no owner
   fits; a second implementation of an owned capability is allowed only behind an interface
   declared in the registry with its implementations listed.
5. Update the registry in the same change only when ownership, the public contract or
   its listed tests actually change. An internal fix needs no ceremonial registry edit.
6. Implement and run the affected behavior tests and `python tools/archcheck.py`.
   For documentation-only changes, check links, generated maps and the scoped diff;
   do not run the application suite. Broader checks need an affected boundary or failure.

Code under `archive/` is not extended and not imported; a lane returns only as a fold onto
the spine.

## Extend behavior, remove superseded paths

Extend the existing owner by default. When replacing a mechanism, remove the
superseded production path in the same change; preserve readers required by
retained data. An export or a test alone does not prove that a path is needed.

A genuinely new requested behavior may have nothing to retire. Implement it
beside its first real consumer; extract a shared abstraction only when real
consumers need it. Do not invent a retirement or a parallel framework to satisfy
a slogan.

Keep tests of observable behavior, geometry, persistence/restart, exact-base,
external effects and retained-data compatibility. Delete assertions that only
freeze unused internal packaging with that packaging; do not delete a boundary
contract merely because its test checks fields or a round trip.

Finished work is Git history. The registry holds live work only. Report the
usable result and remaining limitation; internal checks support that report.
