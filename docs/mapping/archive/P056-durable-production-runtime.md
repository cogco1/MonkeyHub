# P056 — Durable production runtime

- Origin: Planning
- Status: Ready
- Depends on: P055, P052, P053

## Goal

Provide one formal recoverable prompt-to-building runtime and CLI that invokes
the typed semantic-spatial and joint lifecycle path, persists only through
P036, and selects an Agent CLI provider today through an interface that also
admits a later API provider.

## Write scope

- `archflow/project/`
- `archflow/runtime/`
- `archflow/production/`
- `pyproject.toml`
- `tests/`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- The official console entrypoint can start or resume a named project run from
  an explicit runtime root and raw prompt; it no longer stops at bootstrap.
- Every retained input, state, receipt, artifact, and recovery marker has one
  P036-owned destination; no repository `.runs` default or machine path enters
  stable identity.
- Authoring and geometry responsibilities resolve through P053 tokens and
  reject stale provider results before P036 compare-and-swap.
- Agent CLI is a configured provider implementation, not an authority; the
  same typed provider port can host a later API implementation without changing
  domain or persistence schemas.
- Interrupted runs resume from durable exact-base records without replaying an
  already accepted transition or treating temporary storage as evidence.

## Tests

- Scripted-provider prompt-to-sandbox run and interrupted-run recovery.
- Explicit external runtime root, P036 layout, stale CAS, stale provider epoch,
  unavailable provider, and invalid resume rejection.
- Console-entrypoint smoke, architecture firewall, and full unit discovery.

## Stop conditions

- Stop if the CLI creates another repository, checkpoint, or default run root.
- Stop if Agent CLI output can bypass typed parsing, lifecycle compilation, or
  P036 persistence.


## Completion

- Completed: 2026-08-16
- Evidence: Official run-project CLI starts and resumes an explicit P052/P036 project using P053-authorized scripted provider evidence: first run performed four typed model invocations, persisted semantic-spatial selection, developed state, neutral geometry and sandbox receipts, and second run resumed with zero provider replay. Focused production and recovery boundary passed 49 tests; full discovery passed 517 tests with 3 explicit opt-in skips; ARCHITECTURE PASS 118 files; V3 boundary PASS 118 production files and 2 frozen oracles; P056 verification PASS.
