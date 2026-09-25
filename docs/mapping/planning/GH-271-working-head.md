# GH-271 — Follow the Working Head; show current, frozen and stale work

Issue: [#271](https://github.com/cogco1/MonkeyHub/issues/271) (duplicate [#272](https://github.com/cogco1/MonkeyHub/issues/272)).
Base: `04e719617d6aa20507b5c1846de3059b5c57a451`.

**Rule.** People operate the project; the system manages candidates. Ordinary Modeling,
Drawing, Render and Board work follows the project's **Working Head**, which is the architect's
editing base. Candidate, branch and run choices appear only where a person compares, forks,
freezes, inspects history or resolves concurrent work.

**Adoption (GH-234 Q1/Q2, confirmed by the repository owner on 2026-09-25).** A generated
result, whether from an agent, a proposal or a continuation of the base, is recorded and shown.
It becomes the base only through one explicit Continue, or when the architect adopts their own
Sync. `record_candidate_draft` therefore records results without moving `current`. This replaces
the issue's first draft, in which a continuation became the head by itself.

## Decision: EXTEND, no new store

The Working Head already exists: the P036 working position `design/working.json`
(`current`, `runs`, `active`). The existing `PUT /api/working-draft` is the one explicit
"continue from here"; `record_candidate_draft` only records results. Nothing needed a new record,
branch database or module.

| Need | Owner extended | Result |
| --- | --- | --- |
| One resolver | `studio.binding` (already owns the working position) | `resolve_working_source(binding, workspace, policy)` + `GET /api/working-source` |
| Worktree Graph V0 | `studio.candidate` (already owns read-only runtime inspection) | `worktree_graph(...)` + `GET /api/worktrees`; reconcile status from the existing `combine_component_changes` owner |
| Drawing freshness | `studio.artifacts` (`drawing_plans.py`) | default status target and drawing-driven proposals use the Working Head |
| Render freshness | `studio.render` | retained results are compared with the Working Head |
| Following, labels, project card | `hub.shell` | Modeling follows live; Drawing LIVE by default; Render STALE wording; Hub project card shows status and the graph |

## Audit of manual source selectors (issue §8A)

Class: **a** required explicit choice, **b** should auto-follow the Working Head,
**c** history/compare/recovery only. Line numbers are `main@04e71961`.

### Modeling and Hub

| Control | Where | Class | This slice |
| --- | --- | --- | --- |
| Hub delivery pin: newest completed Studio operation by admission sequence | `web/src/ChatShell.tsx:746-813` | c | Kept: a delivered result opens view-only with Continue (Q1/Q2) |
| Chat result auto-open of the turn's candidate | `ChatShell.tsx:815-828` | c | Same: shown, adopted only by Continue |
| Pin restored from localStorage on cold start | `ChatShell.tsx:94-111, 740` | b | A restored pin follows the editing base, through the same local-edit and busy gate |
| "Open this candidate" (chat result, project card, operations) | `ChatShell.tsx:886, 1022-1030` | c | Kept as explicit view-only comparison; raw id list replaced by Worktree Graph rows |
| "Continue from this version" / "Viewing only" chat gate | `features/stage/Stage.tsx:1144-1160`, `app/App.tsx:3057-3072` | a | The one adoption step for any generated result (Q1) |
| Home fallback "last export listed" | `app/App.tsx:1332-1341` | b | Kept: it applies only when the followed head has no model of its own and names the run it shows |
| Versions "New" badge, branch select, Stage buttons, A/B, combine, accept, fork, save | `features/stage/VersionsStrip.tsx` | a / c | Unchanged; they remain the history/decision entry |
| Editing base only re-read on cold open | `app/useSession.ts:95-121, 186-196` | b | Session follows the server head after candidate events, activation and refresh |

### Drawing

| Control | Where | Class | This slice |
| --- | --- | --- | --- |
| New cut plan target hard-wired to `main` head Stage | `monkeydiagram/DrawingCanvas.tsx:106-113` | b | Target is the head's drawable exact source (accepted or not) |
| Status target = head of the drawing's own Stage branch | `drawing_plans.py:158-169, 202-203` | b | Target is the Working Head; no exact STEP on the head → STALE with reason |
| "Model to draw" select | `DrawingCanvas.tsx:210-214` | b / c | Moved into an explicit "Draw another version" disclosure |
| Revision select defaulting to "New cut plan" | `DrawingCanvas.tsx:207-209` | b / c | Newest drawing opens; earlier revisions are FROZEN views under a disclosure |
| "Rebuild on selected model" | `DrawingCanvas.tsx:223` | b | LIVE drawings rebuild on the head automatically, once per head |
| Drawing-driven dimension change requires branch head | `drawing_plans.py:264-271` | b | Requires the drawing to be on the Working Head |
| Raw run ids in "This drawing's source" | `DrawingCanvas.tsx:219` | — | Removed from the ordinary surface |

### Render

| Control | Where | Class | This slice |
| --- | --- | --- | --- |
| Result freshness = snapshot Stage is still its branch head | `rendering.py:64-86` | b | Compared with the Working Head: a result becomes STALE as soon as the head moves past its source |
| "Use updated source" (replacement links only) | `RenderWorkspace.tsx:133-147` | b | Kept for replacements; a model advance explains how to capture the current view instead of failing |
| Model preview | `render/ModelPreview.tsx` | b | Already follows the Modeling viewer, which now follows the head |
| Source/reference image selects, results list, compare toggles | `RenderWorkspace.tsx:181-198`, `RenderResults.tsx:75-110` | a / c | Unchanged; ids stay behind "generation details" |

### Board / Publish

| Control | Where | Class | This slice |
| --- | --- | --- | --- |
| Automatic page replacement follow | `monkeyboard/Board.tsx:575-645` | b | Already LIVE for replaced pages; no change |
| Sidebar page select / "Update this page" / model link | `Board.tsx:1169-1213`, `DocumentCanvas.tsx:850-863` | a | Unchanged |
| Publish LIVE/FROZEN/STALE per element | not on `main` (PR #270, GH-66) | a | Out of this slice; Publish owns it |

## Acceptance mapping

| Issue scenario | Proof |
| --- | --- |
| 1 Sequential modeling | API tests: a finished result is recorded without moving the base; Continue (explicit select) makes it the head with its lineage. Web decision tests: follow, defer while busy or with local edits; delivered results stay view-only |
| 2 Drawing follows model | API tests: status target is the head, missing STEP reports STALE; web decision tests for one automatic rebuild per head; live Hub check |
| 3 Render follows without rewriting | API test: retained result becomes outdated when the head advances and current again when the position returns; preview follows Modeling |
| 4 Intentional fork | Graph test: a forked branch is its own line; the head stays unambiguous. Resolver test: a result continued on a fork is recorded and continued on the fork, not on main |
| 5 Concurrent non-conflicting | Graph test: second result from the same base is `diverged`, `can-combine`; a combined result contains its inputs, so continuing it leaves no conflict lines |
| 6 Real conflict | Graph test: overlapping writes are `conflict` with the shared refs; nothing merges |

## Result (2026-09-24)

- Server: `GET /api/working-source` and `GET /api/worktrees`; drawing status, drawing-driven design
  changes, render and publication freshness compare with the Working Head. Stage-less cut plans are
  no longer exempt from freshness because the default target no longer needs a Stage.
- Modeling follows the head, the architect's editing base, after activation and refresh, unless
  this tab is busy or holds unsynced edits. A delivered result opens view-only with Continue; a
  restored cold-start pin shows the base. Generated results never move the base (GH-234 Q1/Q2).
- Drawing opens the newest drawing LIVE, rebuilds once per moved head, freezes earlier revisions and
  states a head it cannot draw. A drawing made from a chosen version records `follow: "frozen"`, stays
  on that version after reopening, and offers "Follow the current model again". Each new cut plan is
  its own drawing (`floor-plan`, `floor-plan-2`, ...).
- Render names current/earlier models instead of run ids; "Use updated source" follows a LIVE drawing.
- The Hub project card shows the current project, Modeling/Drawing/Render status (including drawings
  kept on a chosen version and records it could not read), running work and the Worktree Graph rows
  with owners, reconcile state and plain conflicting names.
- Independent review fixes: resolving takes no project guard and publication status is read outside
  the publication lock (a save/read deadlock); lineage follows combined inputs and each run's parents
  are read once per process; the graph skips the head's own history before walking; one request
  resolves the head once for render lists and publication status; the Drawing tab polls
  `GET /api/working-draft/revision` instead of the whole position.
- Checks: Studio API suite 1262 passed / 3 skipped; Hub web 21 + workspace 339 unit tests; Drawing 13,
  Render 13 and Hub chat-shell browser scenarios; generated clients, typecheck, build, archcheck scopes.
- Live Hub on a disposable demo project: two successive headless Agent edits were shown and edited
  without choosing a candidate; a cold browser opened on the head although the Hub's delivery pin
  named a newer concurrent result; three concurrent results appeared as one combinable and two
  conflicting lines with the element names; a visible LIVE cut plan rebuilt itself on a moved head.

## Limits of this slice

- The graph is read-only. Automatic reconcile, human conflict resolution and per-agent
  write-scope declaration beyond the existing job read/write sets remain future work (#229, #32).
- Owner/agent attribution comes from the Hub's operation journal; P036 records no owner.
- Drawing FROZEN is either an earlier revision or a drawing made from a chosen version; Board/Publish
  presentation pins stay with Publish (#66).
- The Worktree Graph's drawing rows use content identity; the Drawing workspace's read-set check can
  still call a drawing current whose inputs did not change.
- Board model-derived pages do not yet show STALE badges.
