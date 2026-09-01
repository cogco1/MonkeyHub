# P088 — Discipline-by-machinery ceremony reduction

- Origin: Planning
- Status: Ready
- Depends on: P036

## Goal

Move hand-written discipline into machinery without weakening any audit
guarantee: one authority-block helper with port-level validation, schema
key sets exported at the definition site and imported by every consumer,
read guards that downgrade unknown keys to typed diagnostics while write
guards stay exact, and cross-record references converged on the semantic
digest single track. The 2026-09-01 audit measured the scribe cost this
replaces: 508 hand-written authority blocks, 297 exact-key validations
with hand-copied key sets, and a three-generation schema drift
(ThreeDmInspectionSummary @1 pinned in the viewer against @3 stored and
@4 emitted). No new capability surface.

## Acceptance

- A single helper produces the authority block; ports validate it; a
  static scan rejects newly inlined authority dicts.
- Schema key sets exist only at the definition site; the state-tree
  viewer imports them and renders ThreeDmInspectionSummary@3 and @4
  records from the villa project.
- Read guards accept unknown keys as typed warnings with exact
  diagnostics; write guards remain exact; both directions tested.
- New cross-record references carry the semantic digest only; byte
  hashes remain internal to storage and transfer.
- Full unittest suite and the architecture firewall pass.

## Write scope

- `archflow/contracts/`
- `archflow/state/`
- `archflow/project/`
- `archflow/adapters/three_dm_inspector.py`
- `tools/state_tree_viewer.py`
- `tests/`
- `docs/mapping/`
- `governance/work_registry.json`

## Tests

- Authority helper round-trip and inline-dict static scan.
- Schema guard asymmetry (write exact, read tolerant) per supported
  version.
- Viewer rendering against retained @3 records and freshly emitted @4
  inspections.
- Architecture firewall.

## Stop conditions

- Stop if any change would loosen a write-side contract.
- Stop if the viewer would gain selection, acceptance, or write
  authority.
- Stop before altering record content semantics — this card moves
  enforcement, never meaning.


## Completion

- Completed: 2026-09-01
- Evidence: 1492 tests + 2 skips PASS; ARCHITECTURE PASS (290 files); retained villa @3 record (40 layers, 1120 objects) and freshly emitted @4 inspection of the 58MB stage-5 3dm both adapt with zero error and zero warning diagnostics; inline authority-literal ratchet baseline 782; scope PASS (10 paths)
