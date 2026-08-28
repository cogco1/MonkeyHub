# Pantheon golden inverse-derivation notes

**Status:** human-facing analysis notes, non-canonical, no V4 evidence claim.  
**Source (read-only V3 heritage, L8 quarantine):**
`D:\ARCHFLOW_V3\samples\pantheon_golden\` — frozen 2026-07-18 (M0.1/v5),
`pantheon.schem` sha256 `61e0e7cb…`, 98 x 127 x 94, 325,282 solid cells,
plus `generation_trace.json` (full 9-stage V3 trace) and
`zoning_manifest.json` (3-volume zoning with canon ratios and 反推 reasons).

## 1. What the golden contains

The V3 trace is already stage-structured:

| V3 stage | Content | V4 counterpart |
| --- | --- | --- |
| 1 prompt | "a large pantheon" | L0 raw brief |
| 2 routing | functional pack `rotunda`, typology `roman_pantheon`, style, scale | brief/typology facts (V4: provider-proposed, never framework-owned) |
| 3 program | zones porch/hall, `porch connects_to hall` from activity causality | P021 `DesignProgram` |
| 4–5 spatial schema + zoning obligations | enclosure classes, needs_walls/needs_roof | P023 zones + typed obligations |
| 6 structure seed | bays 3x3, max_span 8.0, 16 column anchors | structural facts + dependency edges |
| 7 components | 333 components by role and source rule | `DesignComponent` tree + P061 families |
| 8 verification | 0 hard fails, 1 soft (`entablature_over_H` 0.100 vs canon [0.2,0.25]), score 0.812 | hard gates vs soft evaluation (P060 project criteria) |
| 9 explanation chain | ChainLinks with decisions/because/outputs | `DecisionOperator` + dependency edges — but retrospective, not operational |

Canon ratios recorded as measured facts: dome rise/span 0.500,
oculus/span 0.188 (canon [0.18, 0.22]), coffers 5 x 28, module D = 4.

## 2. Decomposed component tree (from stage 7 source rules + stage "Generate" decisions)

```text
building  (roman_pantheon, large, D=4)
├─ portico  (porch zone, semi_exterior: needs_roof, open_sides)
│   ├─ column family        16 anchors, D=3, fluted=False   [roman_pantheon/portico, 92 comps incl. entablature]
│   ├─ pediment/entablature  soft: entablature_over_H off canon
│   └─ porch roof            roof_truss x4, roof_purlin x3
├─ rotunda  (hall zone, interior: needs_walls, needs_roof)
│   ├─ floor plinth          datum z=0                       [roman_pantheon/floor x4]
│   ├─ drum wall             load-bearing perimeter ring, 9-bay, span 94
│   │   ├─ axial doorway     from porch connects_to hall
│   │   ├─ interior recesses x15, aediculae x36 (family)
│   │   └─ drum cornice x3, drum attic x1
│   └─ dome                  hemispherical, rise/span 0.500  [roman_dome/rotunda x10]
│       ├─ oculus            aperture, oculus/span 0.188     "exterior inside the interior"
│       ├─ coffer field      5 rings x 28 (ONE component)    [roman_pantheon/coffers x1]
│       └─ step ring x3
└─ statuary ornament family  x161                            [pantheon.sculpt_statuary]
```

Note the convergences with existing V4 evidence: V3's single coffer-field
component matches P058 stage 2's bounded coffer field; the drum/dome/oculus
sequence matches P058's three stages; repeated elements (columns, aediculae,
statuary) are exactly P061 parametric/mesh families.

Recoverable dependency edges (from stage-9 `because` links):
`enclosure(hall)=interior -> roof_required`; `span(dome) -> bearing(drum wall)`;
`daylight obligation -> oculus aperture`; `porch connects_to hall -> axial
doorway`; `column anchors <- span check`. These become typed
`DependencyEdge` records, not prose.

## 3. Inverse-derivation path (V4 terms, quarantine-compliant)

1. **Evidence ingestion (read-only).** Register the three V3 files as
   retrieved evidence through the P012 quarantined bridge; digests are
   already pinned in `zoning_manifest.json`. V3 output never becomes a V4
   generated answer (L8 rule).
2. **Decision re-sequencing.** Transcribe trace stages 2–7 into an ordered
   set of exact-base `DecisionOperator`s (~12–15: typology commitment, zone
   facts + relation, enclosure obligations, structure seed, then the six
   "Generate" decisions: floor plinth -> drum wall -> doorway -> dome ->
   oculus -> coffer ring).
3. **Component tree authoring.** One `SpatialOptionProposal@2` carrying the
   tree above; repeated elements as P061 families with the canon ratios as
   parameters.
4. **Geometry programs + forward replay.** Compile through the P056 formal
   runtime into sandbox realization, exactly as P058 did for the simplified
   radial hall — this time at full monument fidelity.
5. **Fidelity measurement, not byte equality.** V3 claims byte-identical
   reproduction by its own pipeline; V4 re-derivation is a *different*
   claim: semantic re-derivation measured by bounding box (98 x 127 x 94),
   solid-cell IoU against the frozen schem, and per-component presence.
   Never claim replay of the V3 generator.

## 4. Why this matters for the paper

Same building, two representations:

- V3: a **retrospective** explanation chain — you cannot ask it "change
  oculus/span to 0.21" and get a bounded reopening.
- V4: an **operational** tree — the same knowledge as typed facts,
  commitments, and dependency edges, where that edit computes a closure
  that reopens only dome/oculus/daylight records while portico and drum
  commitments are retained.

That contrast is Figure 1 material (terminal/log vs process-first), and the
decomposition table is Figure 3 material. Both can be drawn from this
analysis without running anything new.

## 5. Governance boundary

- This document is analysis only. The executable inverse derivation is new
  work: it needs its own card (candidate: P064 "golden inverse derivation
  and fidelity replay") and must not start before P062 closes
  (PAPER_CONTRACT §10 forbids scope expansion in the P063 window).
- Time-boxed pre-deadline use: figures and the decomposition table only.
  Full replay + fidelity metrics are post-deadline work or a second paper's
  experiment (repair locality on a monument-scale case).
