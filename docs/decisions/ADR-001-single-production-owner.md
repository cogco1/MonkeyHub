# ADR-001 — One active owner for each production responsibility

- Status: Accepted for P053 implementation
- Date: 2026-08-08
- Decision owners: ArchFlow framework governance

## Context

ArchFlow can register multiple implementations for research, shadow comparison,
or staged replacement. Registration alone must not put an implementation on the
production path. Without a separate authority contract, a new implementation
can coexist with an old direct call, hidden fallback, cached handler, or stale
in-flight result and leave two effective owners for one responsibility.

P014 proves this boundary for V3 heritage, and P036 proves exact-base atomic
replacement for project `HEAD`. The same rule is not yet available as a generic
provider-neutral mechanism.

## Decision

Each named production responsibility has:

- exactly one contract owner;
- zero or one active provider binding;
- an exact authority epoch and binding digest;
- no implicit fallback;
- no canonical-write authority in provider or handover receipts.

Provider lifecycle is explicit:

```text
registered -> shadow -> verified -> active -> retired
                         ^                     |
                         +--- shadow again <---+
```

Only an exact-base handover may change the active binding. The handover prepares
its complete receipt before mutating the in-memory authority state, increments
the epoch, retires the previous provider, and activates the verified target as
one critical section. A stale decision or stale invocation result fails closed.

A failed active provider returns a named failure. It never invokes a shadow,
verified, or retired provider. Returning to a prior provider is a new handover
with a new epoch, not rollback of history.

The public router facade carries no lifecycle-mutation or signing API. Those
powers remain in an internal control plane reached through a separately held
reconciler object capability. The trusted adapter receives only the request and
bounded token; adversarial providers must remain out of process because Python
private naming is not a security boundary. Production tokens and frozen
invocation envelopes are authenticated by an ephemeral control-plane key, so
copied fields, cross-router reuse, and post-return receipt mutation fail closed.
The key is intentionally not persisted; restart rehydration remains outside
P053.

P053 is a control-plane mechanism. It returns typed states, tokens, and receipts
but chooses no filesystem path and writes no project state. P036 remains the
only project persistence authority. Later migration cards must remove direct
provider injection before claiming a responsibility has cut over.

## Consequences

- New implementations can be exercised in shadow mode without production or
  canonical-write authority.
- In-flight results can be rejected after a handover by revalidating their
  authority epoch and binding digest.
- Provider registration, qualification, invocation, and canonical persistence
  remain separate concerns.
- Restart persistence of an active binding is deliberately not decided here;
  it requires one explicit owner and destination before implementation.

## Rejected alternatives

- **Ordered provider lists with fallback:** failure semantics silently change
  ownership and retain legacy code on the production path.
- **Dual writes during migration:** two authorities can diverge and neither
  record proves which result is canonical.
- **Provider-owned activation:** an implementation could grant authority to
  itself and bypass independent reconciliation.
- **Cross-system atomic transactions:** ArchFlow cannot honestly make an
  external model process, project `HEAD`, and provider switch one transaction.
