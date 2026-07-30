# M024 — Sandbox Gold evidence honesty repair

- Origin: Modify
- Status: Ready after P049 and P050
- Depends on: P049, P050

## Goal

Repair the four audited honesty defects in the active P026 runtime
(`archflow/runtime/sandbox_gold.py`) so the Gold run derives what it
currently stages: the framework-owned box/door/window template, the
fabricated use-zone bindings, the phantom approval references, and the
schema-guaranteed concept rejection are replaced by record-driven
derivation and genuine review outcomes. This is a bounded repair of the
active path; the enabling capabilities are P049/P050 work, not this
card's.

## Acceptance

- `sandbox_gold` owns no massing, topology, opening, or dimension-bound
  constant; geometry arrives through the P050 producer over project
  records (closes review finding #3).
- Use-zone evidence derives from spatial derivation against observed
  regions; the required-spaces gate can genuinely fail and a negative
  test proves it (closes finding #12).
- The approval policy and pre-authorization event are persisted project
  records whose references resolve on reload (closes finding #13).
- Concept rejection emerges from hard gates judging genuine model
  output; no output schema or orchestrator branch guarantees failure,
  and an honest concept may legitimately pass (closes finding #14).
- The Gold E2E test drives the honest path end to end: raw request to
  accepted artifact, reload, and restart-reload, with the rejected
  lineage preserved.
- Case-specific content (prompt, scenario configuration, approval
  policy document) lives under `probes/p026-sandbox-gold/`, never in
  `archflow/`.

## Write scope

- `archflow/runtime/`
- `probes/p026-sandbox-gold/`
- `tests/integration/`
- `docs/mapping/`

## Tests

- Honest-path Gold E2E with scripted and repair-loop provider variants.
- Negative gate tests (required-spaces failure, resolvable approval
  references, non-staged rejection).
- Full reload and restart-reload chain.
- Static no-instance-default scan over `sandbox_gold`.

## Stop conditions

- Stop if closing a finding requires reintroducing an instance answer
  anywhere in `archflow/`.
- Stop if the P026 acceptance list would be satisfied by choreography
  rather than derivation.
- Stop before treating this repair as P026 completion evidence; P026
  closes on its own card after this repair lands.
