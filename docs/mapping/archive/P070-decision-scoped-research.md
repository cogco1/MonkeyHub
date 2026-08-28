# P070 — Decision-scoped agent research

- Origin: Planning
- Status: Ready
- Depends on: P062, P067

## Goal

Let the agent research precedents and codes on the live web scoped to one
named design decision, and route every adopted finding to exactly the
declaration ranges, constraints, or criteria it calibrates.

## Loop

1. A typed `PrecedentQuery` names the decision refs it calibrates, the
   question, search terms, and an optional jurisdiction allowlist.
2. Retrieval snapshots pages (P067 adapter): content-addressed, no
   authority, raw text never in a prompt.
3. A bounded RESEARCH invocation reads only keyword windows from the
   snapshots and may output only quoted fact candidates; every quote is
   machine-located verbatim in the full snapshot and the harness
   computes the authoritative span itself — a fabricated or paraphrased
   quote is a typed per-candidate rejection.
4. Candidates carry no authority. A typed adoption promotes selected
   facts, each retaining its decision refs.
5. A calibration record tightens the named declaration ranges or adds
   constraints and criteria, provenance-chained to the URL. Revising a
   source reopens exactly its decisions through the typed closure.

## Stop conditions

- Stop if raw page text would enter a prompt or a quote cannot be
  located verbatim in the retained snapshot.
- Stop if a fact would calibrate a decision it does not name.

## Tests

Window extraction, span verification, decision binding, fabricated-quote
rejection, calibration provenance; architecture scope and discovery.


## Completion

- Completed: 2026-08-28
- Evidence: Live loop closed in probes/p066-live-monument run research-003: PrecedentQuery (3 decision refs) -> retained Wikipedia snapshot -> 5 keyword windows -> one codex RESEARCH call -> 3 verbatim-located quoted facts (10:1 slenderness, 6:5 total-to-shaft ratio, capital-proportion flexibility) -> typed adoption -> per-decision calibration record. Honest failures retained: research-001 (candidate rejected, decision_refs outside query), research-002 (model returned zero candidates). Harness computes authoritative quote spans; paraphrase is a typed per-candidate rejection. tests/test_decision_research.py (10) green; archcheck PASS.
