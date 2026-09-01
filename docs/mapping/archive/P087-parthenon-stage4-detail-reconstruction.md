# P087 — Parthenon Stage 4 evidence-bound detail reconstruction

- Origin: user continuation and open-asset rule, 2026-08-29
- Status: Done
- Depends on: P086

## Goal

Create `reconstruction-005` as the first genuinely inherited Parthenon detail
stage: consume the exact Stage 3 state/model/program and exact current visual
ROI manifest, compile branch-fit textual and optional open-asset evidence, then
generate and self-check a metre-based Rhino Z-up detail candidate without
manual Rhino edits.

## Boundaries

- Exact predecessor is reconstruction-004 progress
  `ac778e607e87...b4f4799a`, model `b5815fa4...dae7f0`, Stage 3 state
  `cd7234b8...122bed`, pack `ac4d8ee9...55a3e3`, and program
  `ba4eb7bb...8ef6c`; directory order or mtime never selects a predecessor.
- `research-006` owns Stage 4 textual and asset research;
  `reconstruction-005` owns the candidate model and receipts.
- Source models, if any, live under
  `runs/research-006/workspaces/asset-rag/source-models/<asset-id>/`.
  The human-readable provenance document is
  `runs/research-006/workspaces/asset-rag/_外部资产来源清单.md`.
- An external asset is only eligible when its source URL, author, explicit
  license, retrieval time, content digest, format, units, up axis, intended use,
  and adoption decision are retained.  The original binary also enters the
  P036 object store.
- If a source requires login, CAPTCHA, account authorization, or non-public
  download, write a blocker and stop for user login; never bypass or retain
  credentials.
- The selected branch is `idealized-periclean-original`.  Roman, Byzantine,
  restoration-process, and other later text/inscriptions are excluded from the
  generated building surface.
- The output remains HOLD and has no canonical write authority.

## Acceptance

1. A typed predecessor-binding receipt proves exact Stage 3 state, pack,
   program, model, branch, metre units, Rhino Z-up, spatial pass, and unchanged
   inherited fact/lock content.
2. Stage 4 adds an OPEN obligation before geometry and closes it only after
   model readback, spatial validation, detail validation, and evidence gates;
   no pre-closed convergence is possible.
3. Web RAG distinguishes HARD dimensions, SOFT reconstruction choices, parked
   visual hypotheses, and branch-excluded later interventions; all adopted
   claims and reasons are retained under `research-006`.
4. Any downloaded 3D asset satisfies the provenance/license/unit/axis contract;
   otherwise the procedural geometry route is used and the non-adoption reason
   is documented in `_外部资产来源清单.md`.
5. The generated 3DM refines Doric shafts/capitals and entablature articulation,
   retains the 92-metope topology, represents supported facade openings, and
   excludes unsupported readable text, lost figures, and uncalibrated metric
   inference.
6. 3DM readback proves metre units, Z-up, predecessor binding, required detail
   object counts/hosts, no branch leakage, no collisions introduced by detail
   operations, and a COMPLETE Stage 4 evidence pack.

## Write scope

- `archflow/capabilities/stage_evidence_pack.py`
- `tools/run_parthenon_stage4_reconstruction.py`
- `tests/test_stage_evidence_pack.py`
- `tests/test_parthenon_stage4_reconstruction.py`
- `probes/parthenon-reconstruction.anchor.json`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Verification

- Offline cross-run pack lineage, state-inheritance, evidence, IR, model,
  detail, login-blocker, and open-asset provenance tests.
- Live bounded `research-006` plus `reconstruction-005` run and 3DM readback.
- Architecture firewall and project repository verification.

## Stop conditions

- Stop for user login or ambiguous/non-open asset licensing.
- Stop before changing inherited facts or locks without an explicit revision
  receipt.
- Stop before using visual pixels as exact dimensions without calibration.
- Stop before adding later inscriptions to the Periclean-original branch.


## Completion

