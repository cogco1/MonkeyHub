# P066 — Live-model monument derivation

- Origin: Planning
- Status: Ready
- Depends on: P062, P065

## Goal

Replace the P065 scripted stage plan with a live model provider: the model
authors the coarse monument proposal, the selection, the neutral geometry,
and at least one stage deepening itself, under the frozen authoring
contracts, with every failed or timed-out attempt retained and no scripted
fallback.

## Known risks (from the study chronicle)

- Output budget: monument-scale footprints must fit the frozen output
  limits; choosing a coarser grid basis is the model's decision, not the
  harness's.
- Stage discipline: studies 015-023 showed revision-stage authoring is
  where live models fail most; the extended repair contracts are frozen.

## Acceptance

- Frozen provider profile before any call; exact P053 envelopes retained.
- Root authored end to end by the live model (two options, selection,
  geometry) with no harness coordinates.
- At least one live P055 deepening stage on the same component identities.
- Model-authored typed arrays expand into enumerated instances.
- Live evidence stays separate from the P065 scripted proof; partial
  progress closes honestly as partial.

## Stop conditions

- Stop on any scripted fallback or silent retry.
- Stop before claiming usable-building or paper results from one run.

## Tests

- Probe reload and provenance tests over retained live attempts.
- Architecture V3 scope diff, compileall, full discovery.
