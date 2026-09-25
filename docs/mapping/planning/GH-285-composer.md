# GH-285 composer

Issue: https://github.com/cogco1/MonkeyHub/issues/285
Base: `4bd8ec32`.

The chat composer says what a message will change, keeps drafts across restarts, offers New topic from the + menu instead of a standing checkbox, says Sending while it sends, and the unfinished-operation notice names the operation and can be cleared.

Batch D (2026-09-25 afternoon): three parallel lanes with disjoint scopes; none touches App.tsx or the i18n catalogs, which GH-244 claims.

## Landed in the composer lane

- **DC-5.** The standing checkbox is gone. The composer's + opens a menu: Add attachments, and New topic, a checked entry. While chosen, New topic is one removable chip above the input and applies to the next send as the checkbox did. A refusal it meets stays visible, with Record edits and continue for unrecorded edits.
- **DC-9.** A target label above the input: "Changes: Current", plus Current's place in the Stage chip's own words where the runtime has a Design Tree ("3 edits after S2"), and "unrecorded edits not included" when they are not. While the tree has another model open read-only it says so; the message still changes Current. The shell has no Continue path of its own, so it only says it. `ProjectWorkspace` reports the chip's position through `onPositionChange`.
- **PP-3.** A send says Sending…; creating its chat and connecting its project keep "Connecting the project…".
- **SS-9.** Unsent text is kept per conversation in local storage (every access guarded; newest 50) and comes back after a reload or a restart. It no longer holds the update restart; chosen attachments, and text storage refused, still do. Sending clears it.
- **Unfinished-operation notice.** It names the operation in words, its admission time when the record has one, how many others wait, and links the chat that asked. Dismiss calls `POST /api/runtime/operations/{operation_id}/acknowledge`; the Hub keeps the dismissal in the runtime's operation journal, so it survives a restart. One that needs recovery cannot be dismissed. New operation records carry `createdAt`.

New copy lives in `composerWords` in `ChatShell.tsx` until the catalogs are free.

## Open

- The generated Hub client (`apps/monkeyhub/web/src/api/generated`) is outside this lane: `npm run api:generate` adds `createdAt`, `acknowledgedAt`, `OperationAcknowledgeRequest` and the acknowledge call. `ChatShell.tsx` reads the two fields through a local `OperationRow` type until then.
- `updateDraftsBlocked` (catalogs, `SoftwareUpdateSettings.tsx`) still says "unsent text or attachments"; it now means attachments, or text the browser could not keep.
- A request that needs recovery and has no candidate (an interrupted `POST /api/proposals` or `POST /api/drawings/sheets`), or a retained run without a receipt, has no recovery path, so its notice stays.
