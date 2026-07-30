# P041 — Typed decision operator and operational state closure

- Origin: Planning
- Status: Ready after P015, P016, and P019
- Depends on: P015, P016, P019

## Goal

Compile one Architect-authored design move into a typed, exact-base operator,
apply its direct state delta, and deterministically propagate all named
future-relevant consequences into the next branch-local operational state.

The mechanism constructs an operationally Markovian working approximation. It
does not claim that architectural design is naturally Markovian, that a model
proposal is a fact, or that the working state has canonical write authority.

```text
natural-language design move
  -> typed DecisionOperator
  -> exact-base StateDelta
  -> deterministic dependency / invalidation closure
  -> OperationalMarkovState D_v,k+1
```

## Write scope

- `archflow/state/decision_operator.py`
- `archflow/state/operational_state.py`
- `archflow/state/__init__.py`
- `tests/test_operational_markov_compiler.py`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/diagrams/`
- `docs/mapping/`

## Acceptance

- `OperationalMarkovState@2` separates canonical base authority from
  branch-local epoch and carries typed brief, semantic, geometry, parameter,
  lock, commitment, obligation, dependency, evaluation, uncertainty, phase,
  deliverable, budget, and evidence information needed by the next move.
- `DecisionOperator@1` binds the exact input state and explicitly represents
  preconditions, bindings, fact additions/deletions, spawned commitments,
  spawned/discharged obligations, and invalidations.
- A deterministic compiler produces a bounded `StateDelta@1`; a proposal or
  natural-language statement cannot directly mutate state.
- Closure follows only explicit dependency edges to a fixed point, marks
  affected downstream outputs stale, and creates evidence-bound revalidation
  obligations without prescribing a building solution.
- Active locks reject unauthorized binding or fact mutation.
- The output remains `D_v,k+1`; only the existing outer review and single
  writer may create canonical `C_v+1`.
- No building dimensions, rooms, topology, materials, coordinates, named
  building, or universal expert order is encoded.
- All types are side-effect free and choose no persistence path.

## Tests

- Typed operator and exact-base rejection.
- Preconditions and lock-authority rejection.
- Add/delete/bind/spawn/discharge transition.
- Transitive dependency closure and local invalidation.
- Two history-distinct but operationally equivalent states admit the same
  decision result.
- Deterministic digest and bounded fixed-point behavior.
- No instance-answer or persistence-path scan.

## Stop conditions

- Stop if closure needs to infer an unnamed architectural dependency.
- Stop if a proposal, expert, evaluator, or tool gains direct state or
  canonical write authority.
- Stop if full prompt/tool history is copied into the operational state.
- Stop if a building-specific answer enters framework defaults.


## Completion

- Completed: 2026-07-25
- Evidence: Implemented side-effect-free OperationalMarkovState@2, DecisionOperator@1, StateDelta@1, exact-base precondition and lock checks, deterministic explicit dependency closure, local invalidation obligations, operational equivalence digests, and bilingual diagram chain; 117 tests passed with 1 explicit external smoke skip, compileall, scope, XML, PNG, map, and instance-literal scans passed.
