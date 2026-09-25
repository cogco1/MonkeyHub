# GH-284 Design Tree, first product version

Issue: https://github.com/cogco1/MonkeyHub/issues/284
Lane: `design-tree-ui` · Base: `e183a69a` (origin/main).

Kaiwen asked for the Design Tree in the product ("state树呢怎么没看见" / "做吧"). This lane builds its first version in MonkeyHub from the research note (`docs/2026-09-25-candidate-graph-interaction.md`) and the growth-tree prototype (`docs/prototypes/candidate-graph/`).

- **Entry.** The owner decided on 2026-09-25 that the rail's primary surfaces are 建模, 画板 and 状态树 ("工作面分为 建模 画板 状态树三个，其他的都放到工具"), relayed by the root task. The Design Tree is therefore a project surface (`view=tree`), the third entry of the rail's first group. The Stage chip in the project workspace header, for example `S2 · Layout — Current · 3 new · 2 running`, stays as the position indicator and also opens the surface. Leaving the tree returns to the previous surface, like the Drawing tool's `returnTo`.
- **Growth tree.** The Working Head's lineage is one trunk. Options not taken are dashed twigs, and running or queued Agent work is a placeholder where it started. Continuing from an older point makes a new trunk; the abandoned future stays, muted. Layout is planar with no crossings.
- **Density.** Semantic zoom has three levels: far shows the trunk and counts, middle adds letters and short names, close adds a summary and status. Author, time and technical details appear only in the side card of the selected node. A keyboard list shows the same nodes for screen readers.
- **Actions.** View is read-only and never moves the Working Head. Continue uses the existing `PUT /api/working-draft`. Accept as next Stage appears only on Current and uses the existing `POST /api/candidates/{id}/accept`. Compare is marked as later.
- **Canvas.** The Excalidraw setup reused from MonkeyBoard moves into `features/canvas/`: mount defaults, the local font path, wheel zoom, theme mapping and the scene hit-test helper. Board switches to it in the same change and behaves as before.
- **Data.** The tree reads `GET /api/design-history`, `GET /api/working-source` and `GET /api/worktrees`. The #294 `candidates[]`/`studies[]` contract is read through a typed adapter. Until `codex/294-admission-record` lands, tests and screenshots use a typed fixture served through a stubbed API, and a real project without that contract shows Stages, Current and running work only.

Overlaps GH-66 on Board.tsx; canvas-host extraction only; coordinated by root task.

The rail entry touches `ChatShell.tsx`, which another lane is regrouping at the same time; the root task merges the two.
