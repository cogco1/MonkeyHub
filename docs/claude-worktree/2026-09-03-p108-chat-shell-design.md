# ArchFlow Studio — Chat Shell design (P108 round 1, web only)

**Status:** approved by Kaiwen 2026-09-03 ("没异议，写 spec 进计划，推进吧"), after two rulings: the shell serves
demo first and daily work second; conclusions stay on screen, evidence lives one click away in a drawer that
never abridges. Implemented by Fable directly (Kaiwen: "用 fable 做"), not dispatched.
Mockup: `claude.ai/code/artifact/45696cca-79de-46b2-a95a-7e8cad5c87de` (design proposal v1).

## 1. Goal

Replace the twelve-panel shell in `apps/archflow-studio/web` with two surfaces: a **conversation** with the
studio and the **model** it is about. The round-1 chain is unchanged — bind → pick → intent → typed proposal →
harness candidate → kernel receipt → server verdict — and every sentence the server says is still shown; the
screen stops saying all of them at once.

## 2. Non-goals

- No API change. Every card is rendered from an existing route (`/api/project`, `/api/state`, `/api/artifacts`,
  `/api/pick/resolve`, `/api/proposals`, `/api/proposals/{id}/candidate`, `/api/jobs/{id}`,
  `/api/candidates/{id}`, `/api/candidates/{id}/validation`, `/api/events`). The generated client is untouched.
- No language model. The "studio" replies are the server's typed answers; an intent provider added later
  slots into the same transcript without changing a card.
- No canonical write, no version history in the browser (a reload loses the view, not the work).
- The viewer is not redesigned: `viewer/ThreeDmViewport.tsx` and `viewer/sceneInspection.ts` are used as they
  are (zero changes). Same three.js + rhino3dm loader, same drag-in / open-file, same SHA-addressed artifact
  download, same picking via `archflow:*` user strings.

## 3. Laws that bind (unchanged; from `.superpowers/sdd/global-constraints.md`)

Browser law: the browser never computes digests, decides verdicts, invents relation states, fabricates
honesty, hides a server sentence, or commits. Three-state law: `held` / `violated` / `unchecked` are shown as
three facts and two flags, never one colour. Ruling of 2026-09-03: a sentence shown in the evidence drawer,
verbatim and one click away with a visible count, is not hidden. Anything that *gates* an action is quoted
inside the card it gates (e.g. the honesty line behind an impact sentence).

## 4. Layout

`AppShell` — a two-column grid, `400px 1fr`, full viewport height; below 900 px it becomes one column with the
model on top (56 vh) and the conversation below.

- **Conversation column** (`features/conversation/`): header (wordmark, project id · HEAD version in mono, a
  `proposal-only` pill), scrolling transcript, composer.
- **Model stage** (`features/stage/`): the viewport filling the column; overlays — top-left *source chip*
  (`RUN` / `CANDIDATE` / `LOCAL` + file name + object count), top-right *view tools* (fit, front, clear, open
  .3dm), a hover tip when the viewer reports a hovered/picked object, a bottom *versions strip* (one card per
  available model: the reference run's exports, each candidate's exports), and the *evidence tab* at bottom
  right (`Evidence · honesty n · receipts n · events n`).
- **Evidence drawer** (`features/evidence/`): slides over the stage's right edge (380 px), three tabs —
  Honesty, Receipts, Events — and a pin control; pinned, it becomes a third column (`400px 1fr 380px`) and
  stays open across actions ("work mode"). Closed by default ("demo mode"). Its state lives in
  `localStorage` (`archflow-studio.evidence.pinned`), read in a try/catch.

## 5. The transcript

An append-only list of entries kept in React state for this tab (`useTranscript`). Entry kinds:

