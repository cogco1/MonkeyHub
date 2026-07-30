# P046 — Bounded Agent context capsule

- Origin: Planning
- Status: Done
- Depends on: P022, M013, P045

## Goal

Generate a deterministic, read-only work-card context capsule so an Agent can
act from the current responsibility, contracts, evidence, and tests without
ingesting the full repository or unrelated project history.

## Write scope

- `tools/devctl.py`
- `tests/test_devctl.py`
- `governance/work_registry.json`
- `docs/mapping/`

## Acceptance

- `devctl context <ID>` emits the current goal, dependencies, actor, scope,
  acceptance, tests, stop conditions, and named relevant files.
- Every included source excerpt is bounded and content-digested.
- Planning claims and verified repository evidence are visibly distinct.
- Raw history, unrelated cards, probe data, secrets, and sibling work are
  excluded.
- Context generation is deterministic and read-only.
- The capsule cannot claim, verify, complete, or write project state.

## Context-file contract

- The current work card and repository `AGENTS.md` are the only automatic
  sources.
- Additional excerpts must be named explicitly in the registry through
  `context_files` with a path, purpose, and bounded line range.
- Explicit probe, history, log, session, secret, escaping, missing, non-UTF-8,
  or oversized sources fail closed. Other work-card files cannot be included
  as an explicit excerpt.
- Dependency cards contribute only ID, status, and stream metadata; their
  prose and history are not copied into the capsule.
- Output is normalized JSON with full-file and excerpt digests plus a digest
  of the capsule itself. No timestamp, absolute path, transcript, test log, or
  mutable latest pointer enters the output.

## Current evidence

- `python tools/devctl.py context P046` emits the bounded contract without
  acquiring the registry lock or modifying the registry and generated map.
- Planning claims and completion/verification evidence occupy distinct,
  explicitly labeled objects.
- The output carries no claim, modification, verification, completion,
  project-state, or evidence authority.
- Unit coverage proves deterministic output, bounded excerpts, safe-source
  rejection, machine-verification labeling without captured logs, and
  read-only behavior.
- Completion now deterministically moves any completed card from architecture
  `open_cards` to `evidence_cards`, preventing the generated dynamic map from
  retaining a finished dependency as an open gap.

## Stop conditions

- Stop if context generation recursively scans the repository.
- Stop if probe content or unrelated card history enters the capsule.
- Stop if a generated capsule becomes a new state or evidence authority.


## Completion

- Completed: 2026-07-26
- Evidence: devctl context emits a deterministic content-digested read-only capsule with planning/evidence labels, bounded explicit excerpts, safe-source rejection, dependency metadata only, no project authority, and no registry lock or write; completion also synchronizes done architecture cards from open gaps into evidence. 232 tests passed with 1 external smoke skipped; architecture firewall and compileall passed.
