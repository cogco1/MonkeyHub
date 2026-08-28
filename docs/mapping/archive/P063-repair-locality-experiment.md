# P063 — Repair-locality experiment

- Origin: Planning
- Status: Ready
- Depends on: P062

## Goal

Measure repair locality for the CAADRIA 2027 main research question (RQ2):
for the same frozen accepted local delta, does dependency-scoped repair
retain more unaffected commitments and recompute less design state than a
whole-chain rebuild, without reducing mandatory-validator pass rate?

## Design (from PAPER_CONTRACT §6 Stage B)

- Unit of analysis: one repair episode from the same retained accepted
  baseline and the same frozen delta.
- Cases: the three completed P062 case envelopes (clinic, workshop,
  courtyard) with their study-028 accepted production baselines.
- Edit classes, one preregistered edit per case:
  1. program-relation edit (access, adjacency, or privacy);
  2. semantic-component replacement with explicit responsibility transfer;
  3. geometric constraint edit (clearance, span, opening, or dimension).
- Strategies applied to the identical frozen delta:
  1. target-only patch — no dependency closure;
  2. whole-chain rebuild — recompute the complete downstream chain;
  3. dependency-scoped repair — recompute only the typed invalidation
     closure.
- 3 x 3 x 3 = 27 paired episodes. The delta is frozen once per case/edit
  and reused across strategies so model sampling is not the experimental
  variable; the episode executors are deterministic and invoke no provider.

## Gold impact set

Expected affected entities, obligations, geometry objects, and validators
are recorded per edit before execution, with named annotator identity.
Harness-proposed annotations are explicitly marked as such and remain open
to human architectural review; disagreements are retained, never resolved
after seeing executor output.

## Metrics

Repair success (preregistered mandatory validators), recompute ratio,
commitment retention, impact precision, impact recall, unintended change
ratio, failure attribution, and cost — each with exact denominators;
missing values remain typed unknowns.

## Write scope

- `archflow/evaluation/repair_experiment.py`
- `tools/run_repair_episode.py`
- `tests/test_repair_experiment.py`
- `probes/p062-*-case/` (episode records), `probes/p063-repair-study/`
- `docs/EXPERIMENT_PROTOCOL.md`, `docs/paper/`, `docs/ARCHITECTURE.md`,
  `docs/DYNAMIC_MAP.md`, `docs/mapping/`, `governance/work_registry.json`

## Acceptance

- The immutable 27-episode preregistration precedes any result.
- Gold impact sets precede execution and carry annotator identity.
- The strategies are generic executors over existing P055/P057 records
  with no geometry invention and no delta editing.
- Every metric reports exact denominators and typed unknowns.
- The P063 index stays table-closed until all episodes have exact
  outcomes; no aggregate winner is claimed by the framework.

## Stop conditions

- Stop if any strategy would acquire design, validation, promotion, or
  canonical-write authority.
- Stop if a delta cannot be applied identically under all three
  strategies.
- Stop before claiming population-level or model-ranking results from
  twenty-seven episodes.

## Tests

- Preregistration immutability, frozen-delta identity, and
  strategy-isolation deterministic tests.
- Gold-impact-set binding, precision/recall denominators, and
  typed-unknown metric tests.
- Architecture V3 scope diff, compileall, and full discovery.


## Completion

- Completed: 2026-08-28
- Evidence: repair-003 executed the corrected frozen 27-episode matrix: dependency-scoped repair matches whole-chain validity (9/9 no new failures) at 0.173 mean recompute ratio with exact gold-closure precision/recall 1.0, while target-only fails 9/9 on typed stale dependencies; defective runs repair-001/002 retained as executor-defect evidence; 7 contract tests and ARCHITECTURE PASS
