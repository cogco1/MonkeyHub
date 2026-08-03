# M031 — Sandbox integrity gates

- Origin: Modify
- Status: Active
- Depends on: M004, P048

## Goal

Integrate and independently verify the P026-relevant sandbox integrity repairs
from the Claude audit: derive unsupported occupied cells from persisted support
relations, issue approvals through the existing policy gate with a durable
policy binding, and make reload rejection semantics match execution.

## Acceptance

- Sandbox observations derive floating occupied cells from the grounded support
  graph; no support edge or grounded cell is fabricated.
- Candidate approval is issued only through the existing disposable automation
  approval gate and binds a persisted `BuildPolicy@1` record by URI and digest.
- Reload verifies the policy window and binding and rejects inconsistent or
  fabricated approval receipts.
- A concept is classified as rejected when the recomputed non-empty finding-code
  set matches the persisted summary, regardless of which hard gate emitted it.
- Existing artifact digests, authority separation, canonical-write boundaries,
  and data-only probe ownership remain unchanged.

## Write scope

- `archflow/realization/sandbox.py`
- `archflow/runtime/sandbox_gold.py`
- `tests/test_sandbox_realization.py`
- `tests/integration/test_sandbox_gold.py`
- `docs/mapping/`

## Tests

- Grounded and floating support-graph observations.
- Approval policy issuance, expiry, binding, and reload rejection.
- Usability-only and mixed-gate rejected-candidate reload.
- Focused sandbox tests, architecture firewall, and full unittest discovery.

## Stop conditions

- Stop before synthesizing support, approval, or evidence.
- Stop if reload becomes weaker than execution-time validation.
- Stop if any change requires project, repository, or design-state ownership.