| kind | source | rendering |
|---|---|---|
| `system` | binding, pick resolution, re-projection notice, reconnect | one grey centred line, server words verbatim |
| `you` | the utterance sent | right-aligned bubble |
| `proposal` | `ProposalDto` | card: title "Set `key` on element", delta `old → new` + unit sentence, kv rows (component/element, protected, impact sentence with a `why` link that opens Evidence → Honesty), a `Run candidate` button and the harness sentence beside it |
| `question` | `StudioApiError` with code `BLOCKED_NEEDS_HUMAN` | card "A question first": the `question` verbatim, `acceptedForms` as quick-reply chips |
| `refusal` | any other `StudioApiError` | card: code, detail verbatim, the route (existing `ErrorPanel` content) |
| `candidate` | `CandidateAcceptedDto` → polled `JobDto` → `CandidateDto` | card: status line (queued/running/succeeded/failed by the server's word), wall time, seat rows summary "N seats accepted · a objects + b objects", exported models as buttons "Preview <fileName>" (each becomes a version card too), `receipts` link → Evidence → Receipts; a failed job shows `job.error` verbatim |
| `verdict` | `ValidationDto` | card: the word — `May advance` when `advance`, otherwise `Blocked` — followed by the receipt line (`passed`, `effectiveChecks`), five clause chips from `blockedBy` (a clause present in `blockedBy` is drawn as refused), the three relation chips (existing `RelationChips`), honesty lines verbatim if any, `Read the receipt` link |

Rules: one entry per server answer; entries are never edited after the fact except the candidate card, which
updates in place while its job runs (same `candidateId`). The transcript auto-scrolls to the newest entry
unless the user has scrolled up.

## 6. Selection and the composer

- Selection = the server's resolution. A pick in the viewer posts `/api/pick/resolve` as today; on
  `resolved` the context chip becomes `component · element`, and a `system` line records it. Picking on a
  local file (no receipt) keeps today's honest behaviour: the resolution status is printed as it came.
- The chip's `change` control opens a searchable list built from `projection.components` (and each
  component's elements) — the component tree, folded into a picker. Choosing there sets the selection without a
  server call (it is the projection's own ids).
- Composer: one text input whose placeholder speaks the architect's language ("make the west portico a
  little taller · open up the entry · keep the roofline" — Kaiwen 2026-09-03: requests are abstract, not
  `set height to 2200 mm`), a `Propose` button, and a hint that says the round-1 studio types four exact
  forms and answers anything else with a question naming the field and the number it needs. Disabled with a
  reason when nothing is selected or the projection is not loaded. Round 2 (carded): an intent provider that
  turns the abstract sentence into the typed proposal, with the same question when the record lacks what it
  needs.
- `Propose` posts `/api/proposals` with `stateDigest`, `targetComponentId`, `elementId`, `utterance`,
  `projectId` (unchanged request). The reply becomes a `proposal` card; `BLOCKED_NEEDS_HUMAN` a `question`
  card; other errors a `refusal` card. `STALE_BASE` additionally re-projects (existing `useSession`).

## 7. The model stage

- `ThreeDmViewport` unchanged; `ViewerPanel`'s chrome is replaced by the stage overlays.
- Source chip: `sourceLabel` from the viewer (`LOCAL_SOURCE_LABEL` or the label the shell passed:
  `RUN · <run id>` / `CANDIDATE · <candidate id>`), file name and `objectCount` from the inspection.
- Versions strip: built from `/api/artifacts` (`ProjectArtifactDto`, grouped by `runId`) — the reference
  run's exports first, then candidates launched in this tab; a card shows label (`Reference · HEAD vN` /
  `Candidate`), file name, `receipt`/verdict word when known; click = `loadArtifactIntoViewer` with the
  matching label (existing function). Unavailable artifacts are shown with `unavailableReason`, not hidden.
- Hover tip: element name and, when the pick resolved, the element's `numericFields` from the projection
  (e.g. `height 1.873`, no unit — the wire carries none for element fields) — projection facts, never
  computed or given a unit the record did not declare.
