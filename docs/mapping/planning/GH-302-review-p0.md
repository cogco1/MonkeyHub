# GH-302 review P0 follow-ups

Issue: https://github.com/cogco1/MonkeyHub/issues/302
Base: `7b9435d5` (batch B, `codex/batch-b-0925`).

Two parallel lanes with disjoint write scopes:

- `view-not-base`: agent results do not take the view, viewing never moves the editing base, Sync becomes Record with one-click record-and-continue, one Board vocabulary, parallel history entries retire. Owns the i18n catalogs.
- `autosave-chrome`: Hub settings and Drawing pages autosave, the canvas zoom bar stays inside its surface. Changes no catalog file.

`Board.tsx`, `PublishWorkspace.tsx` and `DrawingDressing.tsx` belong to GH-66 and are not changed here.
