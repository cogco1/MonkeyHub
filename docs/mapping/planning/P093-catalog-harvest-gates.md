# P093 — Catalog confrontation and harvest obligations

- Origin: Planning
- Status: Ready after P091
- Depends on: P091

## Goal

Put the catalog on the write path and the harvest on the acceptance
path, so component reuse is guaranteed by mechanism rather than left to
memory. Geometry authoring opens only after a per-family answer: a
selected template ref, or a typed declination with reason. Stage
acceptance emits a harvest obligation — or a recorded waiver — for every
family the run produced inline. Both reuse P050 selection receipts and
the existing obligation machinery; the gates carry no selection,
acceptance, or canonical-write authority of their own. Declinations are
the legitimate escape valve that keeps the library from ossifying;
obligations are what keeps converged search from dying untextualized.

## Acceptance

- Authoring without a per-family catalog answer fails typed; declination
  records carry reasons and feed the harvest queue.
- Stage acceptance on a run with inline-produced families emits harvest
  obligations; waivers are recorded with reasons and are auditable.
- End-to-end demonstration: a family declined in one run surfaces as an
  obligation, harvests into a template, and the next authoring session
  is offered it in the catalog.
- Emergency repair flows pass through the waiver path without blocking.

## Write scope

- `archflow/capabilities/`
- `archflow/production/`
- `archflow/control/`
- `tests/`
- `docs/mapping/`
- `governance/work_registry.json`

## Tests

- Entry-gate typed failure and declination record round trip.
- Acceptance-gate obligation and waiver emission.
- End-to-end decline-harvest-offer cycle with a scripted provider.
- Architecture firewall.

## Stop conditions

- Stop if the entry gate would block an emergency repair with no waiver
  path.
- Stop before granting the catalog or either gate any authority beyond
  typed refusal.
