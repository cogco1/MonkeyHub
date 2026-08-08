# P053 — Production responsibility isolation and atomic handover

- Origin: Planning
- Status: Done after P014 and P036
- Depends on: P014, P036
- Decision: `docs/decisions/ADR-001-single-production-owner.md`

## Goal

Provide one reusable, provider-neutral control plane that proves each production
responsibility has at most one active implementation, allows bounded shadow
comparison without production authority, switches ownership by exact-base
atomic handover, and rejects stale in-flight results without fallback.

## Boundary

```text
provider registration (no authority)
  -> shadow invocation (same request, non-production token)
  -> independent verification
  -> exact-base handover decision
  -> one active binding at authority epoch N+1
  -> provider result
  -> current-epoch revalidation
  -> existing domain validation / Committer / P036
```

P053 does not schedule work, choose a provider for design reasons, persist a
binding, invoke canonical writers, or migrate an existing responsibility. It
does not make provider execution and P036 project commit one transaction.

## Write scope

- `archflow/production/`
- `tests/test_production_responsibility.py`
- `docs/decisions/ADR-001-single-production-owner.md`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- A responsibility contract has exactly one contract owner and zero or one
  active provider.
- Registering a provider grants no production or canonical-write authority.
- Provider lifecycle is explicit: registered, shadow, verified, active, and
  retired. Retired providers must be qualified again before reactivation.
- Resolution without an active provider returns a named unavailable result and
  never searches for a fallback.
- Handover uses an exact expected binding digest, increments a monotonic
  authority epoch, prepares a complete receipt before mutation, and changes the
  old and new modes in one critical section.
- Stale and concurrent handovers fail closed with at most one winner.
- A provider result is accepted only when responsibility id, provider identity,
  epoch, and binding digest still match the active binding.
- Provider failure never calls a shadow, verified, or retired implementation.
- The public router carries no mutation or signing API; provider adapters
  receive only request and token, while qualification and activation require a
  separate reconciler capability. Adversarial providers remain out of process.
- Authority tokens and frozen invocation envelopes reject forgery, cross-router
  reuse, receipt mutation, machine-local paths, and non-JSON object keys.
- A -> B -> A is a new epoch and cannot create an ABA ambiguity.
- Stable states and receipts serialize provider identity but never serialize a
  callable, process handle, machine path, or canonical writer.
- Every authority token and receipt states
  `canonical_write_authority=false`.
- The mechanism returns typed values and chooses no persistence path; P036
  remains the only project writer.

## Tests

- Registration without authority and unavailable resolution.
- Lifecycle transition validity and verified-only activation.
- Exact-base activation, stale rejection, and concurrent single winner.
- Receipt-construction failure leaves the old binding unchanged.
- Identity mismatch and stale result rejection after handover.
- A -> B -> A monotonic epoch behavior.
- Active provider failure with zero fallback calls.
- Shadow execution receives no production authority.
- Stable serialization excludes handlers and machine paths.
- Provider self-elevation, grant/envelope forgery, receipt mutation, and unsafe
  provider-error formatting.
- Architecture firewall and full unittest discovery.

## Stop conditions

- Stop if two providers can be active for one responsibility.
- Stop if a failed provider triggers implicit fallback or dual write.
- Stop if an old-epoch result can reach downstream reconciliation as current.
- Stop if P053 needs a project path or becomes a second persistence authority.
- Stop if a specific P026, CAD, Minecraft, or model-provider answer enters this
  generic mechanism.


## Completion

- Completed: 2026-08-08
- Evidence: 18 focused production-responsibility tests, architecture firewall, and full unittest discovery passed under the machine verification receipt.
