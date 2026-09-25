# GH-284 tree-actions

Issue: https://github.com/cogco1/MonkeyHub/issues/284
Base: `e7fd526e` (batch D claim).

Design Tree actions confirm themselves: Continue shows a toast with Undo that restores the previous Current, Accept shows a toast without Undo, and while another model is viewed the Stage chip offers Continue from here and Back to Current.

Batch E (2026-09-25 afternoon), parallel with batch D; none touches App.tsx, ChatShell.tsx or the i18n catalogs.

## What the lane delivers

- **FN-5, Continue** (`features/designTree/DesignTreeToast.tsx`, `DesignTreeBar.tsx`). After a successful Continue, from the side card or the chip, a toast beside the Stage chip reads 当前已改为「label」· 撤销 / Current is now “label” · Undo. It sits in one live region in the bar, so it shows on every surface. It fades after about 8 s. Hover, keyboard focus or a running Undo keeps it, and it lingers 2 s after release.
- **Undo** (`continueUndo.ts`, `useDesignTree.ts`). Undo goes through the same Continue path, `PUT /api/working-draft`, onto the previous Current. That is the working-position entry (run and line) the Continue wrote over, recorded from the position the Continue read. It shares `moveHead` with Continue: unrecorded edits refuse it, and a stale write re-reads up to three times. After Undo the toast reads 已撤销 · 当前已回到原处 and offers no second Undo.
- **FN-5, Accept**. After Accept as next Stage the toast reads 已接受为 S{n} / Accepted as S{n}, with no Undo. S{n} is the label the confirm promised.
- **R2, chip half** (`DesignTreeBar.tsx`). While a model other than Current is open read-only, the chip offers 回到当前 / Back to Current and 从这里继续 / Continue from here. Continue runs the tree's Continue, so its toast and Undo are the same. A View opened from the tree carries its node id (`DesignTreeView.node`); other views resolve to the run's Stage node, else its option node.
- **Refusals** are never toasts. The side card keeps its inline message. The chip shows its own refusal on a row of its own in the bar. A refused Undo says why in its toast.

## Decisions

- **No Undo rather than a guess.** If no working-position entry named the previous Current (the line's accepted Stage or the reference run answered for it, `current: null`), there is no Undo: returning there is the other act, `runId: null`. A Continue onto the run Current already stood on has no Undo either.
- **Undo applies only while Current is still on the continued run.** Otherwise it refuses with `DESIGN_TREE_UNDO_MOVED`, for example after another window moved Current or after edits were recorded on it. Edits made since the Continue read the same way in the toast.
- **One confirmation per act (Π6).** The side card's inline success lines (`designTree.outcome.continued` and `.accepted`) retire in favour of the toast. Both catalog keys are now unused; GH-244 can drop them.
- **Continue from here in the chip** is offered only for runs the tree has a node for, and only when the runtime can move Current. A viewed run that is the Working Head is Current, so the viewing state ends when the chip's Continue lands.

## Local copy (moves into the catalogs with GH-244)

`words.ts` `ACTION_COPY`: continued 当前已改为「{name}」 / Current is now “{name}”; undo 撤销 / Undo; undoing 正在撤销… / Undoing…; accepted 已接受为 {stage} / Accepted as {stage}; undone 已撤销 · 当前已回到原处 / Undone · Current is back where it was; undoMoved 继续之后当前已有变化，未撤销。 / Current has changed since the Continue; nothing was undone.

## Tests

- `designTree.test.ts`: Undo is the same request onto the replaced entry and restores the trunk; no Undo for a line-head Current or a Continue that moved nothing; Undo refused once Current moved on; toast and refusal copy in both languages.
- `designTree.browser.mjs`:
  - the Continue toast, and its Undo sending the previous source through `PUT /api/working-draft`;
  - the Accept toast without Undo;
  - hover keeps the toast, and it fades once released;
  - no toast on a refused Continue, from the card or the chip;
  - the chip's viewing state offering both actions, with its Continue and Undo.

## Hand-off (outside this lane's scope)

- `ProjectWorkspace.tsx`: pass `onRecordEdits={recordEdits}` to `DesignTreeBar`. Until then, a chip Continue refused for unrecorded edits shows the refusal without the one-click Record edits and continue.
- R2's other half: with the chip carrying Continue from here, the editing-base row in `features/stage/Stage.tsx` and `App.tsx` can retire.
