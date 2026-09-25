# GH-284 Design Tree, first product version

Issue: https://github.com/cogco1/MonkeyHub/issues/284
Lane: `design-tree-ui` · Base: `e183a69a` (origin/main).

Kaiwen asked for the Design Tree in the product ("state树呢怎么没看见" / "做吧"). This lane builds its first version in MonkeyHub from the research note (`docs/2026-09-25-candidate-graph-interaction.md`) and the growth-tree prototype (`docs/prototypes/candidate-graph/`).

- **Entry.** A Stage chip in the project workspace header, for example `S2 · Layout — Current · 3 new · 2 running`. It opens the tree wide over the workspace and keeps the chip; closing returns to the previous view. There is no rail entry and nothing inside Drawing.
- **Growth tree.** The Working Head's lineage is one trunk. Options not taken are dashed twigs, and running or queued Agent work is a placeholder where it started. Continuing from an older point makes a new trunk; the abandoned future stays, muted. Layout is planar with no crossings.
- **Density.** Semantic zoom has three levels: far shows the trunk and counts, middle adds letters and short names, close adds a summary and status. Author, time and technical details appear only in the side card of the selected node. A keyboard list shows the same nodes for screen readers.
- **Actions.** View is read-only and never moves the Working Head. Continue uses the existing `PUT /api/working-draft`. Accept as next Stage appears only on Current and uses the existing `POST /api/candidates/{id}/accept`. Compare is marked as later.
- **Canvas.** The Excalidraw setup reused from MonkeyBoard moves into `features/canvas/`: mount defaults, the local font path, wheel zoom, theme mapping and the scene hit-test helper. Board switches to it in the same change and behaves as before.
- **Data.** The tree reads `GET /api/design-history`, `GET /api/working-source` and `GET /api/worktrees`. The #294 `candidates[]`/`studies[]` contract is read through a typed adapter. Until `codex/294-admission-record` lands, tests and screenshots use a typed fixture served through a stubbed API, and a real project without that contract shows Stages, Current and running work only.

Board.tsx was still claimed by GH-66, whose Board change is merged (#273, `2395f8fa`) and whose lane checkout is clean. The claim moves to this lane for the canvas host extraction; GH-66 keeps its Publish paths.
