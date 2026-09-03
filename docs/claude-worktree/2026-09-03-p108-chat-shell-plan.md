# ArchFlow Studio Chat Shell — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (Kaiwen: Fable implements this one in-session, not dispatched). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the twelve-panel shell in `apps/archflow-studio/web` with a conversation column beside the model, an evidence drawer, and nothing else — per `2026-09-03-p108-chat-shell-design.md`.

**Architecture:** `App.tsx` keeps every server-facing handler it has today (session, artifacts, viewer hand-off, pick, propose, run candidate) and adds a transcript reducer; the panels become cards rendered from the same DTOs; the viewer (`ThreeDmViewport`) is untouched except for its user-facing strings; the drawer holds everything verbatim that the cards do not quote.

**Tech Stack:** React 19, TypeScript 7 (typecheck) / Vite 8, generated client (unchanged), CSS custom properties, Google Fonts (Instrument Sans, IBM Plex Mono).

## Global Constraints

- API unchanged; `npm run api:check` must keep reporting "16 generated files match the current schema".
- `viewer/ThreeDmViewport.tsx` and `viewer/sceneInspection.ts`: behaviour unchanged; only the seven Chinese user-facing strings in `ThreeDmViewport.tsx` become English (this plan lists them).
- Browser law and three-state law (spec §3). No hard-coded fact the server did not send; the only two client-side lists are the five clause names (mirror of the DTO description, used for chip order) and the event names (existing mirror in `EventStream`).
- Evidence ruling: drawer content verbatim, one click away, counts on the tab.
- Write scope: `apps/archflow-studio/web/**` (not `src/api/generated/**`), `apps/archflow-studio/README.md`. Explicit-path commits with the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Never touch the real villa project; smoke on a temp copy.
- Battery after every task: `npm run typecheck`; after Tasks 6–8: `npm run build`, `npm run api:check`; final: live smoke (spec §12).

---

## File structure (final)

```
web/index.html                       lang="en", fonts link, description
web/src/main.tsx                     unchanged (boundary + App)
web/src/styles.css                   rewritten: tokens, shell, conversation, cards, stage, drawer, viewer classes kept
web/src/app/App.tsx                  composition + handlers (rewritten)
web/src/app/AppShell.tsx             grid: conversation | stage (| drawer when pinned)
web/src/app/transcript.ts            Entry union + useTranscript()
web/src/app/{ErrorBoundary,ErrorPanel,RelationChips,format,jobs,loadable,useSession}.ts(x)  kept
web/src/features/conversation/Conversation.tsx   header + transcript list + composer
web/src/features/conversation/Composer.tsx       input, context chip, picker toggle
web/src/features/conversation/SelectionPicker.tsx searchable component/element list from the projection
web/src/features/conversation/cards/ProposalCard.tsx
web/src/features/conversation/cards/QuestionCard.tsx
web/src/features/conversation/cards/RefusalCard.tsx
web/src/features/conversation/cards/CandidateCard.tsx  (polling from today's CandidatePanel)
web/src/features/conversation/cards/VerdictCard.tsx     (reading from today's ValidationPanel)
web/src/features/conversation/cards/SystemLine.tsx
web/src/features/stage/Stage.tsx                 viewport + overlays
web/src/features/stage/SourceChip.tsx
web/src/features/stage/VersionsStrip.tsx
web/src/features/evidence/EvidenceDrawer.tsx     tabs + pin + counts
web/src/features/evidence/HonestyTab.tsx
web/src/features/evidence/ReceiptsTab.tsx
web/src/features/events/EventStream.tsx          kept, rendered inside the Events tab
web/src/features/artifacts/artifactLabels.ts     canonicalSourceLabel/candidateSourceLabel/receiptDocumentStrings (moved out of ArtifactList)
web/src/viewer/ThreeDmViewport.tsx               strings only
web/src/viewer/sceneInspection.ts                unchanged
removed: app/Shell.tsx, features/state/*, features/pick/*, features/intent/*, features/proposal/*, features/impact/*,
         features/candidate/{ReviewPanel,CandidateRuns,CandidatePanel}.tsx, features/validation/ValidationPanel.tsx,
         features/project/TopBar.tsx, features/artifacts/ArtifactList.tsx, viewer/ViewerPanel.tsx
```

---

### Task 1: Tokens, fonts, viewer strings

**Files:** Modify `web/index.html`, `web/src/styles.css` (top block only in this task), `web/src/viewer/ThreeDmViewport.tsx` (strings).