- The `loadedArtifact` / `pendingArtifact` discipline (T9-R1) stays exactly as in `App.tsx`.

## 8. Evidence drawer content

- **Honesty:** `projection.honesty` (label "Record honesty · projection"), the current candidate's `honesty`
  and the verdict's `honesty`, each under its own label; Identities: `stateDigest`, `recordDigest`, `head`
  version + `stateSha256`, reference run id + `receiptRef` + `matchesReferenceReceipt`; the validation note
  (`validatorNote`, `canonicalFacts`). The tab count = number of honesty lines currently held.
- **Receipts:** for the selected candidate: `candidateId`, `receiptRef`, seat rows (`seatId`, `status`,
  `objects`, `programRef`), artifacts (`fileName`, `status`, `available`, `sha256`), validation
  (`receiptId`, `passed`, `findings` verbatim, validators, effective checks, all five clause names with their
  state). Tab count = candidates in this tab.
- **Events:** the existing `EventStream` component moved here unchanged (catch-up, reconnect notice, gap
  detection). Tab count = lines held.
- Every string is the DTO's own; the drawer formats, it never rewrites.

## 9. Errors and edge states

- Session failed (`/api/project` or `/api/state` refused): the conversation shows one `refusal` card at the
  top and the composer is disabled with the code; the stage shows the empty viewer with "open .3dm" still
  available (a local file can be inspected without a binding, as today).
- `componentTreeError` (projection served without a tree): the picker lists elements/components as served;
  the honesty line the server added is in the drawer and the composer hint says proposals will be refused.
- A candidate whose job failed: the card shows `job.error`; no verdict is requested (server word gating, as
  landed in the final fix wave).
- `ErrorBoundary`s stay: one around the shell, one around the viewport.

## 10. Visual system (Kaiwen 2026-09-03: "参考 Unreal，稍微行业化一点")

Dark-first, in the register of the Unreal Editor: charcoal panels separated by 1 px hairlines, 3 px radii,
flat controls, a slim top toolbar across the full width, one cool accent for selection and the primary
action, and numbers in a monospace. Light theme provided from the same tokens (three-state theme handling).

Tokens (dark / light): ground `#1b1b1b` / `#e9e9e9`, panel `#242424` / `#f5f5f5`, panel-2 `#2c2c2c` /
`#ececec`, ink `#e4e4e4` / `#1e1e1e`, ink-2 `#c8c8c8` / `#3a3a3a`, muted `#9b9b9b` / `#5f5f5f`, faint
`#6f6f6f` / `#8a8a8a`, line `#3a3a3a` / `#cfcfcf`, line-2 `#2f2f2f` / `#dedede`, accent `#2f80ed` /
`#1f6fd1` (accent-ink `#ffffff`), held `#58b368` / `#2e7d32`, violated `#e5534b` / `#c62828`, unchecked
`#e0a03a` / `#b26a00`, viewport `#202020` / `#e4e4e4`, grid major `#3d3d3d` / `#c9c9c9`, grid minor
`#2e2e2e` / `#d9d9d9`.
Type: Roboto (UI) and Roboto Mono (ids, digests, numbers, seat rows), from Google Fonts with system
fallbacks; 13 px body, 11 px uppercase labels with 0.06 em tracking. The accent is used only for the primary
action, the selection and the active tab; the three relation colours are semantic and never decorative.
Top toolbar (36 px, full width): wordmark, project id, `HEAD vN`, the `proposal-only` pill, and the
evidence toggle at the right; the conversation column and the stage sit under it.
Shell copy is English; the viewer's existing Chinese strings are switched to English in the same pass.
The viewer's two exceptions to "unchanged": (1) strings only; (2) the three.js scene background and grid
colours are read from the CSS tokens `--viewport`, `--grid-major`, `--grid-minor` at mount and on theme
change, so the canvas belongs to the theme — colours only, no behaviour.

## 11. What is removed

