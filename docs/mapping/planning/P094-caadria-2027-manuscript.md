# P094 — CAADRIA 2027 manuscript production

- Origin: Planning
- Status: Ready
- Depends on: P062, P063, P090, P093
- Deadline: 26 October 2026, 11:59 PM AoE

## Goal

Produce the CAADRIA 2027 full paper from the submitted abstract (final
Chinese version, 2026-07) and frozen project records: a Chinese working
draft first, then the English manuscript inside the official template.
Every empirical number carries a record reference and digest; every
abstract sentence maps to a section with its evidence status recorded;
gaps between the abstract and delivered evidence are stated, not
smoothed over. The paper workspace is `D:\PROJECTS_ACTIVE_当前项目\ARCHFLOW CAADRIA 2027\V4_RUNTIME\paper\caadria-2027\` (moved out of the repository on 2026-09-05: manuscripts are project output, not code documentation), which
owns no project or experiment state.

## Acceptance

- Chinese draft v0 exists and marks every abstract promise as delivered,
  待记录, 待做, or 裁决 (delivered 2026-09-01).
- `PAPER_CONTRACT.md` and `OUTLINE.md` are re-based on the submitted
  abstract: main question = derivation-history protocol versus terminal
  and staged regimes; repair locality becomes a secondary question.
- A decision record exists for the three-regime comparison promised by
  the abstract (run as its own card, or explicitly re-scoped in §5).
- A decision record exists for the two "preliminary results" episodes
  (portico column-count revision: record a real run or rewrite; interface
  collision: villa run-016 wording).
- Every number in the manuscript resolves to a retained record; a
  numbers-to-records table accompanies the draft.
- Figures 1–5 and Tables 1–2 have source manifests.
- Double-blind anonymisation, AI-use statement, and manual citation
  verification complete before submission.

## Schedule (claim-freeze gates from the contract, re-dated)

| Date | Gate |
| --- | --- |
| 2026-09-01 | Chinese draft v0 (this card's first deliverable) |
| 2026-09-05 | Decide experiment A (three-regime comparison) and re-base contract |
| 2026-09-10 | No new headline contribution after this date |
| 2026-09-22 | Experiment A executed if approved; otherwise §5 re-scoped |
| 2026-10-03 | Empirical evidence freezes |
| 2026-10-13 | Results text freezes |
| 2026-10-20 | References, anonymity, AI-use, figure provenance complete |
| 2026-10-26 | Submission |

## Write scope

- `V4_RUNTIME/paper/caadria-2027/` (workspace; the repository keeps no manuscript files)
- `docs/mapping/`
- `governance/work_registry.json`

## Tests

- Draft and manifests exist in the paper workspace.
- Architecture firewall (no framework code is touched by this card).

## Stop conditions

- Stop before any paper file repairs, infers, or omits a recorded
  outcome.
- Stop before a mechanism test or a diagnostic study is described as a
  measured model outcome.
- Stop before running an experiment under this card; experiments are
  their own cards.
