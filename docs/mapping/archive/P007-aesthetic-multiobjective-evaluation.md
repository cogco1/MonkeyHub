# P007 — Aesthetic multi-objective evaluation

- Origin: Planning
- Status: Done
- Depends on: P004, P006

## Goal

Add read-only aesthetic and spatial-quality observations that help the
Architect compare candidates without converting taste into a hard compliance
gate or a single scalar authority.

## Write scope

- `archflow/evaluation/aesthetic.py`
- `archflow/evaluation/README.md`
- `tests/test_aesthetic_evaluation.py`
- `tests/fixtures/views/`
- `docs/mapping/`

## Acceptance

- Evaluators may observe proportion, massing coherence, facade rhythm,
  legibility, spatial variety, material coherence, and view-dependent quality.
- Results are structured score vectors plus rationale and evidence references.
- Missing views return unavailable/low-confidence observations, never invented
  visual claims.
- The primary Architect may compare Pareto trade-offs and retain authorship.
- Aesthetic output cannot waive hard usability failures or write canonical
  state.

## Tests

- Same evidence yields stable schema and bounded output.
- Missing/failed visual evaluator is recorded without blocking hard validation.
- Multi-objective observations are not collapsed into an automatic winner.
- Evaluation remains read-only.

## Stop conditions

- Stop if the implementation promotes one aesthetic score to final authority.
- Stop if visual evidence is unavailable and cannot be regenerated safely.
- Stop after two failures of the same evaluator boundary.


## Completion

- Completed: 2026-07-24
- Evidence: 5 aesthetic tests and full 50-test unittest suite pass; compileall and P007 five-path scope check pass
