# P071 — CAD realization adapter

- Origin: Planning
- Status: Ready
- Depends on: P048, P065

## Goal

Realize the same neutral geometry program in a CAD kernel through the
external Rhino adapter, with per-object equivalence receipts against the
sandbox realization, so detail-stage validation gains exact measurement
while voxels remain the coarse-stage acceptance substrate.

## Design

- `cad_program.py` translates a compiled program deterministically into
  NURBS build steps (solid, revolve, extrusion, loft, booleans, linear
  and radial arrays). The translator owns no design authority.
- Execution runs through the external CAD adapter; its identity is
  retained in the receipt.
- The equivalence receipt compares each object's CAD bounds against the
  sandbox scene within explicit tolerances; disagreements are typed
  losses, never hidden.
- L15 remains the frame: this is an adapter, not a design authority; no
  reverse design flow.

## Stop conditions

- Stop if translation would invent or repair geometry.
- Stop if a loss would be silently dropped.

## Tests

Translator determinism and coverage; equivalence tolerances and typed
losses; architecture scope and discovery.


## Completion

- Completed: 2026-08-28
- Evidence: translate_to_rhino_python maps the promoted P065 36-op compiled program to a 187-line rhinoscriptsyntax script (deterministic: regeneration byte-identical, sha256 3117dd1446e4bf2b...); executed in external Rhino 8 slot via MCP adapter producing 17 physical objects / 308 breps; expected_object_bounds analytic replay compared against CAD measures: EQUIVALENT, max deviation 0.346 m within 0.6 m tolerance, zero translation losses; receipt retained as cad-equivalence-receipt in probes/p065-monument-derivation run monument-001. tests/test_cad_program.py (7) green; archcheck PASS.
