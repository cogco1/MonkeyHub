# P032 — Building-scoped site context compiler

- Origin: Planning
- Status: Ready after P020
- Depends on: P002, P004, P020

## Goal

Compile an authorized building-scoped `SiteContext` before spatial design
without prescribing grading, relocation, or foundation answers.

## Write scope

- `archflow/state/site_context.py`
- `archflow/runtime/site_compiler.py`
- `archflow/adapters/site_observation.py`
- `archflow/state/__init__.py`
- `archflow/runtime/README.md`
- `tests/test_site_context.py`
- `tests/fixtures/site/`
- `docs/mapping/`

## Acceptance

- Site context binds world, dimension, authorized envelope, anchor, approach,
  ground model, protected cells, observation digest, and unknowns.
- Superflat is one explicit simple site, not a hidden universal default.
- Terrain or access uncertainty creates evidence and obligations rather than a
  fixed response.
- Stale, cross-world, and unauthorized site evidence is rejected.

## Tests

- Authorized superflat context.
- Unknown and uneven context.
- Protected-envelope case.
- Cross-world and stale rejection.

## Stop conditions

- Stop if the compiler selects a foundation, grading, or relocation answer.
- Stop if a site observation can authorize a world write.



## Completion

- Completed: 2026-07-25
- Evidence: Implemented SiteContext@1 binding exact project base, world, dimension, read-authorized and observed envelopes, anchor, approaches, sampled ground model, protected cells, observation digest, unknowns, and obligations with no site-response or write authority.
- Evidence: Detached site adapter rejects stale base, cross-world, cross-dimension, anchor mismatch, out-of-envelope, cross-project, malformed, and write-authorizing observations before compilation; superflat requires explicit equal-elevation samples and is never a default.
- Evidence: Authorized superflat, uneven/unknown, protected-cell, isolation, stale, and unauthorized cases pass; 169 tests passed with one opt-in live retrieval smoke skipped, compileall passed, P032 scope passed, and instance-answer scan was clean.
