# P064 — Pantheon golden inverse derivation

- Origin: Planning
- Status: Ready
- Depends on: P062, P063

## Goal

Inverse-derive the frozen V3 Pantheon golden sample
(`D:\ARCHFLOW_V3\samples\pantheon_golden`, re-frozen 2026-07-18) into V4
records: transcribe its retained nine-stage generation trace into an
explicit component tree, decision sequence, and typed dependency edges;
replay only the coarse massing deterministically; and measure voxel
fidelity against the frozen schematic.

## Boundaries (L8 quarantine)

- V3 inputs are read-only external evidence, referenced by the exact
  sha256 digests already pinned inside the golden's own
  `zoning_manifest.json`; no V3 code or asset is imported, copied, or
  executed.
- V3 already reproduces its own golden byte-for-byte; V4 makes no byte
  replay claim. The V4 claim is semantic re-derivation of the coarse
  massing measured by bounding box, solid-cell IoU, and per-part
  presence, with exact denominators.
- Full-fidelity replay (333 components, families, statuary),
  live-provider re-derivation, and platform export are explicitly out of
  scope and named as such in the fidelity receipt.

## Deliverables

1. `probes/p064-pantheon-inverse/` P036 project: evidence ingestion
   records, the transcribed component tree, decision sequence, and
   dependency edges — every entry citing the exact V3 trace or manifest
   field it transcribes.
2. A deterministic coarse massing replay (drum, dome shell, oculus,
   portico) derived only from transcribed dimensions and canon ratios
   (module D=4, span 94, rise/span 0.500, oculus/span 0.188).
3. A fidelity receipt against the frozen `pantheon.schem`
   (98 x 127 x 94, 325,282 solid cells, sha256 `61e0e7cb…`).

## Stop conditions

- Stop if any step would import V3 code or write inside the V3 tree.
- Stop if the schematic digest does not match the pinned freeze.
- Stop before claiming full-fidelity or byte-level reproduction.

## Tests

- Sponge schematic parser digest and occupancy determinism.
- Transcription citation completeness and quarantine boundary.
- Fidelity metric denominators and claim boundary.
- Architecture V3 scope diff, compileall, and full discovery.


## Completion

- Completed: 2026-08-28
- Evidence: inverse-001 retains digest-pinned V3 external evidence, a fully cited 15-component transcribed tree, 10-decision sequence, and 5 typed dependency edges; coarse canon-ratio massing (no fitting) reaches plan-footprint IoU 0.925 and elevation-silhouette IoU 0.937 against the frozen schem whose parsed 325,282 solid cells match the manifest exactly; receipt denies byte-replay and full-fidelity claims; 7 contract tests and ARCHITECTURE PASS
