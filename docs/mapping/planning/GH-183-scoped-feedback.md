# GH-183: consume scoped feedback

Issue: https://github.com/cogco1/MonkeyHub/issues/183

Active on `codex/183-decision-consumers`, based on GH-185 PR #238 at
`9358d8c63132c25f1de0f26ce33246e616c9d706`. The Runtime decision owner is not
modified by this task. Its exact source records remain the only persistence.

Extend the existing Hub chat adapter to bind avoid/keep feedback to the actual
user message and revoke those records through the same Runtime authorization.
Model interpretation stays attributed to the agent. This does not accept a
Stage, change HEAD, grant a parameter lock or require a second grant prompt.

Verify copy, design keep and hatch (explicit defer if unsupported), including a
fresh real provider session, actual next artifact readback, scope mismatch and
revocation. Reuse the existing Monitor trace and provider fixture helpers.
Do not rerun the completed #202 courtyard work or alter event gating (#204).

Exact source write scope is registered in `governance/work_registry.json`.
Run focused feedback/chat tests, the new opt-in provider acceptance, and
`python tools/archcheck.py` before delivery.