`features/state/ComponentTree.tsx`, `SelectionPanel.tsx`, `HonestyLines.tsx` (content moves to the drawer),
`features/pick/PickPanel.tsx`, `features/intent/IntentPanel.tsx` (becomes the composer),
`features/proposal/ProposalPanel.tsx` + `features/impact/ImpactPanel.tsx` (become the proposal card),
`features/candidate/ReviewPanel.tsx` (the Run button lives on the proposal card), `CandidateRuns.tsx` (the
versions strip and the candidate cards replace it), `features/project/TopBar.tsx` (the header line),
`app/Shell.tsx` (`AppShell`), the `Panel` wrapper, and every CSS rule that only they used. `ErrorPanel`,
`RelationChips`, `EventStream`, `ArtifactList`'s helpers (`canonicalSourceLabel`, `receiptDocumentStrings`),
`CandidatePanel`'s polling logic, `loadable.ts`, `jobs.ts`, `format.ts`, `useSession.ts`, `ErrorBoundary`
are kept (some moved).

## 12a. The intent compiler (added 2026-09-03 on Kaiwen's ruling: the seam is an agent, not a grammar)

`POST /api/intents` replaces `POST /api/proposals` as the composer's route. The process's **intent
compiler** (`ARCHFLOW_STUDIO_INTENT_PROVIDER` = `deterministic` | `codex` | `anthropic`; both agent
backends are wired, env-selected) is shown the **record sheet** — components, elements with numeric
fields, parameters, honesty lines, the four grammar forms — and answers with one JSON object (`status`
compiled | question, `targetComponentId`, `elementId`, `utterance`, `why`, `question`). A compiled sentence
must parse in the grammar and is then proposed through the deterministic seam unchanged; an agent's
question, or an agent's sentence the grammar cannot type, is the same `422 BLOCKED_NEEDS_HUMAN` with the
agent's reading in `detail`; an agent that fails is `502 INTENT_AGENT_FAILED`. A sentence already in the
grammar never reaches the agent. The answer carries `agent` (provider, model, compiled sentence, `why`,
latency, prompt sha256) beside `proposal`. On screen the agent's reading sits above the proposed-change
card as the agent's ("Read by codex · 45.1 s — …"), never as the record's. The codex backend runs
`codex exec --ephemeral --skip-git-repo-check --ignore-user-config -s read-only` in a temp directory with
`--output-schema`; the Anthropic backend reads its key from the SDK's environment and this process never
holds it.

## 12b. Design-review language (Kaiwen's product feedback, 2026-09-03)

Cards speak to an architect: **Proposed change** — Change / Will update / Keep / [Apply] [Adjust]
(Apply runs the candidate, Adjust puts the compiled sentence back in the composer); **Is it safe?** —
Geometry / Dependencies / Receipt / Unresolved, each ✓ or △ on the server's own clause names, the three
relation chips kept; the evidence tab says `N changes · M checked · K need review`; the toolbar shows
`HEAD vN` without the digest (the digest is in Evidence → Identities); the picked chip names the element
and its fields, and keeps the resolution status and source state in a tooltip and the system line. Carded
for the next spec: Before / After / Why per component with a before ↔ after compare; parallel candidates
with dependency-aware queueing shown as behaviour, never as a graph; folding the versions strip per run.

## 12. Testing and acceptance

- `npm run api:check` unchanged (no schema change), `npm run typecheck`, `npm run build` green.
- Live smoke in the Browser pane against a temp villa copy with export on: bind → pick in the loaded model →
  `set height to 2.2` → proposal card → Run → candidate card updates → Preview loads the candidate export
  with the `CANDIDATE` chip → verdict card `May advance` with five clauses and three relation chips →
  `make the west portico a little taller` → question card naming the field and number it needs; Evidence
  drawer shows every honesty line and the receipt; dark theme and the 900 px fold checked.
- Acceptance is Kaiwen's read of the live shell against this document.
