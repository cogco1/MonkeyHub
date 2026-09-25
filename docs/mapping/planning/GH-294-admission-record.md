# GH-294 candidate admission

Issue: https://github.com/cogco1/MonkeyHub/issues/294

Lane `admission-record` (done) implemented slices S1 (the fact and the gate) and S2 (the readers and the #284 API) of [the admission audit](../../2026-09-25-candidate-admission-audit.md) on the owner's 2026-09-25 decisions Q1–Q4: `CandidateAdmission@1` in the fixed `studio-admissions` run, `POST`/`GET /api/admissions` sharing the Stage acceptance preflight, `candidates[]` and `studies[]` on design history with legacy derivation, admission on Worktree result lines, a warning on a rejected Working Head, and retired Exploration creation.

Lane `agent-admission` implements S3 (Hub Agent contract) and S4 (attributed Continue) on decision Q3, bounded automatic Agent authority. Base: `7cc3aef9` (batch A with GH-301 steering, plus S1 and S2), merged with the published batch A (`9a0d6d3e`).

- The chat tool lets the Agent `POST /api/admissions` as `hub-chat` and `PUT /api/working-draft`. Hub binds `messageSource` to the last user message the Agent was given, and `rawLanguage` where the user's words carry the decision: a quoted passage, a rejection, and every Continue. Provider-supplied provenance, another task kind and a Continue without the user's message or words are refused.
- The Hub prompt asks for one admission per completed loop: a declared Study for several alternatives, each result's superseded attempts, never intermediate runs, and a rejection or Continue only on the user's own words.
- Every Continue retains `AuditEvent@1` `design.continued` beside its run with ids only. The Agent's Continue needs a Hub-managed Runtime. A Continue admits nothing, the acceptance reader ignores the event, and generation never moves the head.

The ChatShell half of the audit's S3 row (auto-open only admitted results, "open result" wording) is not in this lane.
