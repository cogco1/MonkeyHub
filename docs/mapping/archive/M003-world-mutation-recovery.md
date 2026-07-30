# M003 — External world mutation recovery

- Origin: Modify
- Status: Done
- Depends on: P002, P018, P024

## Goal

Repair the existing Minecraft mutation boundary so partial or uncertain world
writes can be detected, compensated, reconciled after restart, and kept
separate from canonical acceptance.

## Write scope

- `archflow/adapters/minecraft_mcp.py`
- `archflow/adapters/README.md`
- `archflow/runtime/world_recovery.py`
- `archflow/runtime/README.md`
- `tests/integration/test_minecraft_mcp_adapter.py`
- `tests/integration/test_world_recovery.py`
- `docs/mapping/`

## Contract

The external sequence is explicit:

```text
prepare -> execute -> observe -> validate -> finalize
                    \-> compensate / orphan / manual reconcile
```

Every phase binds the exact plan, world/session identity, candidate workspace,
base state, and receipt chain. The protocol does not claim that Minecraft and
the canonical store form one atomic transaction.

## Acceptance

- Partial write and unknown write are distinct from unchanged world.
- Compensation success, failure, orphaned candidate, and manual reconciliation
  are distinct reloadable outcomes.
- Restart reconciliation cannot advance canonical state from uncertain world
  evidence.
- A successful world write remains only candidate evidence until full review.

## Tests

- Execute succeeds and capture fails.
- Compensation succeeds and fails.
- Restart reconciliation.
- No canonical advance from uncertain external state.

## Current evidence

- `MinecraftMutationReceipt@1` is written into the explicit speculative
  workspace before the execute call. Its hash-linked phase receipts bind the
  exact plan digest, world/dimension identity, session digest, workspace, and
  canonical base.
- Execute-started and execute-acknowledged are separate records. A lost or
  failed call therefore remains `write_unknown`; a confirmed write without
  observation remains `world_changed_unobserved`.
- Capture and transport binding are explicit observe/validate phases.
  Transport validation states that P005 hard usability has not been evaluated.
- Compensation is separately authorized. It requires the same world identity,
  an exact execution-supplied undo token, and an affirmative server
  acknowledgement. Success and failure are both recorded without claiming an
  atomic or universally reversible rollback.
- `world_recovery.py` reloads and verifies the complete receipt chain. Restart
  outcomes distinguish no write, pending candidate, orphaned candidate,
  compensated, compensation failed, manual reconciliation, manually
  reconciled, and exact receipt-bound canonical commit.
- Reconciliation is read-only. It cannot call Minecraft, change project
  `HEAD`, or accept a candidate. A canonical advance is recognized only when
  the durable candidate, candidate submission, exact plan digest, exact base,
  next head, and commit receipt agree.
- Recovery archives persist only through the P036 run-recovery destination.
- M003 integration coverage passes 14 tests, including capture-after-execute
  failure, compensation success/failure, restart reload, orphan detection,
  uncertain writes, manual resolution, and exact commit reconciliation.

## Stop conditions

- Stop if exact plan or pre-write world identity cannot be proven.
- Stop before calling compensation atomic or universally reversible.


## Completion

- Completed: 2026-07-27
- Evidence: Minecraft mutation now emits a hash-linked exact plan/world/session/base phase chain before execute, distinguishes unknown and acknowledged writes, records observation and transport validation, performs only opt-in exact-token compensation, and preserves success/failure/manual outcomes without atomicity claims. Restart reconciliation verifies durable candidate, submission, plan, base, head, and commit bindings; uncertain state cannot advance canonical. Recovery archives use P036 run-recovery. 268 tests pass with 1 external smoke skipped; architecture firewall passes 85 files; compileall and scope pass.
