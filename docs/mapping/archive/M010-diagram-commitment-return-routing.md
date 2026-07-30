# M010 — Diagram commitment-return routing

- Origin: Modify
- Status: Done
- Depends on: M009

## Goal

Repair the presentation-only return connector from the commitment monitor so
hard-gate and commitment findings visibly merge into one evidence-bound
obligation path back to `D_v,k`.

## Write scope

- `docs/diagrams/`
- `docs/mapping/`

## Acceptance

- The commitment-monitor connector reaches the shared return path rather than
  ending as a detached segment.
- A visible junction makes the two read-only finding sources unambiguous.
- Chinese and English SVGs retain identical connector geometry.
- Both PNG exports include the complete 1600 by 1430 SVG canvas.

## Tests

- SVG XML parse.
- Connector coordinate parity.
- PNG dimension check.
- Visual inspection of the shared return junction and full canvas.

## Stop conditions

- Stop before changing any architecture responsibility or process semantics.


## Completion

- Completed: 2026-07-25
- Evidence: Extended the commitment-monitor return segment from x=860 to the shared x=740 return path and added an explicit junction node; hard and commitment findings now share one evidence-bound obligation arrow back to D_v,k.
- Evidence: Chinese and English SVG connector coordinates match; both XML documents parse and visual inspection confirms no detached yellow segment.
- Evidence: Re-exported both complete diagrams at their native 1600x1430 canvas, correcting the prior 1570x1280 cropped PNG export; devctl scope and 5 devctl tests passed.
