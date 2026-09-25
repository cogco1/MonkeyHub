# GH-60 legacy card closeout

Issue: https://github.com/cogco1/MonkeyHub/issues/60 — GitHub Issues are the canonical work items; P/M/R numbering is frozen.
Base: `04e719617d6aa20507b5c1846de3059b5c57a451`.

Close out the P108 and P115 registrations so a historical card or a broad write scope is not read as a current lock. Release delivered execution scope, keep still-valid product acceptance findable through the Issue that now carries it, remove obsolete waits and pauses, and state unknowns briefly. Point live-work lookup and onboarding at GitHub Issues and active lanes instead of deriving current tasks from P115.

Existing registry and card files are edited in place; no second backlog, migration ledger or governance document set is added. Historical commit scope checks keep reading the registry recorded in each commit.

## End state

- **P115** stays registered as a blocked, frozen historical index. Its lanes had all closed, so the empty `lanes` list and the 386-path historical `write_scope` are removed; `devctl status` and `devctl work` now show the same blocked card. The card names the open Issues that carry the remaining acceptance and lists the questions left open. It is not removed because ARCHITECTURE.md, the Stage plan and the P111 card still link it.
- **P108** stays registered as a blocked legacy card with no path claim. Its delivered Studio work is Git history; the independent first-user trial is carried by #86 and real-revision acceptance by #185. It stays blocked on one owner decision: whether the ARCHITECTURE stair and side-passage trial is still wanted.
- The obsolete P115-migration wait in P105 and the P115 bookkeeping note in P111 are corrected; entry docs now start new work from the Issue and treat P115 as history.
- `devctl work <card>` printed `blocked_reason: unassigned` for a blocked card whose reason is in its card; it now prints `see card` (covered in `tests/test_devctl_work.py`).
