# GH-234 autosave and update restart

Issue: https://github.com/cogco1/MonkeyHub/issues/234
Base: `e183a69a`.

Settings > Software Update refused to restart with "sync or save" while the model edits were already in the project's working draft. Split the workspace reason: a draft the working draft holds is `unsynced` and does not block the restart (reopening restores it); edits whose autosave is still being written, failed or is unavailable stay `unsaved` and do. Both keep the project-state chat context unavailable.