- [ ] **Step 1: index.html** — `lang="en"`; remove the language-split comment; add
  `<link rel="preconnect" href="https://fonts.googleapis.com">` and
  `<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&family=Roboto+Mono:wght@400;500&display=swap">`.
- [ ] **Step 2: tokens** — replace the `:root` block in `styles.css` with the three-state token set, **dark on
  `:root`** (dark-first), light under `@media (prefers-color-scheme: light) { :root:not([data-theme="dark"]) {…} }`
  and `:root[data-theme="light"] {…}`: ground/panel/panel-2/ink/ink-2/muted/faint/line/line-2/accent/
  accent-ink/accent-soft/held/violated/unchecked/viewport/grid-major/grid-minor/shadow/font-ui/font-mono,
  values from spec §10; `body { font: 13px/1.5 var(--font-ui); background: var(--ground); color: var(--ink); overflow: hidden; }`.
- [ ] **Step 2b: viewer colours from tokens** — in `ThreeDmViewport.tsx`'s mount effect replace the literal
  `scene.background = new Color(0xf3f1ec)` and `new GridHelper(40, 40, 0xa6a29a, 0xd8d4cc)` with values read
  by a small helper `themeColours()` that does `getComputedStyle(document.documentElement).getPropertyValue("--viewport" | "--grid-major" | "--grid-minor").trim()`
  (fallbacks `#202020`, `#3d3d3d`, `#2e2e2e`); re-read and re-render on `matchMedia("(prefers-color-scheme: dark)")`
  change and on a `data-theme` attribute mutation of `<html>` (one `MutationObserver`), disconnected in the
  cleanup. Nothing else in the file changes.
- [ ] **Step 3: viewer strings** (only these, in `ThreeDmViewport.tsx`): `"无法解析这个 3DM 文件。"` → `"This 3DM file could not be parsed."`; `"拖入 .3dm，或从本机选择文件"` (×3) → `"Drop a .3dm here, or open one from this machine"`; `"3D 视口尚未初始化。请稍后重试。"` → `"The 3D viewport is not ready yet; try again in a moment."`; `"仅支持 Rhino .3dm 文件。文件没有上传。"` → `"Only Rhino .3dm files are supported. The file was not opened."`; `"文件为空或超过首版 512 MB 的本地解析上限。"` → `"The file is empty or over the 512 MB local parse limit."`; `` `正在本地解析 ${file.name}` `` → `` `Parsing ${file.name} locally` ``; `` `${file.name} 已打开，但没有发现可显示网格` `` → `` `${file.name} opened, but it holds no displayable mesh` ``; `"3DM 模型视口"` → `"3DM model viewport"`; `选择 3DM` → `Open .3dm`; `释放以在本地打开` → `Release to open locally`.
- [ ] **Step 4:** `npm run typecheck`. Commit `P108 chat shell: tokens, fonts, English viewer strings`.

### Task 2: The transcript

**Files:** Create `web/src/app/transcript.ts`.

