# GH-58 update-status

Issue: https://github.com/cogco1/MonkeyHub/issues/58
Base: `4bd8ec32`.

The update status says what is actually true: a prepared, applicable update is never reported as failed, and Settings words each state plainly.

Batch D (2026-09-25 afternoon): three parallel lanes with disjoint scopes; none touches App.tsx or the i18n catalogs, which GH-244 claims.

## Cause (reproduced 2026-09-25)

A private copy of 0.1.421 checking the real channel showed the owner's sequence: ready with the check still downloading at 52 s, then failed (`UPDATE_PREFLIGHT_FAILED`, timed out after 180 s) with `canApply` still true. The trial load of the new runtime (`run.py --help`, `updates.py` `_preflight`) inherited the Hub's stdin; the desktop starts the Hub with `--managed-stdin`, whose watcher always waits on that pipe, and on Windows a Python child that inherits it does not start until the read returns. Every packaged check therefore failed its trial load, so no automatic update was ever switched in at quit. The status also reported the record ready before the trial load and computed `canApply` without the failure.

## Result

- The trial load and the installer get `stdin=DEVNULL`; the trial load is part of preparing, so the record is ready only after it passes, or failed with its reason.
- The check reports downloading only while downloading, then checking until it ends ready, up to date, needs a full update or error with its reason.
- Status: ready means applicable; a failed, undone or unfinished transaction is failed with its reason and no prepared update, restart or next launch; a later failure keeps an earlier prepared update ready with the reason in `error`.
- Settings: one state line with its next action (Restart to update, Check again); local zh-CN/en copy until GH-244 releases the catalogs.
- Left: `POST /api/updates/apply` still accepts a failed transaction as an explicit retry (existing test), though the status no longer offers it; download progress needs a schema field.
