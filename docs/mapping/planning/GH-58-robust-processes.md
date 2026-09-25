# GH-58 robust-processes

Issue: https://github.com/cogco1/MonkeyHub/issues/58
Base: `acb391ef` (batch DE).

No Hub child process inherits the Hub's stdin, and an interrupted operation with no way to recover is reported as such and can be acknowledged.

Batch F (2026-09-25 afternoon); none touches App.tsx, the i18n catalogs (GH-244) or GH-66's files.

## Landed in the robust-processes lane

- **Child stdin.** Every process the Hub package starts names its child's stdin. The checkout revision read (`applications.py`, also decoding git's UTF-8 explicitly) and the two `taskkill` calls that stop a chat's process tree (`chat.py`, `acp_session.py`) now pass `stdin=DEVNULL`; the others already passed DEVNULL or a pipe the Hub owns. With a read pending on the Hub's stdin, a `git rev-parse` that inherited it outlasted its 5 s timeout and the Hub lost its source version. `test_applications.py` scans the package's syntax trees (subprocess, asyncio and os starters, direct or handed to `to_thread`) and fails on any start that does not name a stdin, unless `INHERITS_HUB_STDIN` says why; a Windows test reads the revision while the Hub's stdin is waited on.
- **Recoverable operations.** Operation rows carry `recoverable`: true only while a needs_recovery operation's retained evidence can still resolve it, meaning the run it named with that run's runner receipt, and for an acceptance a named parent Stage its branch has not moved past. A request that named no run (`POST /api/proposals`, `POST /api/drawings/sheets`) never is. Before a read after the lost reply or the Hub's start, one that named a run counts as recoverable. The acknowledge route now also takes an unrecoverable needs_recovery operation and still answers 409 for a recoverable one. The journal keeps each dismissal with the status it read (older entries keep their failed or stale meaning), so it outlasts restarts and ends when the operation reads otherwise; a dismissed needs_recovery row is no longer pinned ahead of finished ones.

## Open

- Outside the Hub package, children still inherit a managed stdin that has a read pending on it. In the Hub process: `monkeycontrol/record.py` (ffmpeg for `POST /api/computer/recordings`). In the managed Studio worker: `archflow/adapters/blender_cad.py` (Blender), `archflow/adapters/cad_execution.py` (Rhino export and cleanup PowerShell) and `archflow_studio_api/application/intent_agent.py` (`codex --version`, `taskkill`). The startup revision reads in Studio and Monitor run before their stdin threads start.
- Offering Dismiss for an unrecoverable row is GH-300/needs-you's. Until that lands, `unfinishedOperations` in `chatProcess.ts` lists every needs_recovery row, dismissed or not, and `ChatShell.tsx` offers no Dismiss for one.
