# M023 — Semantic-to-realization diagram bridge

- Origin: Modify
- Status: Ready
- Depends on: P047, P048

## Goal

Make the main bilingual architecture diagram show how one Design Decision
co-sources a Semantic Component and Realization Specification without changing
the accepted V4 authority model.

## Write scope

- `governance/work_registry.json`
- `docs/DYNAMIC_MAP.md`
- `docs/ARCHITECTURE.md`
- `docs/diagrams/v4-bounded-agency.svg`
- `docs/diagrams/v4-bounded-agency.png`
- `docs/diagrams/v4-bounded-agency.en.svg`
- `docs/diagrams/v4-bounded-agency.en.png`
- `docs/mapping/modify/M023-semantic-realization-diagram.md`
- `docs/mapping/modify/INDEX.md`
- `docs/mapping/archive/M023-semantic-realization-diagram.md`
- `docs/mapping/archive/INDEX.md`

## Acceptance

- One Design Decision visibly co-sources Semantic Component and Realization
  Specification.
- Realization Specification branches to Parametric, Authored Asset, and Hybrid
  families before compiling to GeometryProgram.
- RealizationReceipt preserves component, geometry-object, and platform-object
  identity.
- Structured scene state is authoritative validation and adapter input;
  screenshots are readonly visual and aesthetic evidence.
- Platform execution does not gain candidate-acceptance or canonical-write
  authority.
- Chinese and English SVG/PNG variants remain structurally aligned.

## Tests

- SVG XML and required semantic anchors.
- Chinese/English structural parity.
- PNG 1600 by 1700 dimension check and visual inspection.
- Relative diagram links and architecture firewall.

## Stop conditions

- Stop rather than changing accepted architecture authority semantics.
- Stop after two failures of the same render or validation path.


## Completion

- Completed: 2026-07-29
- Evidence: Bilingual main diagram now exposes one Design Decision as the common source of Semantic Component and Realization Specification, three realization families, neutral GeometryProgram compilation, stable component-geometry-platform receipt mapping, and structured-scene versus screenshot evidence authority; deterministic render, XML/anchor/parity/link/dimension checks, architecture firewall, and devctl tests passed.
