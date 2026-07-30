# P045 — Executable architecture firewall

- Origin: Planning
- Status: Done
- Depends on: M006, P036, M013

## Goal

Compile the stable V4 architecture invariants into one fast mandatory check
that rejects framework contamination, reverse dependencies, unowned writes,
duplicate authorities, and hard/soft/single-writer boundary collapse.

## Write scope

- `tools/archcheck.py`
- `tools/devctl.py`
- `tests/test_archcheck.py`
- `tests/test_devctl.py`
- `governance/architecture_policy.json`
- `governance/work_registry.json`
- `docs/mapping/`

## Acceptance

- The check is fast enough to run before every protected completion.
- `archflow/` cannot import or derive authority from `probes/`.
- Project answers and fixed expert schedules cannot enter framework generation
  paths.
- Persistent writes route through the P036 repository or an explicit
  speculative adapter workspace.
- Duplicate event, checkpoint, canonical, or commit authorities fail closed.
- Hard usability, read-only aesthetics, and single-writer promotion stay
  separate.
- Narrow technical exemptions are explicit, typed, and reviewed.
- M013 verification includes the firewall receipt.

## Stop conditions

- Stop if the scan treats every numeric technical constant as a project answer.
- Stop if an exemption can disable an entire rule family.
- Stop if the firewall becomes another source of project semantics.

## Current evidence

- `tools/archcheck.py` checks 73 production Python files in about 0.4 seconds.
- Policy-owned exact write sites distinguish P036 project persistence,
  speculative adapter workspaces, and two named legacy explicit-path debts.
- Synthetic Red fixtures prove reverse probe imports, project literals,
  unowned writes, duplicate state authorities, executable probes, root run
  stores, hard/soft import collapse, and commit-time aesthetic ranking fail.
- `devctl verify` now inserts the architecture check for every work card and
  avoids duplicate execution when a card already declares it.
- Focused architecture, project boundary, probe boundary, aesthetic, and
  development-control tests pass.


## Completion

- Completed: 2026-07-26
- Evidence: Mandatory Architecture Firewall passes across 73 production Python files in about 0.4 seconds and is automatically inserted into every devctl verification.
- Evidence: Synthetic Red tests reject reverse probe imports, instance-answer literals, unowned writers, duplicate state authorities, executable probes, root run stores, hard-soft import collapse, and commit-time aesthetic ranking; 31 focused tests and compileall passed.
