# M029 — Geometry parameter JSON normalization

- Origin: Modify
- Status: Active
- Depends on: M027, M028, P050

## Goal

Repair the provider-facing P050 boundary exposed by real P026 run
`gold-agent-cli-007`. Canonicalize syntactically valid
`GeometryParameter.value_json` text before typed construction when the only
difference is JSON serialization, without changing the decoded value or
weakening typed geometry validation.

## Acceptance

- Valid parameter JSON text is parsed and re-encoded with the same canonical
  JSON function used by the proposal capability.
- Whitespace and object-key ordering differences do not consume a model repair
  round or change the decoded parameter value.
- Malformed JSON, non-finite values, parameter-kind mismatches, invalid units,
  and all geometry/compiler invariants remain rejected.
- `GeometryParameter` itself retains its strict canonical-state invariant.
- No project-specific geometry, missing parameter, dependency, or platform
  answer is synthesized.

## Write scope

- `archflow/capabilities/geometry_proposal.py`
- `tests/test_geometry_proposal_producer.py`
- `docs/mapping/`

## Tests

- Non-canonical but valid parameter JSON is accepted and stored canonically.
- Malformed JSON and decoded-value type mismatches remain rejected.
- Focused producer tests, architecture firewall, and full unittest discovery.

## Current evidence (2026-08-02)

- Real P026 run `gold-agent-cli-007` reached geometry round two and was rejected
  first at `value_json` text `[-0.2,3, -0.2]`; its decoded numeric vector is
  identical to canonical `[-0.2,3,-0.2]`.
- The provider-boundary normalization and hard-rejection regressions pass all
  16 focused producer tests. The standalone architecture budget test passes in
  0.87 seconds.
- Formal verification remains incomplete because the same pre-existing
  three-second architecture performance test took 5.48 and 5.76 seconds inside
  two full-suite runs. All other tests passed with two explicit external skips;
  the mandatory repeated-failure stop condition prevents a third attempt.

## Stop conditions

- Stop if normalization changes the value produced by JSON decoding.
- Stop before repairing a missing parameter or substantive geometry error.
- Stop before increasing P026 live repair rounds or weakening hard gates.
