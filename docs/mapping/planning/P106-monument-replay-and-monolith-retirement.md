# P106 — Replay the monuments, then retire the monoliths

**Status:** blocked (needs P105)
**Lane:** productization and componentization
**Depends on:** P105 (the vocabulary), P089 (the runner), P102 (the State Record), P103 (the export path)
**Retires:** the per-building runner monoliths, once and only once their behaviour is reachable without them.

## Why

P089 promised this and could not honestly deliver it; the promise is kept here instead of being quietly dropped.
Two things stood in the way, and only one of them is P105's.

**The vocabulary** is P105.

**The monoliths are not only geometry.** `archive/tools/run_pantheon_reconstruction.py` (5,516 lines) carries stage plans,
stage contracts, stage-closure gates, detail enrichment plans, STL ingestion, Rhino overlay scripts, symmetry
findings, realized declaration values and candidate structure issues. `archive/tools/run_parthenon_reconstruction.py`
(3,390) and `archive/tools/run_parthenon_stage4_reconstruction.py` (5,818) carry their own, plus roughly 13,000 further
lines in the stage-4 helpers. Every one of those capabilities has to be reachable another way before the file that
holds it can become a stub, or the retirement is a deletion.

## Mechanism

1. **Extract, do not re-author.** The retained records already carry each monument's spatial option (the pantheon's
   `selected-spatial-option` holds 5 components, 3 levels, 4 volumes, 3 zones) and its geometry program. The State
   Record is extracted from those records; nothing is invented.
2. **Replay and compare** through `tools/verify_state_record.py`, the same instrument the villa and Rocca use:
   object-by-object bounds against the retained program, recorded as `StateRecordEquivalence@1`.
3. **Inventory before retirement.** Every public entry point of each monolith is listed with where its behaviour
   lives after the move. Anything with no destination blocks the retirement of that file and gets its own line.
4. **Stub, then delete.** A retired tool first becomes a stub that names its replacement and fails typed, so a
   caller learns where to go; deletion is a later, separate step once nothing calls the stub.

## Acceptance

- [ ] Pantheon and parthenon State Records are extracted from retained records and run through the runner.
- [ ] Both reproduce their retained programs object by object within tolerance, recorded as equivalence receipts.
- [ ] An inventory names every monolith entry point and where its behaviour lives afterwards; nothing is dropped
      silently, and anything without a destination is carded before the file is touched.
- [ ] The retired tools are stubs that name their replacement and fail typed; their tests move to the runner path.
- [ ] Full unittest suite and the architecture firewall pass.

## Do-not-do

No retirement of a capability that has nowhere to go. No "replay" that flattens semantic members to make the
numbers match. No deletion in the same change as the stub.
