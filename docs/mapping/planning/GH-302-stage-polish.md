# GH-302 stage-polish

Issue: https://github.com/cogco1/MonkeyHub/issues/302
Base: `acb391ef` (batch DE).

Modeling stays steady: Record appearing does not shift the toolbar, a hidden workspace's loading line never shows through, and the Board intent handoff test runs again.

Batch F (2026-09-25 afternoon); none touches App.tsx, the i18n catalogs (GH-244) or GH-66's files.

## Landed in the stage-polish lane

- **Toolbar steadiness.** Record sits outside the tools' row, so its coming and going moves no tool. With room beside the tools, it is a capsule of its own against their right edge. Without it, it is the toolbar's first line with its status inline, and `contain: inline-size` keeps it from widening the tools. Stage measures the room (`RECORD_FRAME`, a ResizeObserver). Select and the view-options button keep their x and y with and without Record at 1440, 900, 760, 700, 560 and 420 px. modelSync asserts Select's x as Record appears and leaves in all three modes. At 700 px it also checks that Record goes above without moving or covering a tool.
- **Hidden loading text (NA-1).** The active BilingualText layer inherits `visibility` and `pointer-events` instead of forcing `visible` and `auto`. A loading line in a project the Hub hides by visibility now hides with it, as does one in Stage's covered model. The two layers still share one cell, and the inactive one still hides on its own. modelSync checks the held boot overlay's waiting line.
- **Board intent handoff test.** The failure on main was the test, not the product. Its fixture answered every `/api/state` read with the drawing's projection and expected a `view=documents` URL. Since the fold into MonkeyHub, Modeling is mounted behind the Board and opens on Current. The session therefore refused the head projection (`EDITING_PROJECT_CHANGED`, worded "another project"). The test now follows the intended flow: the note hands over in place, and on another model version it asks first, with View only focused. Continue from here moves the base to the note's exact model and Stage and submits the note once. compiled, clarification, changed-model and wrong-project pass.
- modelSync tolerates a busy machine:
  - It waits for the API while its process lives.
  - Navigations and the held boot overlay wait out Vite's first dependency scan.
  - The first authored-only Record gets the other modes' 120 s.

## Open

- **A refused Board note loses the Board's words** (`App.tsx`, GH-244's file):
  - When `changeEditingBase` returns null, `continueDocumentIntent` throws a plain `Error`. The switch's own `StudioApiError` is published only as `baseError` and never reaches the note's refusal. An unavailable model therefore shows "This step did not finish" instead of "This drawing's saved model could not be opened…".
  - A Stage mismatch refuses the whole session, and the base-restore page renders `<ErrorPanel error={error} />` without `what="MonkeyBoard"`. It says "This step belongs to another project" for the same project.
  - boardIntentHandoff's changed-stage and unavailable-base scenarios fail on exactly this, and only on this.
- `apps/monkeyhub/web/test/chatShell.browser.mjs` (GH-300) still waits for a loading "Parsing" line under the hidden panel to clear. The line no longer shows through, so that wait can go.
- boardDocumentOpen (not this lane) fails at line 379 on the base as well. It calls `isEnabled()` on Record after Undo, but since batch DE Record is removed when nothing is left to record. Its 15 s API budget is also short now that `publication_output.py` imports `pptx` at startup.
- On an idle machine, modelSync's first authored-only Record is refused with 409 `WORKING_DRAFT_STALE`: an autosave `PUT /api/working-draft/local` lands right after the candidate POST. It fails the same way at the base `cfc8edd9`.
