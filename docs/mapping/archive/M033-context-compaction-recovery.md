# M033 — Context compaction recovery

- Origin: Modify
- Status: Ready after P046
- Depends on: P046, M013

## Goal

Make Codex history compaction recoverable without treating a transcript,
generated summary, hook output, or temporary file as project authority.

## Boundary

```text
active work card
  -> P046 read-only context capsule
  -> PreCompact recoverability check
  -> Codex compaction
  -> SessionStart(source=compact)
  -> bounded recovery context for the immediate continuation
  -> re-check current card, git state, and verification evidence before edits
```

M033 does not increase or guess a model context window, persist chat history,
read the unstable transcript format, alter project records, select an active
work card, or make hook output evidence. It writes no checkpoint file.

## Write scope

- `.codex/config.toml`
- `.codex/hooks/context_recovery.py`
- `tests/test_context_recovery_hook.py`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- Project config counts compaction growth after the carried prefix and supplies
  a preservation prompt, without setting a guessed context window or numeric
  auto-compaction threshold.
- `PreCompact` regenerates the P046 capsule for every active work card and
  stops compaction when exact recovery context cannot be built.
- `SessionStart` with `source=compact` injects bounded developer context before
  the immediate continuation, including current commit, dirty path names,
  active-card goal, scope, acceptance, tests, stop conditions, evidence status,
  and capsule digest.
- The hook reads neither `transcript_path` nor raw history and writes no project,
  cache, checkpoint, or temporary artifact.
- Hook output labels planning claims and hook context as non-authoritative and
  instructs the Agent to refresh current state before modification.
- Output is deterministic for fixed registry and git state, path-portable,
  secret-free, and bounded independently of transcript size.
- Project-local hooks remain subject to Codex trust review and fail closed on
  invalid input, missing repository contracts, or capsule-generation failure.

## Tests

- Project config TOML and exact hook matcher contract.
- PreCompact success, invalid-event rejection, and read-only behavior.
- Compact SessionStart bounded recovery content and authority disclaimer.
- No transcript ingestion, absolute machine path, secret, or project write.
- Architecture firewall and full unittest discovery.

## Stop conditions

- Stop if recovery requires parsing the session transcript.
- Stop if the hook writes a canonical or durable checkpoint.
- Stop if hook output can claim verification or completion authority.
- Stop if a guessed model window or auto-compaction token threshold is needed.
- Stop if more context is injected than the bounded P046-derived contract.


## Completion

- Completed: 2026-08-08
- Evidence: Added fail-closed PreCompact capsule verification and compact SessionStart bounded recovery from P046; four focused hook tests, architecture firewall, and full unittest discovery passed without transcript ingestion or checkpoint writes.
