# GH-291

Issue: https://github.com/cogco1/MonkeyHub/issues/291
Base: `bf80accc` (batch H2 wave 1, after batch H1).

Render follows a rebuilt drawing through its registered replacement instead of its own front-end fallback.

Batch H2 (2026-09-25), in the owner's order; plans are kept outside the repo.

## Lane `render-consumers` (291-S2)

- Landed: Render's "Use updated source" reads only `pageReplacements`. The cut-plan fallback that took the drawing's newest revision (`latestRevisions`) is gone; a drawing source with no registered replacement is told to rebuild it in Drawing or choose an updated image. `boardReplacement.browser.mjs` places a cut plan's first revision, marks it and receives its rebuild in place with one notice. `renderWorkspace.browser.mjs` renders from an imported-model plan: a reshaped revision, which registers nothing, is not taken; a rebuild on another source is, through its replacement. PROTOCOL's Render `sourceState` names the representation-only exception and where an updated source comes from.
- Open: no browser step reaches the drawing message; it needs a model-bound plan that went outdated on the Working Head without a rebuild.
