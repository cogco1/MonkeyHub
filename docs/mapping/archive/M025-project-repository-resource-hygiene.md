# M025 — Project repository resource hygiene

- Origin: Modify
- Status: Done
- Depends on: P036

## Goal

Close the subprocess streams opened by the cross-process project-repository
test so the health suite is warning-free, and align the deferred P026 card with
the dependency truth already recorded in the registry. This maintenance card
changes no project data, geometry authority, or runtime behavior.

## Acceptance

- The cross-process HEAD-lock test closes every subprocess pipe it opens on
  success and failure paths.
- The focused repository test emits no `ResourceWarning` under an always-on
  warning filter.
- The full suite remains green without the prior unclosed-file warning.
- The P026 work card and registry agree that P026 is deferred until M024 and
  its neutral-geometry prerequisites are complete.

## Write scope

- `tests/test_project_repository.py`
- `docs/mapping/`

## Tests

- Focused cross-process HEAD-lock test with `ResourceWarning` enabled.
- Full unittest discovery.
- Architecture firewall and scope check.

## Stop conditions

- Stop if the warning originates in product runtime rather than the bounded
  test process.
- Stop before changing project-repository locking or commit semantics.
- Stop if dependency alignment would require claiming or implementing P026.


## Completion

- Completed: 2026-07-31
- Evidence: Closed the cross-process project-repository test stdout on every wait path; focused ResourceWarning run, 367-test suite with two explicit external skips, compileall, 99-file architecture firewall, scope check, and machine verification passed; P026 card and registry now truthfully defer completion until M024 and neutral-geometry prerequisites.
