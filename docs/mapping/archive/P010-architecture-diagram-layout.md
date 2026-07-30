# P010 — Architecture diagram layout

- Origin: Planning
- Status: Done
- Depends on: P000

## Goal

Refine the existing V4 bounded-agency diagram in the established V3 visual
language and export a reviewable PNG without changing the accepted
architecture.

## Write scope

- `docs/diagrams/v4-bounded-agency.svg`
- `docs/diagrams/v4-bounded-agency.png`
- `docs/diagrams/v4-bounded-agency.en.svg`
- `docs/diagrams/v4-bounded-agency.en.png`
- `docs/mapping/archive/P010-architecture-diagram-layout.md`

## Acceptance

- The diagram distinguishes MCP/tool success from architectural usability.
- The primary Architect remains free inside the speculative workspace.
- Hard usability gates, state-responsive experts, soft aesthetic evaluation,
  obligations, and single-writer promotion are visually distinct.
- Operational Markov state appears only at formal checkpoints.
- Text is legible at normal desktop width with no overlaps or clipped labels.
- The PNG faithfully renders the SVG at presentation resolution.
- Chinese and English variants contain the same nodes, gates, and connectors;
  only language-specific copy and typography may differ.

## Tests

- SVG XML parses.
- Relative links remain valid.
- PNG opens and matches the SVG composition.
- Visual inspection at full size and fit-to-window.

## Stop conditions

- Stop rather than changing architectural semantics to make the layout easier.
- Stop after two failures of the same SVG render/export path.


## Completion

- Completed: 2026-07-24
- Evidence: SVG XML parse passed and 10/10 architecture anchors remained
- Evidence: 2320x2180 RGB PNG exported and visually inspected without clipping
- Evidence: P010 scope check passed for SVG PNG and card
- Update 2026-07-25: added an explicit seven-stage design-maturity graph,
  backward-invalidation path, phase-bounded dynamic experts, and coordinated
  Chinese/English 2320x2074 SVG/PNG variants.
