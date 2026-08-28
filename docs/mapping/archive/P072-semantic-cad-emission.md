# P072 — Semantic CAD emission

- Origin: Planning
- Status: Ready
- Depends on: P061, P071

## Goal

Make the CAD realization carry the program's semantics natively in the
CAD document — no plugins, no sidecar files: the mechanisms are core
Rhino object attributes persisted in `.3dm`.

## Design

- Every physical object is emitted with its `object_id` as the object
  Name and placed on a per-component nested layer
  (`archflow::<component_id>`), colored deterministically per component.
- Key-value user text on each object retains the binding ids, component
  id, commitment refs, evidence refs, and producer op id exactly as the
  compiled program states them. Document-level user strings retain the
  program provenance (proposal id, project, run).
- Component repetition becomes native instancing: array and radial
  array operations emit one block definition from the seed and N
  transformed block instances, mirroring the family/instance identity
  the program already owns, instead of anonymous copies.
- The build script reads its own semantics back from the document and
  prints them beside the measures; the equivalence receipt gains a
  semantic round-trip section comparing them against
  `expected_object_semantics(program)` derived from the program alone.
  A semantic mismatch is a typed finding, never repaired in place.
- The translator still owns no design authority; unsupported semantic
  carriers are typed losses.

## Stop conditions

- Stop if semantics would be invented beyond what the program states.
- Stop if a semantic mismatch would be silently dropped from the
  receipt.

## Tests

Semantic emission coverage (names, layers, user text, block
instancing), expected-semantics derivation, receipt mismatch typing;
determinism; architecture scope and discovery.


## Completion

- Completed: 2026-08-28
- Evidence: translate_to_rhino_python emits native Rhino semantics with zero plugins: object Name=object_id, per-component nested layers with deterministic colors, key-value UserText (bindings/component/commitments/evidence/producer op), document user strings for provenance, and array/radial repetition as native block definitions with N transformed InsertBlock instances (by-parent color). expected_object_semantics derives the expectation from the program alone; receipt CadEquivalenceReceipt@2 gained a semantic round-trip section. Monument rebuilt in Rhino 8: 13 family blocks (36/9/8/8/28x5/8/8/15/72 instances), 17/17 objects semantics VERIFIED, geometry still EQUIVALENT at 0.346 m max deviation; .3dm saved with semantics persisted. tests/test_cad_program.py (11) green; archcheck PASS.
