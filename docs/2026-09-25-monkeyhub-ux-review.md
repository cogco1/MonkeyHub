# MonkeyHub UI/UX review (2026-09-25)

**Issue:** [#234](https://github.com/cogco1/MonkeyHub/issues/234), lane `ux-review` · **Base:** `origin/main` `e183a69a` · **Nature:** review only. No product code, record or API change.
**Asked:** “我建议你看一遍整体UI/UX方案，看看有什么优化点”.

**Method.** I read the shell (`ChatShell.tsx`, `ChatShell.css`, `main.tsx`), MonkeyArch (`App.tsx`, `features/stage/*`), Drawing, Render, Publish, Board and both catalogs. I also read the [09-24 audit](2026-09-24-monkeyhub-ui-reality-audit.md), the [09-24 proposal](2026-09-24-monkeyhub-interaction-proposal.md), the [#284 note](2026-09-25-candidate-graph-interaction.md) and its [prototype](prototypes/candidate-graph/README.md), the [#294 audit](2026-09-25-candidate-admission-audit.md) (§5 decisions) and #300.

I ran three browser tests on the built UI with synthetic fixtures:
- `test/chatShell.browser.mjs`: 30 screenshots.
- `workspaces/test/candidatePreview.browser.mjs --view-base`: 5 screenshots.
- `workspaces/test/drawingCanvas.browser.mjs`: 2 screenshots.

`renderWorkspace.browser.mjs` failed twice: its Python runtime did not answer `/api/health` within 20 s. Render is therefore reviewed from code only. Nothing was run against a real Hub, provider or project.

**Evidence keys.** The screenshots are not committed. Rerun the command to regenerate them.

| Key | Meaning |
| --- | --- |
| `HUB` / `WS` | `apps/monkeyhub/web/src` / `apps/monkeyhub/web/workspaces/src` |
| `EN` / `ZH` | `HUB/i18n/messages.en.ts` / `HUB/i18n/messages.zh-CN.ts` |
| `shot:` | the folder that `node test/chatShell.browser.mjs` prints |
| `base:` | the `VIEW_BASE_SCREENSHOTS` folder of `candidatePreview.browser.mjs --view-base` |
| `draw:` | the folder that `drawingCanvas.browser.mjs` prints |
| `proto:` | `docs/prototypes/candidate-graph/screenshots/` |

**Principles (owner, 2026-09-25):**

| Tag | Principle |
| --- | --- |
| **Π1** | The design history is one growing trunk on a zoomable canvas. |
| **Π2** | Low density, semantic zoom, no raw ids in the main copy. |
| **Π3** | No manual save. Sync is a design act, not saving. |
| **Π4** | The sidebar follows the Codex and Claude desktop pattern (#300). |
| **Π5** | Drawing is a Tool. View, Continue and Accept-as-Stage stay separate. |
| **Π6** | One canonical entry per concept. The parallel entry is retired in the same change. |

**Priority:** P0 blocks daily use or contradicts a principle · P1 friction · P2 polish.
**Effort:** S ≤ 1 day · M ≤ 3 days · L longer.

## 0. Owner decisions (Kaiwen, 2026-09-25)

The owner asked for the review's open questions to be settled on the recommendations below. Slices from §4 follow these.

| Question | Decision |
| --- | --- |
| Surfaces and tools on the rail | Surfaces: 建模 · 画板 · 状态树 (Design Tree). Tools: 图纸 · 渲染 · 制作 · 用量. 排版 (Publish) becomes a 画板 \| 排版 mode inside Board. |
| Position word | **当前 / Current**, as in the chip "S2 · 当前". Explanatory copy says "从这里继续修改". 修改起点 leaves the main copy. |
| Name for Sync | **记录 / Record**: "记录这一版"; status "有未记录的修改 · 已自动保存". Edits always autosave, so Sync is never "save". |
| Board auto-receive vs "Send to Board" | Keep auto-receive and retire the explicit send buttons. New items land in a Board inbox with a count and never move the viewer. |
| One chat or two | One chat: the Hub conversation. MonkeyArch's deterministic chat panel is not a second chat; its cards merge into Hub result cards, and intent compilation stays as a capability. |
| Fabrication | A Tool. |
| Design Tree list view | Kept as a secondary view (accessibility and narrow screens). The canvas is the default. |
| Software updates | Fully automatic: the new version takes effect on next launch (#58 reordered). |

## 1. Summary: the five changes that matter most

1. **One “where am I” on every surface.** Add the project bar and Stage chip (#284), and make the Design Tree the only history entry. In the same PRs, retire three parallel entries: the Modeling footer rows, the VersionsStrip design section and the rail project card (IA-1, MC-1, MC-2, R1–R4).
2. **Agent results arrive without grabbing the view.** Show one Study card per request and a chip notice: “Entrance Study · 5 schemes ready · Show”. Stop switching the panel to Modeling on every result, and fold raw tool rows into “Process · N steps” (FN-1, DC-1, DC-2).
3. **Autosave everywhere; Sync becomes a named design act offered where it is needed.** Retire the Save buttons in Publish, Drawing, Board and Hub settings. Replace “Sync or undo them, then try again” with one click: “Record edits, then continue” (SS-1 to SS-6).
4. **Viewing never moves the base.** A Stage click opens it read-only, and Board feedback asks before switching the base. Use one verb per act: View · Continue from here · Accept as next Stage (SS-7, SS-8, DC-6).
5. **More model, less chrome, one vocabulary.** Collapse the sidebar automatically when a surface opens, and give all five surfaces one header pattern. Adopt one glossary: Board alone has four visible Chinese names. Take copy only from the catalogs, use one error presenter, and fix the Board zoom bar that leaks over the composer (MC-3, BD-1, DC-4, FN-4, NA-1).

## 2. Findings

### 2.1 Information architecture

| ID | Problem · evidence | Proposal | P | Eff. | Owner |
| --- | --- | --- | --- | --- | --- |
| IA-1 | “Where am I” is split three ways and missing from four of five surfaces. The chat header shows only the project and chat title (`HUB/ChatShell.tsx:891`). Stage and position live in the Modeling footer (`WS/features/stage/Stage.tsx:2040-2055, 1176-1201`) and in a rail popover (`ChatShell.tsx:1051-1115`). Drawing shows only its own source (`DrawingCanvas.tsx:296`), and Render, Board and Publish show no position. No project bar, chip or tree exists in the code yet. | Add a project bar above every surface: `Project / S2 · Layout · Current ▾` plus attention badges (#284 §6, `proto:01-closed.png`). The chip is the only persistent tree entry. Below 900 px it moves into whichever header is on top. | P0 | M | #284, #283 |
| IA-2 | The rail holds four kinds of thing: surfaces, tools, a project popover and a panel toggle (`ChatShell.tsx:1028-1125`). The group captions truncate even at 1440 px: “Workspa…”, “This proj…” (`shot:desktop.png`; `ChatShell.css:68`). | Keep only surfaces and tools on the rail. Project facts go to the chip menu and project actions to the sidebar (IA-4). Pressing the active entry again hides the panel, which retires the Hide/Show tools button (see IA-6). Replace the captions with one divider. | P1 | S | #283, #300 |
| IA-3 | Cross-project questions have no home. The sidebar has only New chat, New project, the list, Archived and Settings (`ChatShell.tsx:866-889`). Update status sits inside Settings (`:1168`), and nothing shows running or waiting work across projects. | Build #300 as approved, with two additions. (a) When slice 1 adds Usage to the sidebar footer, remove the rail Usage entry (`ChatShell.tsx:51`) in the same PR, or make the footer item only a readout that opens the same page (Π6). (b) Project rows get the chip and badges in slice 2. | P0 | M | #300 |
| IA-4 | Project actions are scattered. Export and Restore archive sit in one project's popover (`ChatShell.tsx:1109-1114`), although Restore is cross-project. Add existing project is a bare “+” (`:871`). Every chat row shows an archive icon at all times (`:880-883`). | Follow the Codex/Claude pattern. Each project row gets a “…” menu (Rename, Export archive, Reveal folder), and “New project ▾” offers Create, Add existing and Restore from archive. Row icons appear on hover. | P2 | S | #300 |
| IA-5 | Publish and Drawings share one icon (`ChatShell.tsx:48, 50`). | Give Publish its own glyph. | P2 | S | #280 |
| IA-6 | Tools toggle inconsistently. Pressing Drawing again returns to the previous surface (`ChatShell.tsx:1042-1047`); pressing Usage again does nothing. | Use one rule everywhere: pressing the active entry steps back one level. For a Tool that means the previous surface; for a surface it means hiding the panel. | P2 | S | #281 |
| IA-7 | Fabrication needs no project but sits in the project group (`ChatShell.tsx:49`; audit H2). #295 ruled out inferring other moves. | Owner decision: keep it, or move it to #300's “More”. | P2 | S | owner |

### 2.2 Density and copy

| ID | Problem · evidence | Proposal | P | Eff. | Owner |
| --- | --- | --- | --- | --- | --- |
| DC-1 | The chat reads like a request log. Rows such as `studio_schema · GET /api/state · in_progress`, `studio_request · POST /api/issue · failed` and `HubFailure(422): …` appear in monospace (`shot:desktop.png`, `shot:hub-arch.png`; `ChatShell.tsx:909-911`). A single turn can mix seven element kinds (`ChatShell.tsx:902-939`). | Fold a turn's tool rows into “Process · N steps”, closed by default and in plain words. Raw rows appear only in developer mode (proposal §6.1). That leaves five element kinds: user message, Agent message, result card, folded process and a needs-you block. | P0 | M | #285 |
| DC-2 | Every tool row that produced a result gets “Open this candidate on the right” plus “View the candidate in the modeling page.” (`ChatShell.tsx:919-920`; `EN:940`). There are two in one turn in `shot:desktop.png`, and five schemes produce five or more (#284 §2). | Show one Study card per request: thumbnails (#292), “5 of 5 ready”, Compare and Show in Design Tree (`proto:01-closed.png`). The per-run buttons retire. | P0 | M | #294 S3, #285 |
| DC-3 | File names and hashes stand in for versions: <br>• Modeling: “Next edit starts from home-B.3dm” (`shot:desktop.png`; `Stage.tsx:1157`). <br>• Versions panel: “REFERENCE home-A.3dm”, “RUN cand-A-1.3dm” (`shot:hub-versions.png`). <br>• Drawing picker: “floor-plan-10.png · 2026-09-23T00:00:010Z” (`draw:drawing-wide-dark.png`; `WS/workspaces/monkeydiagram/DrawingCanvas.tsx:276-280`); Stage picker shows `label · branchId` (`:287`). <br>• Render and Publish pickers: `fileName` only (`RenderWorkspace.tsx:211, 222`; `PublishWorkspace.tsx:174`). | Derive every label in one module (#279), e.g. “S2 · Current · 3 edits”, “Plan A · follows Current”, “Render 3 · from S1 · 9/24”. File names go to the details. | P0 | M | #279 |
| DC-4 | One concept, many names. <br>• Board: 画板 on the rail, 白板 (`ZH:55`), 画布 and 图墙 (`WS/workspaces/monkeyboard/Board.tsx:22`). <br>• Drawing: “Drawing”, “Drawings”, “Drawing · 图纸”, “MonkeyDiagram · Drawings” (`DrawingCanvas.tsx:13, 29`; `EN:52, 936`). <br>• Publish: 排版 vs 放入汇报 (`Board.tsx:1143`). <br>• Stage vs 阶段 in the same composer (`ZH:924`). <br>• Position: 修改起点, 下次修改起点, Current, Working Head. | Make one glossary pass after the owner chooses the position word (#284 Q2). Use Study / 方案组 as decided in #294 Q4. | P1 | S | #280 |
| DC-5 | The composer always spends two lines on a checkbox: “Continue from project state (without previous conversation context)” (`ChatShell.tsx:968-973`; every chat screenshot). | Replace it with “New topic” in the + menu (proposal §6.1). | P1 | S | #285 |
| DC-6 | Verbs multiply. <br>• Continue: 打开并继续修改, 预览并继续修改, “Continue from this version”, “Return to default editing base”. <br>• Accept: 确认下一 Stage, 接受为下一 Stage, 确认当前模型为 S0, “· 当前提交”. <br>(`WS/features/stage/VersionsStrip.tsx:102, 105, 125, 131, 157, 159`; `EN:173, 175`) | Keep three verbs: View · Continue from here · Accept as next Stage. Accept appears only on Current. Retiring the VersionsStrip design section (R3) provides this. | P0 | S | #284 |
| DC-7 | Copy leaks across languages. <br>• VersionsStrip is hard-coded Chinese, so the English UI shows Chinese. <br>• Stage, Render and Publish use inline `zh ? … : …` (`Stage.tsx:1684, 1716, 1726, 1783, 1989-2024`; `RenderWorkspace.tsx:186-198`). <br>• Headers are English in both languages: “Render”, “AI”, “Physical”, “Publish” (`RenderWorkspace.tsx:185-195`; `PublishWorkspace.tsx:143`). <br>• Server details appear in English in the Chinese UI (“Exact source retained.”, `draw:drawing-narrow.png`). <br>• English prose is machine-translated at runtime (`WS/i18n/BilingualText.tsx`). <br>• Meanings differ between languages: toolError “Failed” vs 连接失败, toolUnavailable “Unavailable” vs 缺少依赖 (`EN:997`; `ZH:952`). | Take all product copy from the two catalogs. Keep runtime translation for user and Agent content only, and add the catalog check from proposal §10. | P1 | M | #280 |
| DC-8 | Boot copy exposes plumbing: “MonkeyArch · starting the session”, “reading the binding · GET /api/project” (`WS/app/App.tsx:3309-3321`). The statuses “Project Runtime”, “Render” and “MonkeyBoard” all appear under a MonkeyArch brand (`WS/app/ProjectWorkspace.tsx:120-151`; `WS/app/LoadingOverlay.tsx:57-61`). The spaces around a file name are lost: “Parsinghome-A.3dmlocally” (`shot:new-project.png`; `WS/workspaces/monkeyarch/viewer/ThreeDmViewport.tsx:1413`). | Show the surface name and one plain step, e.g. “Opening Plan A…”. No API paths, and keep the spaces around protected tokens. | P2 | S | #282 |
| DC-9 | The composer never says which model a message will change. The design context is attached silently (`ChatShell.tsx:540-543`) and echoed only after sending (`:924`). | Add a target label above the input: “Changes: Current · 3 edits after S2”. While viewing another model, offer two choices (proposal §6.1). | P1 | S | #285 |

### 2.3 Save, sync and continue

| ID | Problem · evidence | Proposal | P | Eff. | Owner |
| --- | --- | --- | --- | --- | --- |
| SS-1 | Publish needs a manual save: an Unsaved/Saved status and a Save button (`WS/workspaces/publish/PublishWorkspace.tsx:144-145`). There is no leave guard; only Board and Fab have one (`Board.tsx:686`; `HUB/FabPage.tsx:89`). | Autosave through Board's compare-and-swap save queue (`boardSaveQueue.ts`). Export remains the act. Retire Save. | P0 | M | GH-66 |
| SS-2 | Drawing edits stay form state until “Save appearance as a revision”. SVG download is blocked until then (`DrawingCanvas.tsx:21, 337-341`). Switching revisions discards edits without asking (`:213-222`). | Apply appearance edits after a pause as a new revision, the way Board autosaves. “Draw another version” and “Follow the current model again” remain the design acts. | P0 | M | #287, #244 |
| SS-3 | Board already autosaves (“Saved”), yet it offers “Save now” in two places (`Board.tsx:21, 1142, 1213`). | Remove both. Keep Ctrl+S as a silent flush. | P0 | S | GH-66 |
| SS-4 | Hub settings have two Save buttons and an Unsaved state (`HUB/main.tsx:285-292`). Unsaved settings block the update restart (`HUB/SoftwareUpdateSettings.tsx:93-94`). Π3 is about design edits, hence P1. | Apply each change immediately, as Codex and Claude desktop do. Ports keep their “applies on next start” note. Drop the settings blocker. | P1 | S | #300 |
| SS-5 | Sync looks like save or reload: an unlabeled circular-arrows icon at the end of the toolbar, with the status “Unsynced” or “Draft saved automatically” (`Stage.tsx:1736-1744`; `EN:667-671`; `shot:desktop.png`). | Name the act by its result; the owner picks the words, e.g. “Record version / 记为一版”. Show it only when there are edits (“3 edits · Record”), and show autosave as a quiet check mark. | P1 | S | #279 |
| SS-6 | Several prompts dead-end on “sync first”: <br>• Continue is refused: “…local model edits are not synced yet… Sync or undo them…, then try again” (`EN:186`; `base:unsynced-refusal-1440.png`). <br>• A new context is refused the same way (`EN:964`). <br>• Restart says “Sync or save them before restarting” (`EN:898`). | Where the refusal happens, offer one inline choice: [Record edits, then continue] [Discard edits] [Cancel] (#284 §8, step 2). The restart prompt belongs to #234 autosave-restart. | P1 | M | #286, #234 |
| SS-7 | Clicking a Stage in Versions moves the editing base (`VersionsStrip.tsx:128-131` → `App.tsx:2191-2193`). Audit F11 is still open. | Clicking a Stage or Candidate only views it. Continue is a separate button (#284 §8). | P0 | S | #284 |
| SS-8 | Board feedback switches the editing base to the drawing's model before proposing, without asking (`App.tsx:2149-2162`). | Ask first: “Update this drawing” or “Change from S1 and send” (proposal §6.3). | P0 | S | #288 |
| SS-9 | Unsent composer text lives only in memory (`ChatShell.tsx:167, 258`) and blocks the update restart (`EN:897`). | Keep drafts per chat in local storage, then drop the “drafts” blocker. | P1 | S | #234 |

### 2.4 Feedback and notifications

| ID | Problem · evidence | Proposal | P | Eff. | Owner |
| --- | --- | --- | --- | --- | --- |
| FN-1 | Results grab focus. During a turn, each result that has been read back calls `openTool("monkeyarch", …)` (`ChatShell.tsx:848-861`). Modeling skips the reuse-the-tab branch (`:672`) and ends by making Modeling the active tool (`:707-708`). An architect marking up on Board is pulled to Modeling. A headless delivery opens a closed panel (`:840-845`). Five schemes mean five jumps (#284 §2). | Never change the visible surface. Update the chip notice and the “new” count. Auto-show only the Study's first ready result, view-only, and only when Modeling is already visible (#284 Q5). | P0 | M | #286, #294 S3 |
| FN-2 | There is no notification layer: `HUB` has no toast, bell or system notification. A background chat only pulses a 6 px dot (`ChatShell.css:25`). A permission request blocks the Agent but shows only inside its own chat (`ChatShell.tsx:912-918`; `shot:permission-pending.png`). | Add the #300 slice 3 bell on the runtime stream. Give thread and project rows a “needs you” state, and send an OS notification when the window is hidden. | P1 | M | #300 |
| FN-3 | In Modeling, the only sign of new results is a “New” pill on the Versions button (`Stage.tsx:2048`; `shot:permission-pending.png`). Other surfaces never see it. | Move the attention counts to the chip (#284 §6). Retire the pill together with the footer. | P1 | S | #284 |
| FN-4 | There are two error presenters. The chat shows a plain line plus details (`HUB/chatError.ts:99-173`). Workspaces show only “This action could not be completed. Check the design state and try again.”; the code and detail appear only in developer mode (`WS/app/ErrorPanel.tsx:88-103`; `EN:148`). | Use one Failure component: what happened, the next step as a button, and details that anyone can open. | P1 | M | #282 |
| FN-5 | After Continue or Accept, the only feedback is a changed footer label. | Show a toast with Undo, e.g. “Current is now Entrance B · Undo” (#284 §8). | P2 | S | #284 |
| FN-6 | Render's unknown-outcome warning prints the raw request UUID (`RenderWorkspace.tsx:251`). | Use a plain sentence and move the id to the details. | P2 | S | #253 |

### 2.5 Modeling workspace chrome

| ID | Problem · evidence | Proposal | P | Eff. | Owner |
| --- | --- | --- | --- | --- | --- |
| MC-1 | A footer card sits over the viewport. It holds “Versions N · Viewing … · New”, the picked object, a Board button, and “Next edit starts from … [Continue] [Return to default editing base]” with notices (`Stage.tsx:2040-2067, 1172-1201`). N counts Stages when design history is on, otherwise exports and options (`:1158-1162`). | Retire the card; the chip carries position and the viewing warning. Keep the picked-object label next to the selection. | P0 | M | #284 |
| MC-2 | The Versions panel covers the model, at up to 720 × 480 px (`WS/styles.css:1281-1297`). It is a second history, containing: <br>• draft rows and a raw branch select; <br>• a Git-style fork form (`pattern` `[A-Za-z0-9]…`, placeholder “alternate-layout”); <br>• recovery points with an explanatory paragraph; <br>• Explorations, Candidates with Combine, and legacy runs. <br>(`VersionsStrip.tsx:86-168`) | Retire the design section when the tree lands. Keep “Files & exports” as a details view. | P0 | M | #284 |
| MC-3 | The workspace gets 43 % of a 1440 px window. The sidebar (244 px), chat (at least 360 px) and rail (76 px) leave a 620 px panel (`ChatShell.css:1, 6`; `shot:hub-arch.png`). At 1280 px the panel is capped at 595 px. | Collapse the sidebar automatically to its 60 px strip when a surface opens (it can be pinned open). Add a focus toggle that narrows the chat (proposal §5). | P1 | M | #283 |
| MC-4 | The toolbar always shows a CAD plane select: XY, XZ, YZ, Face (`Stage.tsx:1707-1713`). “View options” mixes views with Clear, Open .3dm, Parameter locks, Screenshot and the Rhino export (`:1780-1870`). | Show the plane select only while a drawing tool is active. Split “…” into View and File. | P2 | S | #283 |
| MC-5 | The Arch “Conversation” is a second chat. It is closed by default and has no control to reopen it (`App.tsx:502-513`), and it states “No agent is wired here” (`EN:521`). Board feedback and sketches open it by switching to Modeling (`ProjectWorkspace.tsx:100-107`). | Settle proposal Q4 in favour of one chat. Send Board feedback into the Hub chat as a message that carries its marks, and keep the deterministic compiler as a capability. | P1 | L | owner Q4 |
| MC-6 | Auto-show is recorded only as English system lines, and they land in the closed Conversation: “the candidate's model is on screen · file · not accepted” (`App.tsx:2985-2997, 3010-3017`) and “Talking about …” (`:3365-3367`). | Drop them once the chip shows the viewing state. | P2 | S | #286 |

### 2.6 Board, Drawing, Render and Publish consistency

| ID | Problem · evidence | Proposal | P | Eff. | Owner |
| --- | --- | --- | --- | --- | --- |
| BD-1 | Five surfaces, five header patterns: <br>• Board: title, Saved, Project documents, Upload, More (`Board.tsx:1129-1155`). <br>• Drawing: h1, intro, “Refresh sources” (`DrawingCanvas.tsx:270-271`). <br>• Render: “Render”, AI/Physical, “Refresh status” (`RenderWorkspace.tsx:184-191`). <br>• Publish: title, Save, PPTX, PDF (`PublishWorkspace.tsx:142-148`). <br>• Modeling: no header. | One project bar (IA-1) plus an action slot with at most two actions per surface (proposal §5). | P1 | M | #283 |
| BD-2 | Freshness is described five ways: <br>• Drawing: “Follows… / Current with… / Not updated / Earlier revision · not updated automatically” (`DrawingCanvas.tsx:15-18`). <br>• Publish: “Current source / Frozen / Source changed, check”, shown only on the selected element (`PublishWorkspace.tsx:183-186`). <br>• Render: “View saved. Capture again…” (`RenderWorkspace.tsx:215-216`). <br>• Board: “Linked” or “Unlinked”, with no stale check (`Board.tsx:1224`). <br>• Project card: `EN:946-948`. | Show one Live / Stale / Frozen badge with one Update action, in the same place on every surface and on Board pages (#254). | P1 | M | #287, #288 |
| BD-3 | There are two ways to put a render on Board. Board adds every unseen project document as a page (`Board.tsx:622-635`; audit 9.1), and Render offers “Send to Board” (`WS/workspaces/render/RenderResults.tsx:105`). | Keep one (Π6). The owner decides; the suggestion is an explicit Send, plus a “New in project · 3” tray on Board. Replacements still update in place (#291). | P1 | S | owner, #291 |
| BD-4 | Physical render is a placeholder page (`RenderWorkspace.tsx:186-198`). An unconfigured engine shows “No engine available” or the provider's own reason, with no link to Settings (`:203-207`). | Hide Physical until an executor exists. When no engine is configured, show “Set up AI Render”, which opens Settings. | P1 | S | #253, #282 |
| BD-5 | Drawing's own title says “Drawing” while the rail says “Drawings”; the Chinese title reads “Drawing · 图纸” (`DrawingCanvas.tsx:13, 29`). | Use the naming-table name, MonkeyDiagram · 图纸 / Drawings, shown in the project bar. | P2 | S | #280 |
| BD-6 | Manual refresh buttons on Drawing, Render and the project card suggest the UI may be stale (`DrawingCanvas.tsx:271`; `RenderWorkspace.tsx:190`; `ChatShell.tsx:1083`). | Refresh from runtime events (PP-2). Show Retry only after a failure. | P2 | S | #282 |

### 2.7 Narrow screens and accessibility

| ID | Problem · evidence | Proposal | P | Eff. | Owner |
| --- | --- | --- | --- | --- | --- |
| NA-1 | When the tool panel is hidden, Board's zoom and undo bar leaks over the chat composer at 1440, 900 and 375 px (`shot:chat-progress-upload-1440.png`, `-900.png`, `-375.png`). The cause is Excalidraw's `.excalidraw .zen-mode-visibility{visibility:visible}`, which overrides the inherited `visibility:hidden` of the hidden panel (`ChatShell.css:8`). A hidden project workspace behind Usage uses the same mechanism (`:99`); that case was not observed. Excalidraw's footer children also set `pointer-events: var(--ui-pointerEvents)`. | Add one rule for both hidden containers: `[hidden] .excalidraw .zen-mode-visibility{visibility:hidden}`. Add an assertion for it to `chatShell.browser.mjs`. | P1 | S | #282 |
| NA-2 | Below 900 px the panel covers the whole chat, header included (`ChatShell.css:93`). While working, neither project nor Stage is visible. | Render the chip in whichever header is on top (#284 §6). | P1 | S | #284 |
| NA-3 | At 390 px the rail labels truncate: “Modeli…”, “Fabrica…”, “Drawin…”, “Hide t…” (`shot:mobile.png`). The toolbar wraps to two rows and the footer to three lines (`shot:toolbar-332.png`, `base:unsynced-refusal-390.png`). | Below 500 px, show icons with accessible names. The footer lines go away with MC-1. | P2 | S | #281 |
| NA-4 | Tool shortcuts appear only in a hover tooltip; there is no `aria-keyshortcuts` (`WS/features/stage/ModelToolButton.tsx:149-158`). Colour swatches announce hex codes (`Stage.tsx:1763`). | Add `aria-keyshortcuts` and give the colours names, e.g. “Red”. | P2 | S | #281 |
| NA-5 | The message list has `aria-live="polite" aria-relevant="additions text"` (`ChatShell.tsx:900`). Screen readers re-announce the reply while it streams. | Announce only completed messages, through a separate status region. | P2 | S | #281 |

### 2.8 Performance perception (loading states)

| ID | Problem · evidence | Proposal | P | Eff. | Owner |
| --- | --- | --- | --- | --- | --- |
| PP-1 | Opening a tool shows only “Connecting the project…”. It appears as the composer note (`ChatShell.tsx:994`), on the empty panel (`:1024`) and as the loading fallback for the 1.1 MB `ProjectWorkspace` chunk (`:1008`). Meanwhile the Hub may poll every 400 ms for up to 60 s (`:93, 416-429`). | Show a skeleton of the target surface at once. Name the steps: “Starting project service”, “Opening model”. After 3 s, show the elapsed time and a Cancel. | P1 | S | #282 |
| PP-2 | Freshness comes from five pollers: Hub `/api/apps` every second (`HUB/main.tsx:141-147`), Board every 5 s (`Board.tsx:722`), Drawing every 5 s (`DrawingCanvas.tsx:167-172`), Render every 2.5 s and Monitor every 5 s. The shell already reads an SSE stream (`ChatShell.tsx:358-382`). | Fan the runtime event stream out to the workspaces. Poll only as a fallback. | P2 | M | new |
| PP-3 | Every send shows “Connecting the project…” (`ChatShell.tsx:994`; `EN:936`). | Show “Sending…”; project connection gets its own state (proposal B3). | P2 | S | #282 |
| PP-4 | The largest cold loads in this build's `dist` are a 1.8 MB shared chunk, Board at 1.2 MB, ProjectWorkspace at 1.1 MB and `rhino3dm.wasm` at 2.7 MB. Load time was not measured. | Prefetch the last-used surface after the chat renders, together with PP-1's skeleton. | P2 | M | new |

## 3. What to retire

| # | Parallel entry or old copy | Where | Replaced by | Slice |
| --- | --- | --- | --- | --- |
| R1 | Footer “Versions N · Viewing · New” | `Stage.tsx:2040-2055` | Stage chip | #284 |
| R2 | Editing-base row and “Return to default editing base” | `Stage.tsx:1172-1201`; `EN:170-175` | Chip viewing state | #284 |
| R3 | VersionsStrip design section: draft rows, branch select, S0 init, Stage rows, fork form, Explorations, Candidates with Combine, legacy runs | `VersionsStrip.tsx:86-168` | Design Tree; “Files & exports” details | #284 |
| R4 | Rail “This project” button and popover: facts, the always-true “✓ Modeling follows the current project”, work rows, Refresh, Recovery details, tool URL, archive buttons | `ChatShell.tsx:1051-1122`; `ChatShell.css:102` | Chip menu, the tree, developer mode, sidebar project menu | #284, #300 |
| R5 | “Open this candidate on the right” plus its hint on every tool row | `ChatShell.tsx:919-920`; `EN:940` | Study card | #294 S3 |
| R6 | Composer checkbox “Continue from project state…” | `ChatShell.tsx:968-973`; `EN:960-964` | “New topic” in the + menu | #285 |
| R7 | Save buttons: Publish; Board “Save now”; Drawing “Save appearance as a revision”; two in Settings | `PublishWorkspace.tsx:144-145`; `Board.tsx:1142, 1213`; `DrawingCanvas.tsx:338-339`; `HUB/main.tsx:288-289` | Autosave | SS-1 to SS-4 |
| R8 | Rail “Usage” once the #300 footer has it | `ChatShell.tsx:51` | Sidebar footer | #300 slice 1 |
| R9 | Either Board auto-receive or Render “Send to Board” | `Board.tsx:622-635`; `RenderResults.tsx:105` | The one the owner keeps | BD-3 |
| R10 | Render “Physical” placeholder | `RenderWorkspace.tsx:186-198` | Nothing until an executor ships | BD-4 |
| R11 | Arch Conversation as a second chat, if Q4 decides for one chat | `App.tsx:3336-3412`; `EN:520-524` | Hub chat | MC-5 |
| R12 | Launcher copy with no quoted reference outside the catalogs: `archTitle`, `boardTitle` (“Presentation” / 展示), `diagramTitle`, `monitorTitle`, `fabTitle`, `shared`, `stopShared`, `closeNote`, `projectHelp`, `projectPlaceholder`, `monitorPort`, `saveLaunch`, `appearanceHelp` | `EN:903-914`; `ZH:859-870` | Nothing | #280 |
| R13 | Board names 白板, 画布 and 图墙; the Drawing title “Drawing · 图纸”; 放入汇报 | `ZH:55`; `Board.tsx:22, 1143`; `DrawingCanvas.tsx:29` | Glossary names | #280 |
| R14 | “MonkeyArch” brand on every loading screen; `GET /api/project` in the boot status | `LoadingOverlay.tsx:57-61`; `App.tsx:3317-3318` | Surface name | #282 |
| R15 | Design Tree “List \| Growth tree” toggle (the prototype defaults to list) | `docs/prototypes/candidate-graph/README.md:29`; #284 note §9 | The growth canvas as the one view, plus a keyboard outline for screen readers and ≤ 500 px | #284 |
| R16 | Unmerged branch `codex/234-ui-daily-use-plan` (`docs/UI_DAILY_USE_PLAN.md`, `140016a3`); it coins 工作版本, which proposal §2 rejects | Remote branch | The 09-24 proposal and this review | Owner closes it |

## 4. Suggested order

Tonight's in-flight work:
- #294 S1–S2: backend and the generated client.
- #284: the Design Tree on a shared Board canvas host.
- #234: autosave-restart.
- #300: sidebar slices 1–2.

Most of these touch `ChatShell.tsx`, `ChatShell.css` or the catalogs. The order below keeps new work off those files until they land.

1. **Now, in files no live lane claims.** Keep copy in local objects for now; step 6 moves it into the catalogs.
   - FN-4, first half: `ErrorPanel.tsx` shows the reason and a next step in normal mode. The chat's `Failure` (`ChatShell.tsx:131-150`) joins it in step 3.
   - BD-4 and FN-6 in Render (`RenderWorkspace.tsx`).
   - SS-2, plus the drawing parts of DC-3 and BD-5 (`DrawingCanvas.tsx`, `drawingPlan.ts`).
   - NA-4 (`ModelToolButton.tsx`) and R14 (`LoadingOverlay.tsx`).
2. **Inside #284, in the same PRs (Π6).** IA-1, NA-2, SS-7, DC-6, FN-3, FN-5, MC-1, MC-2, R1–R3, R15, and R4 except its archive buttons.
3. **After #294 S1–S2 and #284 V0.** #294 S3, #285 and #286: DC-1, DC-2, R5, FN-1, DC-5, R6, DC-9, SS-6, MC-6, PP-3, and the second half of FN-4.
4. **After #300 slices 1–2 and #234 autosave-restart.**
   - #300 slice 3 (FN-2), R8, IA-4, and the archive part of R4.
   - SS-4 and SS-9, so that the restart guard reads a single set of blockers.
   - The NA-1 rule in `ChatShell.css`. It can also ride with #300 slice 1, which edits the same file.
5. **Layout (#283).** MC-3, BD-1, IA-2, IA-5, IA-6, MC-4, SS-5, NA-3, PP-1.
6. **Words and accessibility (#280, #281).** DC-4, DC-7, DC-8, R12, R13, NA-5. Once steps 2–5 have removed most of the old copy, add the “no raw identifiers” and catalog checks from proposal §10.
7. **Owner-gated.**
   - GH-66 lane, which still claims `PublishWorkspace.tsx` and `Board.tsx`: SS-1, SS-3.
   - #288: SS-8.
   - Q4: MC-5, R11.
   - The rest: BD-3 and R9, IA-7, PP-2 and PP-4 (a new perf issue).

**Owner decisions this review needs:**
1. The position word: Current / 当前, or 修改起点 (#284 Q2).
2. The name for Sync (SS-5).
3. Board auto-receive or explicit Send (BD-3).
4. One chat or two (Q4).
5. Where Fabrication belongs (IA-7).
6. Whether the tree keeps a List view (R15).

## 5. Checked, and not reported as problems

- The “Candidate — not endorsed, not issued” line is gone from both catalogs.
- Drawing already follows the Working Head by default (`DrawingCanvas.tsx:130-132, 144`); it is no longer limited to accepted Stages.
- Direct edits on a viewed model are refused, with Continue offered in place (`base:view-only-refusal.png`).
- The Modeling toolbar already hides Arc, Rotate, Scale, Copy, Measure, Fit and Front (`ModelToolButton.tsx:43-52`), so proposal §5's single row is close.

## 6. Not verified

- **Render UI.** `renderWorkspace.browser.mjs` stopped twice at “project-a Runtime ready” (20 s), so the Render items come from code.
- **Clicks on the leaked Board controls (NA-1).** The overlap is visible in the screenshots; whether the controls actually take clicks was not tested.
- **Measurements.** Load times (PP-1, PP-4) and colour contrast were not measured.
- **Publish at the default panel width** was not rechecked after the audit.
- **Scope of the evidence.** All screenshots come from fixtures. No real Hub, provider, packaged build or real project was used.

## 7. Follow-up status: #302 `view-not-base` (2026-09-25)

What the lane landed, by finding:

- **SS-7, SS-8, DC-6.** A Stage row opens that Stage read-only; Continue from here moves the base. A Board note on another model version asks first, with View only as the default. The catalogs use one verb per act: 查看 · 从这里继续 · 接受为下一 Stage.
- **SS-5 (name), SS-6.** Sync reads 记录 / Record. Each "sync first" refusal now offers 记录修改并继续 / Record edits and continue, which records the edits and retries the refused action. The restart prompt stays with #234.
- **FN-1, DC-2, R5.** A result no longer switches the surface, opens the panel or replaces the model. Each request gets one Study card whose View opens the Design Tree. The Stage chip shows "N 个方案就绪 · 查看".
- **R4 (work rows only), R13 (catalog names).** The project card's work lines are now one Open in Design tree link. The catalogs call the Board 画板.
- **R1, R3 (with a Design Tree), FN-3.** Where the runtime has a Design Tree, the footer's Stage count, viewing label and "new" badge become one Open in Design tree link. So do the Versions panel's Stages, lines, explorations and candidates. Versions keeps the working draft, recovery points, the first-Stage button and the files.

Still open:

- **R2.** The editing-base row stays: it carries Continue from here and Record edits and continue for a viewed model. The chip's viewing state offers only Back to Current.
- **Combine.** With a Design Tree, manual Combine has no entry until the tree offers it. The API and the Agent's combine step are unchanged.
- **R3 without a Design Tree.** Runtimes without `working-source` keep the old design section.
- **Board.tsx and ModelPreview.tsx.** Their hard-coded 画布, 图墙 and 同步 copy belongs to GH-66.
