# P069 — Full-scale reconstruction reference derivation

- Origin: Planning (reconstruction plan 执行路线 step 4; first tenant
  of the runtime workspace per spec §6.1)
- Status: Ready
- Depends on: P068, M069, P076, P077

## Goal

Derive the full-scale reconstruction target through the formal staged
runtime as the scripted reference: four stages at metre-cell footprint
granularity inside the 4096-cell bound, every stage passing its
declaration gate and its axial-symmetry gate, every dimension citing an
adopted fact or carrying a typed widened-range uncertainty, materials
assigned through the provenance ledger, and final fidelity measured
against the adopted dimensions. The scripted reference is the
acceptance test of the stage contracts; the live takeover reuses them
unchanged.

## Milestones

1. **Stage 0 — 场地与体量**: workspace project bootstrapped as the
   first tenant; full-scale context (site, program ranges, envelope and
   axis commitments); project-authored Stage-0 declaration contract
   with every range citing adoptions (56.2 m outer drum from
   43.3 + 2×6.4, portico width, heights); white-model root through the
   production chain gated by declarations and symmetry.
2. **Stage 1 — 结构与外皮**: drum wall with entrance, tapering dome
   shell with oculus, sixteen-column portico with pediment, step
   rings; stage declaration and symmetry gates; wall/oculus/column
   dimensions citing their adoptions.
3. **Stage 2 — 空间平面**: niche ring, aediculae, floor declarations.
4. **Stage 3 — 细部**: five coffer rings × 28, oculus ring, front
   steps; material ledger over all components; fidelity receipt vs
   adopted dimensions; CAD/IFC externalization with materials.

## Stop conditions

- Stop if any dimension would be invented instead of adopted or
  typed-uncertain.
- Stop if a stage could archive without its gates.
- Stop if records would land in the code repo instead of the workspace
  (anchors only in-repo).

## Tests

Stage-gate integration on the derivation runner; declared-vs-realized
fidelity measures; architecture scope and discovery.

## Headless stage progress surface

The explicit progress panel consumes a non-authoritative P036 export produced
by `tools/build_pantheon_progress_snapshot.py`. The export joins candidate
stage reviews, any real P078/P079/P080 and `StageEvidencePack@1` closure
records, and a direct OpenNURBS inspection of the retained `.3dm`. It never
starts Rhino and never infers acceptance from file existence.

Metric identity is a hard gate across all three boundaries:

1. geometry-program parameters carry metre units;
2. generated Rhino wrappers set and verify `Rhino.UnitSystem.Meters` before
   authoring geometry; and
3. the headless reader verifies the saved `.3dm` unit metadata before the
   model can be marked current.

Digest, object-count, unit, or current-program disagreement keeps the panel
status `BLOCKED`. Formal Stage completion still requires branch scope,
evidence sufficiency, convergence `stage_ready`, and a separate review;
`StageEvidencePack@1` itself has no selection, acceptance, or canonical-write
authority.
