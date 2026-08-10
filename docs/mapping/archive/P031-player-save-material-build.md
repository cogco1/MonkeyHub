# P031 — Player save, material, and staged-build workflow

- Origin: Planning
- Status: Ready after M003, P025, P026, and P034
- Depends on: M003, P025, P026, P034

## Goal

Add post-design material accounting, staged realization, save/export, reload,
and shareable building packages. Platform adapters may target `.schem`,
Minecraft, Rhino, Revit, or other formats without granting exports future
generation authority.

## Write scope

- `archflow/interaction/`
- `archflow/runtime/staged_build.py`
- `archflow/runtime/artifact_library.py`
- `tests/test_staged_build.py`
- `tests/test_artifact_library.py`
- `tests/integration/test_saved_build_reload.py`
- `docs/mapping/`

## Acceptance

- Material totals, available amounts, shortages, and current-stage needs
  reconcile with the upstream resource/build policy before execution.
- Build speed, phase/layer, pause, resume, and cancel preserve exact plan state.
- Saved packages contain artifact, program, derivation, receipts, version, and
  authority metadata.
- Once geometry is materialized, the neutral shareable package requires
  provenance-coded iso, transverse section A, axial section B, elevation, and
  README. Requested platform exports are separately receipt-bound; failure of
  an optional `.schem`, Minecraft, Rhino, or Revit conversion cannot falsify
  the neutral artifact or silently change its geometry.
- Imported community packages are references/candidates, never implicit
  production generators.
- Reload reproduces the saved artifact and trace without claiming model/MCP
  execution replay.

## Tests

- Material shortage and staged-resume cases.
- Save/export/reload digest verification.
- Geometry-present neutral visual-package contract, optional platform export,
  and missing-view failure.
- Imported-package generation-authority rejection.
- Multiplayer permissions remain explicitly out of this card.

## Stop conditions

- Stop before selecting any platform-specific format without an adapter
  boundary and an explicit loss/equivalence receipt.
- Stop if save/share strips provenance or authority metadata.


## Completion

- Completed: 2026-08-10
- Evidence: ARCHITECTURE PASS (102 files); 449 tests passed with 2 explicit skips; material ranges and cumulative stage needs, exact-plan controls, neutral five-view package, explicit platform equivalence/loss receipts, P036 export persistence, and digest-stable reload verified.
