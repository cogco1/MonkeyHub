# P103 — Incremental Rhino patch

**Status:** active (registry registration deferred until codex's P089 registry edit lands)
**Lane:** productization and componentization
**Retires:** delete-all-and-rebuild as the *only* export path. The full rebuild stays as the
equivalence oracle (a patch must read back identical to a rebuild of the same program).
**Depends on:** P089 runner export reuse by program digest; P099 `revalidation_closure`; P102 closure.

## Why

Every upstream speedup ends in the same six-minute Rhino queue: the executor clears the document and
rebuilds every object (`cad_execution.py`, delete-all then full script), ~20–40 s per program, and a
villa run is several programs. A column-height change that moves four objects should cost four objects.

## Mechanism

1. `select_patch_operations(program, prior_program)` — object-level diff by digest: changed, added,
   retired outputs; plus every operation whose inputs are consumed by a changed one (booleans, arrays,
   lofts) so the subset is closed under `input_object_ids`.
2. Subset translation: `translate_to_rhino_python` over the patch order only, same emitters.
3. Prior document as the base: open the prior `.3dm`, delete by object name (`obj-…`) for every
   changed/retired output and every consumer that will be rebuilt, run the subset script, save.
4. Readback identical to the full path (by name, 3 mm), and a `PatchReceipt@1`: prior program digest,
   new program digest, patched object ids, deleted object ids, readback digest.
5. Oracle: the first N patches (and any patch whose closure exceeds half the program) also run the full
   rebuild and compare bounds; a mismatch is a typed failure that falls back to the rebuild and records
   the divergence.

## Acceptance

- [x] `tests/test_cad_patch.py` (7): no-op; capital change rebuilds 6 of 14; column height moves the chain even
      where digests hold (bounds criterion); retirement/addition; identity-only change selects nothing;
      subset translation; patch plan carries the selection and the whole-document denominator.
- [x] Villa west portico (reference-001): abacus side 1.12 → 1.20 rebuilt 6, kept 8; column height 6.426 → 7.0
      rebuilt 13, kept 1 (the tympanum is bound to the cornice level, so it does not move). Both patches read back
      **identical** to the full rebuild (worst 0.0 m). Wall clock 32.5 s vs 31.7 s and 28.5 s vs 38.5 s: on a
      14-object program the Rhino COM start + save (~30 s) is the whole cost, so the "< 10 s" target is not
      reachable through this executor regardless of the patch; the win scales with the number of kept objects
      and must be measured on a full villa/Rocca program (next).
- [ ] Runner uses the patch path when a prior export for the same project/branch exists, else rebuilds
      (blocked on codex's `project_runner.py` edit; the plan already records `patch` for the receipt).
- [ ] Prior programs with block-instance arrays (P099 typed instances) fail typed to a rebuild; carrying
      instance definitions by name is the next mechanism.

## Known parallel abstraction (retirement condition)

`cad_patch.structural_digests` is a second per-object identity beside the compiler's `object_digest`. It
exists because the compiler's digest folds in the semantic binding's whole object list and evidence refs, so
re-recording identical geometry marks every object changed. Retire it when the compiler's object digest is
split into geometry identity and binding identity (a `CompiledGeometryProgram` schema bump: frozen digests
in `test_geometry_compiler`, handover `object_digests`, the three `__all__`s); until then the patch selector
is the only consumer and the plan records which identity decided the rebuild.

## Do-not-do

No in-place editing of Rhino object geometry (always delete and re-emit by name); no patch without a
prior digest match; no silent fallback (the receipt says which path ran).

## Evidence

- 2026-09-02 villa reference-001: `project://villa-rotonda-reconstruction/runs/reference-001/records/portico-by-reference-report-7d6c705bc857db4ebf7e88ba49e4788dca9344a5af01106df1757ccc01b53cf1.json` (records `portico-rhino-execution-b1-patch/-full`, `-b2-patch/-full`, inspections).
