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

For ordinary design revisions, give a reversible visual candidate before asking the
architect to approve implementation parameters. The agent owns interpreting the request,
consulting relevant sources when needed, choosing a modeling method, inspecting the
result and correcting technical mistakes. Existing adapters own mechanical field mapping,
units and reference normalization; ask people about genuine design choices, not internal
ids or schemas. An unsupported tool operation is a system limitation, not missing user
information. Keep precise values and diagnostics available on demand. Preserve exact-base
binding, stated keep conditions, recovery and independent formal issue. The target loop and
its currently implemented limits are described in `docs/ARCHITECTURE.md`.

## Framework versus project data

- `archflow/` contains shared project, fact and technical contracts. `monkeyarch/`
  owns 3D modeling algorithms; `monkeydiagram/` owns drawing algorithms.
  `monkeymonitor/` owns independent engineering usage, pricing and budget advice;
  its explicitly configured diagnostic logs are not project state. The Studio
  host composes peer workspaces. Core code imports neither workflow, and the
  workflows do not import one another; see `docs/REPO_LAYOUT.md`.
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

## Rule sources and independent modules

- `AGENTS.md` states durable working rules. `governance/module_registry.json` names
  each capability's owning module and public contract; an owner is not a person or a
  single-file limit. Module `canonical` denotes established ownership, not a released
  application or issued project. `governance/work_registry.json` holds current work and source-edit
  scopes. `governance/architecture_policy.json` holds the checks enforced by archcheck.
  Generated maps are views of these sources, not separate rules or runtime switches.
- Work is `active` while progressing, `ready` when its prerequisites are met, and
  `blocked` while awaiting a concrete input or decision, including an explicit pause.
  Record the reason in the existing card. Reuse that card for related fixes; remove
  the card and registry entry only when its remaining acceptance is complete.
- A new domain capability may live in its own module and reach Studio through an
  existing application API or adapter. Extend the owning module's contract when needed;
  do not move independent implementation into core merely to make it callable. Change
  state, compilation or project persistence only when the behavior needs that contract.
  Registered ownership and reserved protocols do not implement automatic plugin loading
  or grant execution or project-write authority. The concrete integration path is in
  `docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md`.

## Before implementing anything

MonkeyHub is the single application entry; MonkeyArch is its modeling workspace. Startup and agent access belong to the same Hub flow.
For a first session, follow section 0 of [the Hub entry guide](docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md#0-monkeyhub-统一入口).
Reuse its source/project locations and bounded lookup sequence; do not load every linked document or search the whole tree to orient yourself.

The repository has one production spine (`docs/ARCHITECTURE.md`) and a module contract
registry (`governance/module_registry.json`, rendered as `docs/SYSTEM_MAP.md`). Every
capability has exactly one owner there. `docs/CANONICAL_SPINE.md` records the earlier
consolidation decision, not a migration to rerun. Before writing code:

1. Find the owner with `python tools/devctl.py module <id-or-keywords>` (for example,
   `module wall`), then query the exact module id. It returns the registered contract,
   dependencies, source paths and tests without dumping the whole registry or system map.
2. Read what that module `owns`, `does_not_own`, and its contract (`inputs`, `outputs`,
   `public_api`, `invariants`). Output is paged: follow any omitted-entry notice with
   `--section <name> --offset <n>`; use `--json` for structured output. Read the matching
   `docs/SYSTEM_MAP.md` entry only when additional map context is needed.
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