**Interfaces (produced):**
```ts
export type Entry =
  | { kind: "system"; id: string; text: string }
  | { kind: "you"; id: string; text: string }
  | { kind: "proposal"; id: string; proposal: ProposalDto }
  | { kind: "question"; id: string; error: StudioApiError; utterance: string }
  | { kind: "refusal"; id: string; error: StudioApiError; what: string }
  | { kind: "candidate"; id: string; candidateId: string; jobId: string; proposalId: string; status: string }
  | { kind: "verdict"; id: string; candidateId: string };
export function useTranscript(): {
  entries: readonly Entry[];
  append(entry: Omit<Entry, "id">): string;          // returns the id
  noteJobStatus(candidateId: string, status: string): void;  // updates the candidate entry in place
  hasVerdictFor(candidateId: string): boolean;
}
```
- [ ] **Step 1:** implement with `useState<readonly Entry[]>` and a `useRef` counter for ids (`e1`, `e2`, …). `noteJobStatus` maps entries, replacing only the matching candidate entry when the status changed (same shape as today's `App.noteJobStatus`).
- [ ] **Step 2:** `npm run typecheck`. Commit `P108 chat shell: transcript state`.

### Task 3: Cards

**Files:** Create the seven files under `features/conversation/cards/`; create `features/artifacts/artifactLabels.ts` (move the three helpers verbatim from `ArtifactList.tsx`, update the one import in `App.tsx` later).

Shared rules: every string comes from a DTO or the error; the code/detail/question are verbatim; buttons are `className="btn"` / `"btn btn--primary"` / `"btn btn--link"`; cards are `<article className="card">` with `card__row` sections.

- [ ] **ProposalCard** (`proposal: ProposalDto`, `busy`, `onRun()`):
  title `Set <span class="mono">{target.key}</span> on {target.elementId ?? target.componentId}`;
  delta line `{change.old} → <b>{change.new}</b>` followed by `change.unit ?? "in the record's own units"`;
  `<dl class="kv">` rows: `component` (`target.componentId` + ` · element ` + elementId when present + ` · ` + `target.ref` in mono), `protected` (`protected.join(", ")` or `nothing was named`), `impact` (`impact.direct.length` direct, `impact.propagated.length` propagated, `impact.conflicts.length` conflicts; `unknownCoverage.count` components with unknown coverage; then a `why` link when `impact.honesty.length > 0` that calls `onEvidence("honesty")` — the honesty lines themselves are ALSO rendered under the row in a `verbatim` block because they gate the sentence);
  status row when `status === "conflict"`: `Conflict: {impact.conflicts.join(", ")}` (verbatim refs);
  actions row: `Run candidate` (disabled while `busy`) + the sentence `a harness run beside the project; no stage advances, nothing is written to HEAD`.
- [ ] **QuestionCard** (`error`, `utterance`, `onReply(text)`): head `A question first`; `error.question` verbatim in a `verbatim` block (fallback to `error.detail` when `question` is null); chips from `error.acceptedForms` (each a button that calls `onReply(form)` — it fills the composer, does not send).
- [ ] **RefusalCard** (`error`, `what`): head `The server refused` + `ErrorPanel` (existing) inside.
- [ ] **CandidateCard** (`candidateId`, `jobId`, `onJobStatus`, `onCandidate(candidate)`, `onPreview(artifact, label)`, `onEvidence("receipts")`, `loadingSha`): move the polling effect from `CandidatePanel.tsx` unchanged (POLL_MS 400, `IN_FLIGHT`); render: head `Candidate {job.status}` with wall time `{wallTimeS.toFixed(1)} s` when present; `job.error` verbatim when present (`verbatim` block); when the candidate DTO is ready: one line `{n} seats · {seatResults.map(s => `${s.seatId} ${s.status}${s.objects !== null ? ` ${s.objects} objects` : ""}`).join(" · ")}`; `candidate.harness` verbatim; artifact buttons `Preview {fileName}` (disabled when `!available || loadingSha !== null`; unavailable ones show `unavailableReason` instead of a button); `candidate.honesty` verbatim block if non-empty; `receipts` link. Call `onCandidate(value)` when the DTO arrives (App stores it for the strip and the drawer).
- [ ] **VerdictCard** (`candidateId`, `jobStatus`, `onValidation(v)`, `onEvidence("receipts")`): move the reading effect from `ValidationPanel.tsx` (reads only when `jobStatus === "succeeded"`; `CANDIDATE_NOT_FINISHED` handling kept); render: the word `May advance` (`.verdict__word--go`) or `Blocked` (`.verdict__word--no`) from `value.advance`; line `receipt {passed ? "passed" : "did not pass"} · effective: {effectiveChecks.join(", ")}`; clause chips in the fixed order `["validation.receipt","runner.seat_execution_complete","relations.held","relations.fully_checked","runner.exports_available"]` — a chip is `pill--violated` when its name is in `blockedBy`, else `pill--held`; any name in `blockedBy` not in the list is appended as a `pill--violated` too (never dropped); then `<RelationChips checks={value.relationChecks}/>`; findings list verbatim when non-empty; `value.honesty` verbatim block when non-empty; `Read the receipt` link. Call `onValidation(value)` when read.
- [ ] **SystemLine** (`text`): `<p class="sys">`. **YouBubble** inline in `Conversation`.
- [ ] `npm run typecheck` (cards are not yet mounted; they must compile). Commit `P108 chat shell: cards`.

### Task 4: Conversation column

**Files:** Create `Conversation.tsx`, `Composer.tsx`, `SelectionPicker.tsx`.

- [ ] **SelectionPicker** (`projection`, `onPick(componentId, elementId | null)`, `onClose()`): a text input filters `projection.componentTree ?? []` by `componentId`/`intent`, plus `projection.elements` by `elementId`; list rows `componentId · semanticKind` and, indented, `elementId · producer`; Enter/click picks; Escape closes. When `componentTree` is null, list the components derived from `elements[].componentId` (unique) and show `projection.componentTreeError` verbatim above the list.
- [ ] **Composer** (`selection: {componentId, elementId} | null`, `projection`, `disabledReason: string | null`, `busy`, `draft`, `onDraft`, `onSubmit(utterance)`, `onSelect(...)`): context row `talking about` + pill (`elementId ?? componentId`, mono) + `change` toggle opening the picker (or `pick in the model, or choose a component` when nothing is selected); the input (`placeholder="set height to 2.2 · increase height by 10 % · keep entity:…"`), `Propose` submit; hint line naming the four forms; disabled state shows `disabledReason` under the box.
- [ ] **Conversation** (`project`, `projection`, `entries`, composer props, card callbacks): header (wordmark `ArchFlow Studio`, mono line `{project.projectId} · HEAD v{project.head.version}`, pill `proposal-only`); the scroll list rendering each entry by kind; auto-scroll: keep a `stickToBottom` ref that is true until the user scrolls up more than 40 px from the bottom; on entries change, if stuck, `scrollTo({top: scrollHeight})`. Session states: `loading` → one `SystemLine("reading the binding…")`; `failed` → `RefusalCard` at the top and the composer disabled with the code.
- [ ] `npm run typecheck`. Commit `P108 chat shell: conversation column`.

### Task 5: Stage and drawer

**Files:** Create `Stage.tsx`, `SourceChip.tsx`, `VersionsStrip.tsx`, `EvidenceDrawer.tsx`, `HonestyTab.tsx`, `ReceiptsTab.tsx`.

- [ ] **Stage** (props = today's `ViewerPanel` props + `picked: {componentId, elementId, fields} | null`, `versions`, `onOpenVersion`, `evidenceCounts`, `onEvidence`): `<section class="stage">` containing `<ErrorBoundary label="viewer"><ThreeDmViewport …/></ErrorBoundary>`, the `hud` (SourceChip left; view tools right: `fit`, `front`, `clear`, `open .3dm` → `onRequestFile`), a `picked` chip under the source chip when a pick resolved (`{elementId ?? componentId}` + the element's `numericFields` as `key value` pairs, no unit), the `artifactError` as an `ErrorPanel` overlay when set, the `VersionsStrip` + evidence tab along the bottom.
- [ ] **SourceChip** (`sourceLabel`, `inspection`, `status`, `message`): tag = `LOCAL` when label is `LOCAL_SOURCE_LABEL`, `CANDIDATE` when it starts with `CANDIDATE`, `RUN` when it starts with `CANONICAL`, `NO MODEL` when null; then the label's remainder in mono, `inspection.fileName · meshCount meshes · objectCount objects` when present; the viewer `message` when `status !== "ready"`.
- [ ] **VersionsStrip** (`versions: VersionCard[]`, `loadingSha`, `onOpen(artifact, label)`): `VersionCard = { artifact: ProjectArtifactDto; label: "Reference" | "Candidate" | "Run"; title: string; meta: string; verdict: string | null }` built in App (see Task 6); each card a button, disabled when `!available`, showing `unavailableReason` as meta when unavailable; `aria-pressed` when its sha256 equals the loaded artifact's.
- [ ] **EvidenceDrawer** (`open`, `pinned`, `tab`, `onTab`, `onClose`, `onPin`, `counts`, children per tab): `<aside class="drawer" data-open data-pinned>`; head with the label `Evidence` / `Everything the server said, verbatim`, pin and close buttons; three tabs with counts; body renders the active tab. Pinned state persisted in `localStorage` key `archflow-studio.evidence.pinned` (try/catch both ways).
- [ ] **HonestyTab** (`projection`, `candidate | null`, `validation | null`): sections `Record honesty · projection` (lines), `Candidate honesty` (lines, only when a candidate is selected), `Verdict honesty`; `Identities` dl (`state digest`, `record digest`, `head` `v{n} · {stateSha256 ?? "—"}`, `reference run` `{runId} · {referenceRunSource}`, `receipt match` `String(matchesReferenceReceipt)`), `Validation note` (`validatorNote`, `canonicalFacts` verbatim when a validation exists). Every list says `none` when empty ("empty is a real answer").
- [ ] **ReceiptsTab** (`candidate | null`, `validation | null`): `candidateId`, `receiptRef`, seat rows (`seatId · status · objects · programRef`), artifacts (`fileName · status · available · sha256`), `skippedRuns`; validation: `receiptId`, `submissionDigest`, `checkedState`, `passed`, findings verbatim, `validators`, `effectiveChecks`, `blockedBy`. When no candidate: `no candidate has run in this tab`.
- [ ] `npm run typecheck`. Commit `P108 chat shell: stage and evidence drawer`.

### Task 6: App composition

**Files:** Rewrite `app/App.tsx`; create `app/AppShell.tsx`; delete the removed files listed in the file structure; update imports (`artifactLabels`).

- [ ] **AppShell** (`conversation`, `stage`, `drawer`, `pinned`): `<div class="app" data-pinned>` grid `400px 1fr` (+ `380px` when pinned); the drawer element is rendered inside the stage when not pinned (overlay) and as the third column when pinned.
- [ ] **App**: keep `useSession`, `loadArtifacts`, `loadArtifactIntoViewer`, `noteSource`, `resolvePick`, `propose`, `runCandidate` bodies as they are today, re-targeted at the transcript:
  - on session ready: `append({kind:"system", text: `Bound to ${projectId} at HEAD v${head.version} · reference run ${projection.referenceRun.runId} (${projection.referenceRunSource})${projection.matchesReferenceReceipt ? " · receipt reproduced" : ""}`})` once per projection load (guard with a ref on `stateDigest`);
  - `resolvePick` success → `append({kind:"system", text: `You picked ${elementId ?? componentId ?? "nothing resolvable"} in the model · ${status} · source ${sourceState}${detail ? ` · ${detail}` : ""}`})` and set the selection when `resolved`;
  - `propose(utterance)` → `append you`; on `ProposalDto` → `append proposal`; on `BLOCKED_NEEDS_HUMAN` → `append question`; other errors → `append refusal` (+ `recoverFromStaleBase`);
  - `runCandidate(proposalId)` → `append candidate` entry (status from `CandidateAcceptedDto`); `noteJobStatus` → transcript; when a candidate card reports `succeeded` the first time → `append verdict` (guard with `hasVerdictFor`) and `loadArtifacts()`;
  - `candidates: Map<candidateId, CandidateDto>` and `validations: Map<candidateId, ValidationDto>` in state (from card callbacks); `selectedCandidateId` = the last candidate entry's id (for the drawer tabs and the strip labels);
  - versions: from `artifacts.value.artifacts` grouped by `runId`: label `Reference` when `runId === projection.referenceRun.runId` (title `HEAD v${referenceRun.baseVersion}` , meta `receipt ${sha8(sha256)}`), `Candidate` when the runId is a candidate this tab launched (title the proposal's utterance, meta the verdict word when known), else `Run` (title runId); order: reference first, then candidates newest first, then runs;
  - evidence: `open`/`pinned`/`tab` state; `onEvidence(tab)` opens the drawer on that tab; counts = honesty lines held (projection + selected candidate + selected validation), candidates launched, events kept (EventStream reports its line count via an `onCount` prop — add it, one line);
  - the STALE_BASE notice and other `pushNotice` lines become `system` entries.
- [ ] Delete the removed files; `npm run typecheck`; `npm run build`; `npm run api:check`. Commit `P108 chat shell: the conversation beside the model`.

### Task 7: Styles

**Files:** `web/src/styles.css` (everything below the tokens).

- [ ] Replace all shell/panel/chip/row CSS with the classes the new components use (`.app`, `.chat*`, `.msg*`, `.sys`, `.card*`, `.kv`, `.delta`, `.pill*`, `.verdict*`, `.btn*`, `.chips`, `.chip` (composer quick-replies), `.composer*`, `.picker*`, `.stage`, `.hud`, `.source*`, `.viewtools`, `.picked`, `.versions`, `.vcard*`, `.drawer*`, `.ev*`, `.verbatim`, `.error-panel*` (kept), `.events*` (kept)); keep byte-for-byte the viewer classes `ThreeDmViewport` renders: `.viewport-host`, `.viewport-canvas`, `.viewport-state` (+ `--idle/--loading/--error`), `.drop-target`, `.is-dragging`, `.spinner`, `.button`, `.button--primary`; `.visually-hidden` for the file input. Responsive rule at 900 px (stage on top, 56 vh). `prefers-reduced-motion` disables the drawer transition.
- [ ] `npm run build`; open the dev server on a temp villa copy and check every state visually (spec §12). Commit `P108 chat shell: styles`.

### Task 8: README and smoke

**Files:** `apps/archflow-studio/README.md` (§ web run/what the shell shows), the ledger.

- [ ] README: replace the description of the panels with the two surfaces + drawer (one paragraph each), the transcript kinds table (spec §5), and the evidence ruling sentence; keep every command; add the fonts note (Google Fonts, system fallbacks).
- [ ] Live smoke per spec §12 in the Browser pane (temp copy, export on), light and dark, 900 px fold; fix what fails; screenshots described in the ledger.
- [ ] Commit `P108 chat shell: README and smoke`. Then a whole-change review (requesting-code-review) before handing to Kaiwen.
