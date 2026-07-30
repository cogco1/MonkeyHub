# Evaluation

Owns read-only critics and soft multi-objective evaluators. Results should
remain structured as evidence, score vectors, uncertainty, and trade-offs rather
than being collapsed prematurely into a single mandatory taste metric.

Evaluation cannot waive a hard-constraint failure and cannot mutate state. See
[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).

Current status: P1 structured metric and `EvaluationObservation` contracts plus
a neutral claim-coverage evaluator. Soft evaluator exceptions produce
read-only error observations and do not gain commit authority.

## Aesthetic observations

`aesthetic.py` adds an evidence-grounded, multi-objective observation boundary
for proportion, massing coherence, facade rhythm, legibility, spatial variety,
material coherence, and view-dependent quality.

The evaluator receives only a detached `AestheticSnapshot`: an exact
`StateRef`, a candidate ID, and immutable references to rendered views. It has
no canonical-state writer, committer, MCP adapter, hard-validator receipt, or
world handle. Missing views and evaluator failures produce unavailable/error
observations with no invented metrics.

Scores remain a vector with per-objective rationale, confidence, and view
references. `pareto_front()` can expose non-dominated trade-offs, but it has no
weights, aggregate score, automatic winner, or promotion action. Hard usability
validation remains a separate prerequisite that aesthetic output cannot waive.
