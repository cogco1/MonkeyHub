# ADR-010 — An admission is the Agent's registration of a finished loop, not the architect's endorsement

**Decision (Kaiwen, 2026-09-29, #404 item 10):** the Hub Agent closes each completed modeling loop with one admission (`POST /api/admissions`, task `hub-chat`).
- The admission registers the result the loop produced and the attempts it superseded.
- It is attributed to `hub-agent`, bound to the user message the Agent acted on, and shown as "智能体（按你在对话里的话）" (#404 F6).
- It does **not** stand for the architect's endorsement.

The architect's own judgments stay separate and attributable:
- rejecting a result needs their words;
- Continue moves the Working Head only on their words;
- accepting a Stage and formally issuing a version are their explicit acts.

The Agent may withdraw a result it made in the same chat (`withdrawn`, #464). That is its own act, never a rejection.

**Why:** registering every finished loop keeps the design tree complete without asking the architect to approve intermediate results. The distinctions the vision asks for (continuing a candidate, endorsing a direction, formally issuing) are carried by Continue, Stage acceptance and issue, not by making an admission mean approval.

**Do not:**
- ask for the user's words or approval before an ordinary admission;
- show an Agent admission as the architect's;
- read an admitted result as an endorsed direction or an accepted Stage;
- let a withdrawal stand in for a rejection.
