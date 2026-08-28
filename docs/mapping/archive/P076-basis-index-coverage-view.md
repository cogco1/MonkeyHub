# P076 — Basis index and coverage view

- Origin: Planning (user direction: stage-scoped retrieval evidence
  should be filed by decision so editing one decision means reading one
  shard — bounding both tokens and blast radius)
- Status: Ready
- Depends on: P070, P074

## Goal

Derive a sharded, decision-keyed index over the retained basis records
(queries, snapshots, adoptions, calibrations) so that:

- changing one decision requires reading exactly its shard, nothing
  else — the context-assembly side can inject only the shards of the
  decisions open at the current gate;
- the same derivation yields the coverage view: decisions named by
  queries but backed by zero adopted facts surface as a typed
  `uncovered` list, never as silence;
- a per-source reverse shard maps each snapshot to the facts and
  decisions it supports, so revising or retracting a source names
  exactly what it reopens.

## Design

- `build_basis_index(records)` is a pure derivation over retained
  record payloads: adoption facts joined with calibrations and queries,
  grouped by decision ref and by snapshot source. Deterministic
  ordering; no authority; nothing invented.
- The index is a **derived view, not evidence**: it lives under
  `probes/<p>/index/basis/` as one shard file per decision ref plus a
  manifest naming the record digests it was derived from — rebuildable
  at any time, never a substitute for the records.
- The tool scans a probe's runs, writes shards and the manifest, and
  prints the coverage summary (covered / uncovered decisions, sources).
- Prompt-side consumption (injecting only open-gate shards into
  authoring context) is a named follow-on with P068/P066 — this card
  delivers the index, the coverage view, and the reverse source map.

## Stop conditions

- Stop if the index would gain authority or replace record reads for
  verification purposes.
- Stop if an uncovered decision would be dropped instead of listed.

## Tests

Shard grouping and determinism, uncovered listing, reverse source map,
manifest derivation refs; architecture scope and discovery.


## Completion

- Completed: 2026-08-28
- Evidence: build_basis_index derives sharded views from retained payloads (queries/snapshots/adoptions/calibrations): deterministic (order-independent, byte-identical shards), one shard per decision ref with facts+quote spans+adoption/authority ids, typed uncovered listing, per-source reverse shard naming the reopen set, manifest citing exactly the record files derived from. Built live for p066-live-monument: 5 decision shards (colonnade-bay-spacing 4 facts, column-shaft-m 3, orientation-axis 4, column-diameter-m 2, portico-width-m 2), 3 source shards, 0 uncovered; written to probes/p066-live-monument/index/basis/ as rebuildable derived view with no authority. tests/test_basis_index.py (6) green; archcheck PASS (also caught and fixed a real D8 violation: executable cad-scripts in probe exports renamed .py.txt with superseding manifests).
