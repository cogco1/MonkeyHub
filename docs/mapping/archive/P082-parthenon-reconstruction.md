# P082 — Parthenon staged reconstruction

- Origin: user request, 2026-08-29
- Status: Done
- Depends on: P036, P078, P079, P080, P081

## Goal

Run a second monument through the frozen branch-conditioned workflow without
reusing Pantheon instance facts: retrieve and retain Parthenon evidence,
algorithmically select one explicit reconstruction branch, carry that branch
identity through four exact-successor stages, compile evidence sufficiency and
stage convergence, create headless metre-unit 3DM candidates, and retain an
honest wall-time and provider-usage summary.

## Boundaries

- Project identity is `parthenon-reconstruction`.
- Durable project state lives only in the configured runtime workspace; the
  repository contains code, tests, this card, and a relocation/evidence anchor.
- Building dimensions and topology remain project-instance data in the runner
  and retained project contracts; nothing becomes an `archflow/` default.
- The output is a `HOLD` candidate. It has no canonical-write or stage-
  acceptance authority.
- Provider-reported tokens are counted only when present. Bytes are never
  converted to tokens; unavailable Codex-app quota remains `n/a`.

## Acceptance

1. Web evidence, the candidate set, algorithmic selection, queries, adoptions,
   decision universes, and stage records carry one selected branch identity.
2. Four stages preserve the same building/component identities and each retain
   a COMPLETE evidence-sufficiency receipt plus an exact-successor, stage-ready
   convergence receipt.
3. Every hard dimension is linked to retained evidence; unresolved details are
   typed uncertainty or explicit scope exclusions.
4. Each stage produces a metre-unit 3DM file that can be inspected without
   starting Rhino; Stage 3 includes Doric-order detail, cella organization,
   entablature/pediments, and a Pentelic-marble material assignment.
5. A project run summary reports workflow wall time, HTTP/RAG calls, local and
   external provider calls, tokens when known, and all `n/a` fields honestly.
6. The repo anchor resolves to the workspace project and binds a final progress
   snapshot digest.

## Write scope

- `tools/run_parthenon_reconstruction.py`
- `tests/test_parthenon_reconstruction.py`
- `probes/parthenon-reconstruction.anchor.json`
- `docs/mapping/`
- `docs/DYNAMIC_MAP.md`
- `governance/work_registry.json`

## Tests

- Deterministic column topology and symmetry.
- Evidence completeness and branch identity inheritance.
- Exact-successor stage convergence.
- Headless 3DM unit/object inspection and usage-summary honesty.

## Stop conditions

- Stop before inventing an unsupported exact dimension.
- Stop if any artifact has no unique P036/workspace destination.
- Stop if a stage could be called accepted from file existence alone.
- Stop before estimating tokens from bytes or claiming unavailable quota data.


## Completion

- Completed: 2026-08-29
- Evidence: project://parthenon-reconstruction/exports/parthenon-progress-snapshot-90aed29c897835c08a360817a87eea494d6194c96444fbcd9b8f797f59f122ba.json
- Evidence: 3 tests PASS; ARCHITECTURE PASS (142 files)
- Evidence: final 3dm sha256 ff9ec8a81d239ea38281c0f8ca31b9e0f4ef15f2680faa7597a7e6ff83cde5f1
