# GH-363

Issue: https://github.com/cogco1/MonkeyHub/issues/363
Base: `c4ed9500`.

Take 0a's project fingerprint off the request path (#363, ADR-008): a per-process background watcher keeps the fingerprint current from Windows directory-change notifications (ReadDirectoryChangesW via ctypes, recursive on the project root), recomputing only the directories that changed; requests read the latest computed token in O(1) and never scan; a missed or overflowed watch falls back to an incremental scan, plus a slow safety-net scan; the racy rule stays. Split out of 1b by the owner so the fix lands before the index.
