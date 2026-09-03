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

## One mechanism in, one mechanism out

Any new mechanism replaces an old one; it does not stand beside it.

1. A change that introduces a canonical record, protocol, resolver or
   vocabulary names what it retires and deletes it in the same change.
2. "Retire" means no production path can reach the old one any more; keeping
   it "for compatibility" without a caller is not retirement, and a symbol is
   not protected because it is exported, documented or asserted by a test.
3. If nothing can be retired, the abstraction is not canonical yet: keep it
   as a candidate beside its one consumer, not in `archflow/`.
4. A test is kept when it proves behaviour a user can observe, geometry,
   persistence and restart, canonical commit, exact-base binding, an external
   side-effect boundary, or the reading of retained data. A test that only
   asserts a key set, a schema string, a round trip, a frozen digest or an
   always-false flag is deleted with the structure it described.
5. Finished work is Git history. The registry holds live work only.
