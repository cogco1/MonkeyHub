# P077 — Relation coverage ledger

- Origin: Planning (dimension-2 closure: basis divergence must find
  relations, not only values; the asymmetry defect was an unexamined
  relation)
- Status: Ready
- Depends on: P074, P076

## Goal

Make "find all the bases" auditable for relations: candidate
dependency edges between components are enumerated mechanically from
the realized artifacts, every candidate must be resolved — by a
machine-detected derivation, a declared edge with provenance, or an
explicit independence declaration — and an unresolved pair is a typed
`uncovered_relation`, never silence. Retrieval gains multi-source
sweep so a rule absent from one source can still be found.

## Design

- `enumerate_candidate_relations(scene_objects, bindings)`: component
  pairs whose realized bounds touch or overlap within a caller
  tolerance are candidate edges. Mechanical, deterministic; the
  framework names no relation kinds of its own.
- Machine-detected resolutions: an operation consuming another
  component's object (boolean cuts) or responding to another
  component's binding is a derived edge with the program as provenance.
- Caller-declared resolutions: typed edge declarations
  (`derived | constraint | independent`) each carrying refs (commitment,
  adoption, criterion). The ledger joins enumeration against detection
  plus declarations; everything left is `uncovered_relation` with both
  component ids and the contact geometry.
- The coverage ledger reports resolved/uncovered counts and ratios —
  the dimension-2 completeness metric.
- Research driver sweep: every `--url` is snapshotted and invoked
  separately (span verification stays per-snapshot); facts union across
  sources into one adoption, honest empties retained per source.

## Stop conditions

- Stop if the framework would own a relation kind, tolerance default,
  or resolution — enumeration mechanics only.
- Stop if an uncovered pair could be dropped instead of listed.

## Tests

Adjacency enumeration math, machine-derived edge detection, declared
resolution join, uncovered typing, determinism; architecture scope and
discovery.


## Completion

- Completed: 2026-08-28
- Evidence: relation_coverage.py: mechanical candidate enumeration from realized bounds (caller tolerance), program-edge detection (cross-component consumption/response with op provenance), declared-edge join (derived/constraint/independent, non-empty refs), typed uncovered_relation rows. Live ledger over the symmetric monument stage-3: 10 candidate edges, 3 resolved (colonnade-portico and portico-rotunda via commitment:primary-axis-center, dome-rotunda via envelope commitment), 7 honestly uncovered (coffers-dome, statuary rings, aedicula, recess, colonnade-rotunda) - coverage 30 percent retained as the dimension-2 metric and work queue. Multi-source sweep live: research-006 swept Pantheon and Roman-concrete pages in one query (18+7 windows, one invocation per snapshot, per-snapshot span verification), union adoption of 9 facts including wall thickness 6.4 m, interior diameter 43.3 m, aggregate grading, relieving arches - cross-source corroboration in a single calibration. tests/test_relation_coverage.py (9) green; suite 614; archcheck PASS.
