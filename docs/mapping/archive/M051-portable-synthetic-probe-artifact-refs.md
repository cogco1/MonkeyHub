# M051 — Portable synthetic probe artifact references

- Origin: Modify
- Status: Ready
- Depends on: M006, P036, M050

## Goal

Remove machine-absolute artifact identities from the retained synthetic
`test_library` probe while keeping its compatibility-only FakeVoxel execution
outside the production chain.

## Write scope

- `tools/probe_smoke.py`
- `tests/test_probe_hierarchy.py`
- `probes/test_library/runs/framework-smoke-002/`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- Persisted synthetic smoke records use project-relative logical artifact
  references and contain no drive letter or machine-absolute path.
- The compatibility adapter may use a local file URI only inside its supplied
  speculative workspace before record serialization.
- Physical containment is checked before a local artifact reference is
  converted to a portable project reference.
- New compatibility runs use the probe project identity instead of the retired
  pre-M007 `project:run` pseudo-identity.
- Relocating a clean repository snapshot does not invalidate the retained
  probe hierarchy test.
- The repair does not restore FakeVoxel, the synthetic smoke, or direct file
  references to the production runtime.

## Tests

- Retained probe digest, containment, and logical-reference validation.
- Full discovery in a relocated clean snapshot with opt-in live model smoke
  disabled explicitly.
- Architecture firewall, V3 boundary, scope, and diff checks.

## Stop conditions

- Stop if the repair requires a second project persistence authority.
- Stop before treating the synthetic smoke as architectural usability or
  production evidence.


## Completion

- Completed: 2026-08-16
- Evidence: Retained synthetic evidence now stores project-relative logical artifact URIs only; new compatibility runs validate workspace containment, serialize portable project identity, and no longer use the retired project:run pseudo-identity. Focused 6 tests and M051 verification passed; relocated clean-snapshot discovery passed 532 tests with 3 explicit opt-in skips; ARCHITECTURE PASS 117 files, V3 boundary PASS 117 production files and 2 frozen oracles, scope PASS 12 paths, and diff check passed.