- Completed: 2026-08-29
- Evidence: project://parthenon-reconstruction/exports/parthenon-stage-4-progress-1060f6732d80606e449940c9107160f2fdd1e4ab9271367f8f83f8c32b5dbc7c.json
- Evidence: project://parthenon-reconstruction/runs/reconstruction-005/branches/idealized-periclean-original/records/stage-4-evidence-pack-533cd7d76d7ed86a5152c9bc0d802cf9846725bfcb6d850235ec4b3835dc39ca.json
- Evidence: project://parthenon-reconstruction/runs/reconstruction-005/branches/idealized-periclean-original/records/stage-4-operational-state-3ba5880632383d89b59ba372c46b3513b86400c2235deb91f4716e9e79b5c096.json
- Evidence: project://parthenon-reconstruction/objects/sha256/79/79e333e33c8db6edcee7ee83068373fcba6bb97ed4aba6c6eda39e5514b1acba
- Evidence: project://parthenon-reconstruction/objects/sha256/9c/9c00f9568c436ccee20219f6c9b2a1ae05d37aed839e2a588491ab05100f4dda
- Evidence: 509-object Meters/Z-up 3DM readback and Stage 4 detail/spatial/evidence gates PASS; COMPLETE pack; HOLD; 56 related tests PASS; compileall PASS; ARCHITECTURE PASS (145 files); project repository verify PASS; canonical HEAD version 0 unchanged

## Full-building correction amendment

The first `research-006` / `reconstruction-005` result is retained, but its
509-object candidate is reclassified as `FAILED_ATTEMPT_EVIDENCE_ONLY`.  Its
local gates closed a 110-operation detail allowlist and treated 161 inherited
operations as copied; that did not prove full Stage 3 semantic-denominator
coverage, roof/eaves/pediment deepening, inner-colonnade support continuity,
principal-door aperture dependency, real Rhino material binding, or exact
detail-to-detail relation closure.

The corrected result therefore starts again from exact `reconstruction-004`,
not from the failed Stage 4 candidate, and is retained in sibling runs
`research-007` / `reconstruction-006`:

- all 271 Stage 3 root operations and all 11 semantic components receive an
  explicit disposition, and all 687 Stage 4 successors fold back to that exact
  denominator;
- roof, eaves, pediments, tympana, raking cornices, tiles, rafters, timbers,
  ridge, and acroterion seats are represented by 34 typed operations while
  sculpture remains parked;
- the 46 inner shafts have real 20-flute geometry, 23 two-tier U-colonnade
  stacks have typed capital/bearing continuity, and direct shaft-to-shaft
  contact is zero;
- the human statement `人工授权的复原，注意依赖关系完整` authorizes only a
  SOFT closed symmetric double-leaf candidate.  Both door leaves, frame,
  threshold, wall hosts, and clear opening derive from one host-local aperture
  contract; the decision is non-metric, non-historical, non-canonical, and is
  invalidated by predecessor, branch, host, evidence, strategy, or authority
  changes;
- 687/687 Rhino objects use object materials from a five-material table;
- 1,408 exact operation-pair relation contracts close all 235,641 evaluated
  pairs without wildcard role authorization;
- the fixed sibling runs are created as one repository batch only after all
  eleven gates, close/convergence, evidence-pack compilation, provenance-ref
  invariance, and exact execution/contract digests pass preflight;
- retained readback verifies 50 JSON records and three artifacts through P036,
  then directly reopens the 687-object metre-based 3DM.

Corrected evidence:

- Progress: `project://parthenon-reconstruction/exports/parthenon-stage-4-progress-0852137bf423f5b8a0d1beb40c2cd6a51ab099471e0d660d777f9e3b62365e23.json`
- State: `project://parthenon-reconstruction/runs/reconstruction-006/branches/idealized-periclean-original/records/stage-4-operational-state-9e61495b37c1f6336a58139ce6ff677c25b75b430e198a0bbc32e2002d40b1d0.json`
- Program: `project://parthenon-reconstruction/runs/reconstruction-006/branches/idealized-periclean-original/records/stage-4-geometry-program-b71f370d33b6ccceacd8e10780af2cf6c7e19f1ae593b18f1d3f4808ff8ad5b6.json`
- Pack: `project://parthenon-reconstruction/runs/reconstruction-006/branches/idealized-periclean-original/records/stage-4-evidence-pack-71fa34c3cc77c4709b5f534a8fa30d95c88b9e7b513d8f4d2bac6f3c6b7c6d56.json`
- Persisted-chain verification: `project://parthenon-reconstruction/runs/reconstruction-006/branches/idealized-periclean-original/records/stage-4-persisted-run-chain-verification-b8d8003391ca2c69ad54aeee2d7c6f641ee8b8dfb2c051de13f3fa560b597a51.json`
- Model SHA-256: `e5ea5a7a22afaf599fe971ee7758e2da34df94922f54c4c7b22c27dea2803de7`
- Result: COMPLETE evidence pack, candidate `HOLD`, canonical HEAD version 0 /
  state SHA-256 `2aa733c5fb565428a4c08aed1d278135d571bded474b64f89b6039a6ee44f266`
  unchanged.
