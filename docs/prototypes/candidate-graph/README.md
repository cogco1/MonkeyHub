# Candidate Graph: dev prototype (#284 section E)

**DEV PROTOTYPE · fixture data · not production.** This folder is a static
interaction study for the project-level Design Tree in
[#284](https://github.com/cogco1/MonkeyHub/issues/284). It uses the admitted-Candidate
semantics of [#294](https://github.com/cogco1/MonkeyHub/issues/294) and the rail
hierarchy of [#295](https://github.com/cogco1/MonkeyHub/issues/295). Nothing
here is imported by MonkeyHub. It calls no runtime and writes nothing.

The chain it makes operable:

```text
Stage → Study → admitted Candidates → Continue → next Stage
```

## Open it

Open `index.html` directly in a browser (`file://`). It needs no build step,
no server and no network. The page loads only `prototype.css`, `prototype.js`
and `data/*.js` from this folder.

Hash parameters:

| Parameter | Values | Default |
| --- | --- | --- |
| `#state=` | `1` … `6`, the review states below | `1` |
| `#theme=` | `system`, `light`, `dark` | `system` |
| `#data=` | a dataset name; the page loads `data/<name>.js` | `issue-fixture` |
| `#view=` | `tree` opens the Design Tree as the growth tree; `list` opens the list | `list` |

Example: `index.html#state=3&theme=dark`. The amber dev bar at the top switches
states, theme and dataset. It also simulates the Agent events for state 6. The
bar is not product UI.

## The six states

Every state opens from its hash. Each can also be reached through the product
UI, and `capture.mjs` walks both routes.

| # | State | Reached in the UI by | Screenshot |
| --- | --- | --- | --- |
| 1 | Normal project, tree closed. The project bar shows `S2 · Layout · Current` with compact badges (`1 alternative ready`, `1 task running`). | Closing the tree (×, Esc) | `screenshots/01-closed.png` |
| 2 | Tree open: the Stage spine plus the five S0 Candidates in `Massing Study · 5 options`. D is selected to show its five distinct actions. | The Stage chip, or `5 schemes ready → Show in Design Tree` on the chat result card | `screenshots/02-tree-open.png` |
| 3 | Compare: 4 of the 5 Massing Candidates side by side with the same camera. D is chosen. The offer is `Continue from here`, and nothing is accepted. | Tick 2–5 boxes in one Study, then `Compare N`. With nothing ticked, `Compare 5` compares all. | `screenshots/03-compare.png` |
| 4 | Old Stage, read-only: `Viewing S0 · read-only · Continue from here`. The Working Head stays at S2. | Select S0, then `View read-only` | `screenshots/04-old-stage-readonly.png` |
| 5 | Entrance A continued into the current line. The line is highlighted, and `Accept as next Stage` is a separate, now-enabled step. | Select Entrance A, then `Continue from here` | `screenshots/05-continued.png` |
| 6 | Agent worktrees under a Study: A ready, B `Agent working…`, C queued. B's Advanced details are open to show its runs. | The `1 task running` badge | `screenshots/06-running.png` |

Also captured: `screenshots/07-narrow.png` (390×844, state 2). At that width the
tree is a full-height sheet under the dev bar. `screenshots/08-dark-tree-open.png`
shows state 2 in the dark theme.

Screenshots 01–08 record the list view as reviewed in PR #298, so they are kept
byte-identical. Since then the list rows show only a thumbnail, letter, name
and one status dot; the summary, author and time appear on the selected row
only (`screenshots/12-list-compact.png`, state 2). The drawer header also has
the `List | Growth tree` toggle.

## Growth tree

`List | Growth tree` in the Design Tree header, or `#view=tree`, opens the tree
as one growing branch. The canvas opens wide over the workspace, the way
Compare does; the project bar and its chip stay. View or Compare narrows the
canvas back to the drawer column and shows the result in the workspace.

- **The trunk** is the Working Head's lineage, one continuous stroke from left
  to right: Stage milestones, the option chosen at each decision, lines of
  continued edits, and the Current tip.
- **Twigs** are the options not taken. At each decision point they alternate
  above and below the trunk: N − 1 per Study, or N when none was continued.
  Running and queued Agent worktrees are dashed placeholders among them.
- **Earlier lines.** Continuing from an old twig makes it the trunk again; the
  future left behind stays on the canvas as a muted branch. So does a line
  continued once from a twig and later left (Facade A + 2 edits).
- **No crossings.** A tree has E = V − 1 edges. Every side subtree gets its own
  x-range on its side of its parent path, and each branch grows its own twigs
  outward, so no edge crosses another and no two nodes overlap.
- **Five element kinds**: trunk, Stage milestones, option twigs (thumbnail and
  letter), the Current tip with `Accept as next Stage`, and running or queued
  placeholders.
- **Semantic zoom.** Zoomed out (below 50 %): the trunk, Stage labels and counts
  such as "5 schemes · 1 continued". Middle: thumbnails and short names.
  Close (125 % and above): one-line summary and status.
- **Details on selection only.** Selecting a node opens a small side card with
  author, time and runs, and the actions View · Compare this Study · Continue
  from here. `Accept as next Stage` exists only on Current.
- **Navigation** follows MonkeyBoard's canvas (Excalidraw 0.18.1 in the product):
  wheel zoom anchored at the cursor, drag, Space-drag or middle-mouse pan, the
  `− 100% +` bar with Fit, the hint "Wheel to zoom · Space or middle mouse to
  pan", and the same 20-unit grid with 5-step major lines. This static page
  emulates that behaviour; it does not load Excalidraw.

| Screenshot | Shows |
| --- | --- |
| `screenshots/09-growth-tree.png` | Fit, with Massing D selected: its side card, runs and actions |
| `screenshots/10-growth-tree-close.png` | Wheel-zoomed near the Current tip: summaries and status |
| `screenshots/11-growth-tree-overview.png` | Zoomed out: trunk, Stage labels and counts only |
| `screenshots/12-list-compact.png` | The compact list rows (state 2) |

Other things to try in the UI:

- `Continue from here` on S0 or on an older Candidate. This starts a new line.
  S1 and S2 stay in the tree, dimmed and marked "Not on the current line · kept
  in history".
- `Accept as next Stage` after a Continue. It asks for confirmation, then adds
  S3 to the spine in page memory only.
- In state 6, the dev bar's `B admitted` and `C rejected before admission`
  buttons. B becomes a Candidate. C never becomes a node; its rejection appears
  only in the Study's Advanced details.
- `Axon` / `Plan` switch every preview and compare tile together, which keeps
  the framing identical.
- The rail's Drawing and Monitor Tools. The project bar and the Design Tree
  entry stay put, because the entry is project-level.

## Anatomy and #294 semantics

| Element | Meaning | Rule encoded |
| --- | --- | --- |
| Stage node (`S0`, `S1`, `S2`) on the spine | An accepted, immutable checkpoint | Shows name, summary, accepted time and actor, and a preview. A Stage off the current line is dimmed, never removed. |
| Study group (`Massing Study · 5 options`) | The results of one request from one exact base | Candidates are grouped by task, not by time. The group has its own `Compare N`. |
| Candidate row (status `ready`) | An **admitted** design result | Only admitted Candidates are nodes. They are the only rows with preview, compare checkbox and Candidate actions. |
| Dashed row (`Agent working…`, `Queued`) | An Agent worktree that has not reached admission | Not a Candidate: no compare, Continue or Accept. It reads "not a Candidate until admitted". |
| Advanced details (collapsed) | Base revision, task, worktree, scope, admission, runs | Runs, repairs, redo attempts, failed runs and results rejected before admission appear only here. Raw ids and hashes appear only here. |
| Earlier line (dashed, dimmed) | A retained line that is no longer current | Kept, viewable, continuable. Never deleted. |
| `Current · Working Head` node and the spine strip | The default editing base | The accent line runs from the root to the Working Head. Opening the project follows it. |
| Top-bar Stage chip and badges | The closed-state indicator | Counts come from the data: ready Candidates on the current Stage that are not on the current line, and running worktrees. |

Actions on a Candidate stay separate:

- **View** is read-only. The workspace shows the result with a banner, and the
  Working Head does not move.
- **Compare** takes 2–5 Candidates from one Study. The tree keeps the
  selection while Compare is open.
- **Continue from here** moves the Working Head. The node records who continued
  it, for example "Continued by you" or "Continued by Arch Agent at your
  request".
- **Accept as next Stage** stays disabled until the Candidate is the Working
  Head, and it asks for confirmation. Continue never implies it.
- **Reject / Archive** is present but disabled, marked "needs #289".

Endorsement (#290) and stale or conflict states are not shown.

## Fixture versus real

**Fixture, invented for this study:** the Riverside Library project and all of
its Stages, Studies, Candidates, lines, times, actors, metrics, run lists,
admission texts and chat. The previews are box sketches drawn from
`shapes` in the dataset: one site, one camera. They are not project geometry.
Accepting a Stage, continuing, and the simulated admission and rejection
change page memory only.

**Taken from the product:**

- Colour, type, radius and shadow tokens are copied from
  `apps/shared-web/src/base.css` (Workshop Graphite with Drafting Blue, light
  and dark).
- Shell geometry follows `apps/monkeyhub/web/src/ChatShell.css`: sidebar,
  chat, resizer, tool panel, rail.
- Rail icons are the `Icon` paths in `ChatShell.tsx`.
- The rail groups follow #295: Surfaces holds Arch and Diagram; Tools holds
  Drawing and Monitor.

**Where each element would come from:**

| Prototype element | Real source today | Gap |
| --- | --- | --- |
| Working Head, its lineage, "Current" | #277 resolver: `WorkingHead` and `lineage_of` in `apps/archflow-studio/api/archflow_studio_api/application/working_draft.py`. Served as `head` of `GET /working-source` and of `GET /worktrees` (`WorktreeGraphDto.head.lineage`). The saved working position behind it is `/working-draft`. | None for the head itself. |
| Stage spine, accepted time and actor | Design history: `application/design_history.py` (`DesignStage`, `StageView`, acceptance attribution) over `archflow.state.design_portfolio`, served by `GET /design-history`. | The Stage's `parent` (the Candidate it came from) must be read from the Stage's model run lineage. |
| Running and queued worktrees | `WorktreeGraphDto.lines` with `kind: "running"`, from the job registry in `application/runtime.py`. | A queued state per Study option. |
| Earlier lines | `WorktreeGraphDto.lines` with `kind: "branch"`, plus retained working positions. | — |
| Study group | No first-class identity yet (#284). Could be derived from same exact base plus same parent task or operation (`baseRunId`, the Hub operation journal), or from a declared exploration group. | Needs a stable grouping fact. When grouping is unsure, list the Candidates separately under the Stage. |
| Candidate (admitted) | **Does not exist yet.** Today `record_candidate_draft` (`working_draft.py`) and `application/candidate.py` make every run candidate-like. That is the legacy behaviour #294 removes. | The future admission record (`CandidateAdmission@1` or equivalent, #294). The tree must read only admitted Candidates from retained project facts, never from `working.json`, timestamps or recovery lists. |
| Rejected before admission | The same future admission record, with a rejected outcome (#294). | — |
| "Continued by …" actor | The working-position change and the Hub operation journal (`ownerOf` in `apps/monkeyhub/web/src/worktreeGraph.ts` already attributes requests). | The actor must be retained on the continuation, not only in chat. |
| Reject / Archive | #289 | Disabled here. |

## Datasets

Fixture data is kept apart from rendering. A dataset is `data/<name>.js`, which
assigns one object to `window.CANDIDATE_GRAPH_DATA`. List it in
`data/datasets.js` so the dev bar offers it. `#data=<name>` loads it, and a
missing parameter loads `issue-fixture`. Datasets load as plain scripts so the
page keeps working over `file://`.

The shape, as used by `data/issue-fixture.js`:

- `project`: `{ name, chatTitle, threads[] }`
- `stages[]`: `{ id, label, name, summary, acceptedAt, acceptedBy, preview, parent, fromNote?, advanced? }`.
  `parent` is the Candidate or line the Stage was accepted from, or `null`.
- `studies[]`: `{ id, stage, base?, name, noun?, ask, askedBy, agent, createdAt, items[], hidden[], advanced? }`.
  `base` is where the Study started when that is not a Stage: a Candidate or a
  line, such as an unaccepted continued result. Its options then hang from
  `base`, labels read "from <base>", and `stage` is still the Stage the list
  shows it under.
  - `items[]`: `{ id, letter?, name, summary, status: "ready" | "working" | "queued", by, at, preview, metrics?, continued?, progress?, admitted?, advanced? }`.
    Only `ready` items are Candidates. `continued` is `{ by: "you" | "<actor>", onRequest?, at }`.
    For a `working` item, `admitted` holds what the item becomes when the dev
    bar simulates admission.
  - `hidden[]`: `{ kind: "rejected" | "failed" | "redo" | "superseded", name, note, at }`.
    These are not Candidates and are shown only in Advanced details.
- `lines[]`: `{ id, parent, name, summary, by, at, preview }`
- `head`: `{ parent }`, the Working Head's node.
- `chat[]`: `{ kind: "day" | "user" | "agent" | "event", text }` or `{ kind: "result", study }`.
- `site`, `shapes`: optional box-sketch geometry. A `preview` may instead be `{ src: "relative/image.png" }`, or `null`.
- `states`: optional presets for `#state=1..6`. Each preset may set `tree`,
  `focus`, `select`, `view`, `compare`, `checked`, `open`, `expand`, `scroll`
  and `continue`.

Names are arbitrary UTF-8 strings, for example `D 房架之家`.

## Screenshots and checks

```text
node docs/prototypes/candidate-graph/capture.mjs            # writes 09–12, runs every check
node docs/prototypes/candidate-graph/capture.mjs --states   # also rewrites 01–08
```

The script uses Playwright from `PLAYWRIGHT_MODULE`, which defaults to
`D:/MONKEYHUB_DEV/cache/headless-tests/node_modules/playwright/index.mjs`, and
installed Chrome (`channel: "chrome"`). It opens `index.html` through `file://`,
checks each state's defining elements, runs all six states again in the dark
theme and walks the states through the UI. For the growth tree it checks that
the trunk is one continuous left-to-right path through the lineage, that each
Study shows N − 1 twigs off the trunk (N when none was continued), and that
no edges cross and no nodes overlap, before and after a fork and an Accept.
It writes `screenshots/09`–`12`; `01`–`08` are rewritten only with `--states`.
It exits non-zero on any console error, page error, failed load or non-file
request.

## Limits

- This is a proposal surface, not an implementation. The research note, the
  entry-point comparison and the recommendation belong to the #284 design note
  in `docs/`.
- The Modeling viewport is a placeholder. Compare shows sketches with one
  shared camera, not synchronized 3D viewports or a full ReviewContext (#271).
- Only the English copy exists. Medium widths overlay the tree on the chat
  rather than being tuned layouts.
- The growth tree emulates MonkeyBoard's canvas in plain SVG. Real Excalidraw
  reuse is being tested separately.
