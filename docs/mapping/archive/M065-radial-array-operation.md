# M065 — Radial array realization mode

- Origin: Modify
- Status: Ready
- Depends on: P048, P061

## Goal

Extend the existing generic `ARRAY` geometry operation with a radial mode
(axis, center, angle step) realized and voxel-sampled deterministically, so
typed repetition can express rings and colonnades without enumerating
per-instance operations and without adding any building answer to the
framework.

## Rationale

The linear array already realizes up to 256 replicas from one authored
operation with exact membership semantics. Ring-shaped repetition (coffer
fields, drum recess rings, radial colonnades) currently requires one
authored transform per replica, which defeats the O(rules)-not-O(instances)
property that bounds provider output. A radial mode is generic geometry,
exactly like the linear step.

## Acceptance

- Linear arrays are byte-compatible: `mode` defaults to linear.
- Radial arrays rotate the single input about an explicit axis and center
  by an explicit angle step, `count` times; bounds are the exact union of
  rotated bounds; membership tests the inverse rotation per replica.
- Zero axis, zero angle with count above one, and out-of-bounds counts
  fail closed with typed errors.
- Voxelization is deterministic and matches membership sampling.

## Stop conditions

- Stop if the change would alter the retained linear-array contract.
- Stop before adding any typology, dimension, or placement default.

## Tests

- Radial realization bounds, membership, determinism, fail-closed inputs.
- Linear array regression suite unchanged.
- Architecture V3 scope diff, compileall, full discovery.


## Completion

- Completed: 2026-08-28
- Evidence: radial_array operation kind added beside the retained linear array: exact model-facing function contract, Rodrigues realization with exact rotated union bounds, per-replica inverse-rotation voxel membership, fail-closed degenerate inputs; 6 new tests plus the 9-test linear regression pass and ARCHITECTURE PASS
