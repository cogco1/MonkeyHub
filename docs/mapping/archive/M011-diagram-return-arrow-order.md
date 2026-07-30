# M011 — Diagram return-arrow vertical order

- Origin: Modify
- Status: Done
- Depends on: M010

## Goal

Swap the vertical order of the two arrows entering the left side of `D_v,k` so
the obligation return sits above the verified-transition return without a
crossing.

## Write scope

- `docs/diagrams/`
- `docs/mapping/`

## Acceptance

- The yellow obligation arrow enters `D_v,k` at y=500.
- The blue verified-transition arrow enters `D_v,k` at y=525.
- The horizontal yellow segment does not cross the blue vertical return.
- Chinese and English geometry remains identical and PNG exports stay complete.

## Tests

- Connector coordinate parity.
- SVG XML parse.
- PNG 1600 by 1430 dimension check.
- Visual inspection.


## Completion

- Completed: 2026-07-25
- Evidence: Swapped the D_v,k return-arrow endpoints: yellow obligation return now enters at y=500 and blue verified-transition return at y=525, eliminating their crossing.
- Evidence: Chinese and English SVG coordinates match, XML parses, visual inspection passed, and complete PNG exports remain 1600x1430.
