# M006 — Project workspace boundary cleanup

- Origin: Modify
- Status: Ready
- Depends on: M005

## Goal

Remove repository-level project artifacts and make the boundary unambiguous:
`archflow/` is the reusable tool layer, while every persistent building state,
branch, receipt, and artifact belongs to one data-only project under
`probes/<project_id>/`.

## Write scope

- `.gitignore`
- `.runs/`
- `archflow/runtime/walking_skeleton.py`
- `probes/__init__.py`
- `probes/README.md`
- `tests/test_probe_hierarchy.py`
- `tests/test_walking_skeleton.py`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/planning/P013-v3-readonly-diagnostic-pilot.md`
- `docs/mapping/planning/P018-event-log-and-state-rebuild.md`
- `docs/mapping/planning/P022-operational-design-controller.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- No repository-level `.runs/` project artifact remains or is silently ignored.
- The walking-skeleton CLI has no implicit repository output path.
- `probes/` has no Python package marker and is never imported as framework
  code.
- Future persistent canonical state, event history, branches, receipts, and
  artifacts are explicitly probe-owned.
- P013 and P018 no longer plan repository-level run archives.
- P022 depends on P018 rather than inventing a separate resumability history.
- Boundary tests fail on root-run output, reverse imports, or a restored probe
  package marker.

## Tests

- Walking-skeleton explicit-workspace parser test.
- Probe package, reverse-import, and root-run absence guards.
- Full unit and integration suite.
- Compileall, registry status, generated map, and Markdown links.

## Stop conditions

- Stop if cleanup would remove the synthetic `test_library` project or its
  probe-owned smoke evidence.
- Stop if a historical archived card must be rewritten to hide prior evidence.


## Completion

- Completed: 2026-07-25
- Evidence: Removed root .runs and two obsolete library artifacts; removed probes package marker; CLI now requires explicit workspace; project envelope owns input/canonical/events/runs; P013/P018/P020/P022 scopes and dependencies realigned; 80 tests, compileall, scope, links, literal and residual scans passed.
