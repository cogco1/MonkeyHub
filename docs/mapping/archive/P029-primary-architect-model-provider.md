# P029 — Primary Architect model provider

- Origin: Planning
- Status: Done
- Depends on: P022, P028

## Goal

Connect one real asynchronous reasoning/model provider to the Primary Architect
protocol so it can read `D_v,k`, discover bounded capabilities, call CLI/MCP as
needed, and propose the next exact-base design transition.

## Write scope

- `archflow/adapters/model_provider.py`
- `archflow/runtime/primary_architect.py`
- `archflow/adapters/README.md`
- `archflow/runtime/README.md`
- `tests/test_primary_architect_provider.py`
- `tests/integration/test_primary_architect_smoke.py`
- `docs/mapping/`

## Acceptance

- The provider receives a bounded detached `D_v,k`, not the complete transcript
  or canonical writer.
- Tool/capability discovery is dynamic and no building-specific prompt template
  carries dimensions, rooms, topology, palette, or coordinates.
- Model output is a proposal; deterministic transition logic decides whether it
  becomes the next design state.
- Timeout, malformed output, repeated plan, token/budget exhaustion, and
  provider failure are bounded and reloadable.
- One explicit live smoke proves actual model invocation without claiming
  architectural Gold.

## Tests

- Fake provider proposal and exact-base binding.
- Dynamic capability-discovery response.
- Prompt-template instance-default static scan.
- Explicit bounded live-model smoke.

## Current evidence

- The provider-neutral asynchronous JSON command boundary and two-stage Primary
  Architect runtime are implemented.
- A concrete Codex Agent CLI JSONL bridge now runs ephemeral and read-only in
  a temporary empty working directory, ignores user configuration and exec
  rules, binds CLI-reported token usage, and remains behind the provider-neutral
  protocol reserved for a future API adapter.
- Offline tests prove dynamic capability selection, detached expert receipts,
  exact-base proposal compilation, repeated-plan stop, reloadable receipts, and
  bounded timeout, exit, malformed, byte, and token failures.
- The official npm Codex CLI `0.145.0` is installed as a user-level command,
  reuses ChatGPT authentication, and is configured through non-secret user
  environment variables for the `codex_exec_jsonl` smoke protocol.
- The explicit live smoke passed with `gpt-5.6-sol`; the synthetic request
  carried no private project data and the provider retained no write authority.

## Stop conditions

- Stop before transmitting private data not placed in scope.
- Stop if provider output can commit or write the world directly.


## Completion

- Completed: 2026-07-28
- Evidence: Implemented provider-neutral Codex Agent CLI JSONL bridge with ephemeral read-only isolated execution, trusted CLI token usage, bounded failures, and no canonical or world-write authority.
- Evidence: Official npm codex-cli 0.145.0 authenticated with ChatGPT; explicit gpt-5.6-sol live smoke passed on a synthetic non-private request; focused, full, compileall, architecture, and scope verification passed.
