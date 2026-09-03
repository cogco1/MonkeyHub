# P103 — Incremental Rhino patch

**Status:** Done, 2026-09-02 (Claude). The fixed Rhino cost moved to P104, which is a new mechanism, not a retirement.
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

- [x] `tests/test_cad_patch.py` (8): no-op; capital change rebuilds 6 of 14; column height moves the chain even
      where digests hold (bounds criterion); retirement/addition; identity-only change selects nothing;
      subset translation; patch plan carries the selection and the whole-document denominator.
- [x] Villa west portico (reference-001): abacus side 1.12 → 1.20 rebuilt 6, kept 8; column height 6.426 → 7.0
      rebuilt 13, kept 1 (the tympanum is bound to the cornice level, so it does not move). Both patches read back
      **identical** to the full rebuild (worst 0.0 m). Wall clock 32.5 s vs 31.7 s and 28.5 s vs 38.5 s: on a
      14-object program the Rhino COM start + save (~30 s) is the whole cost, so the "< 10 s" target is not
      reachable through this executor regardless of the patch; the win scales with the number of kept objects
      and must be measured on a full villa/Rocca program (next).
- [x] Runner: artifact per program digest (`<stage>@<digest12>.3dm`); the latest succeeded export of the stage with a
      present model is the base — same digest → reused; empty selection → **restamp** (carry every object, re-stamp
      semantics: bindings, evidence, commitments); else patch; not expressible → rebuild; `RunOptions.patch_oracle`
      rebuilds beside every patch and compares readbacks (a mismatch is a typed failure). Rocca equivalence-002 through
      the runner: hall-wall what-if (2.5 → 3.0 piedi) patched 8 of 18 objects, kept 10, oracle equal; reverting it
      restamped the structure seat (0 rebuilt, 9 kept) and patched the envelope back (8/10), oracle equal — all
      readbacks verified. Wall clock 37.2–37.8 s per path either way: the COM start and save are the cost.
- [x] **Block-instance families are carried, not refused.** The blanket refusal is gone: the prelude rebuilds every
      instance definition in the prior file from its own member geometry and re-adds each instance reference against
      it (`AddInstanceObject`), and the carry is now verified by *name set* rather than a bare object count, since one
      arrayed object id is many Rhino objects. Live proof on the villa west band (run `array-patch-001`,
      `block-array-patch.report.json`): the `ground-left` opening arrayed to two placements makes a block family;
      widening the unrelated `attic-a` then patches, rebuilding 27 objects and keeping 35, of which the five
      `…-ground-left-…-array` objects are the carried family. Readback verified; the oracle rebuilt the same program
      in full and agreed on all 46 objects at 0.0 m.
## Handed to P104

The fixed Rhino cost is not a patch problem and is not solved here. Every path measured on real projects costs the
same to the second, because the variable part is small and the COM start and save are not:

| Path | Objects rebuilt | Wall clock |
|---|---|---|
| Rocca envelope, full rebuild | 18 | 37.9 s |
| Rocca envelope, patch | 8 | 37.2 s |
| Rocca structure, restamp | 0 | 37.5 s |
| Villa envelope, patch carrying a block family | 27 | 37.3 s |

A resident Rhino process is the only way below it, and that is a new mechanism rather than a retirement, so it is
its own card: P104.

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


## Completion

- Completed: 2026-09-02
- Evidence: Structural-digest and bounds diff closed under input edges; reuse / restamp / patch / rebuild by receipt evidence with a full-rebuild oracle
- Evidence: Rocca equivalence-002: hall-wall what-if patched 8 of 18 objects, restamp kept 9 rebuilt 0, oracle equal both ways
- Evidence: Villa array-patch-001: a block-instance family is carried, not refused; 27 rebuilt, 35 kept including the five arrayed objects; oracle agreed on all 46 objects at 0.0 m
