# P059 — Live provider evidence and recovery

- Origin: Planning
- Status: Done
- Depends on: P056, M050, P058, M052

## Goal

Turn the configured Agent CLI seam into honest, recoverable production
evidence while machine-proving that CLI and future API-shaped providers share
the same P053-authorized request and receipt contract.

## Write scope

- `archflow/adapters/model_provider.py`
- `archflow/production/provider_runtime.py`
- `archflow/project/production_transition.py`
- `archflow/runtime/production_compiler.py`
- `archflow/runtime/production_runtime.py`
- `archflow/project/runtime.py`
- `tests/test_primary_architect_provider.py`
- `tests/test_production_responsibility.py`
- `tests/test_production_transition.py`
- `tests/test_production_root_compiler.py`
- `tests/integration/test_primary_architect_smoke.py`
- `probes/p059-provider-runtime/`
- `probes/p059-provider-runtime-live/`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- Every model receipt records bounded wall-clock duration alongside exact
  provider, model, version, fingerprint, request, status, byte, token, and
  output/error evidence; historical receipt records remain explicitly
  readable without being rewritten.
- A P053-validated timeout, offline, exit, malformed, oversized, or budget
  failure is retained through P036 as a failed production attempt with no
  lifecycle successor, transition checkpoint, fallback, or canonical write.
- Reloading a failed attempt reproduces its exact logical evidence and permits
  a separately identified retry; it cannot masquerade as a completed
  production transition.
- Command-backed Agent CLI and an API-shaped `AsyncModelProvider` pass one
  machine-readable conformance contract and receive authority only through
  P053 activation.
- The existing 120-second timeout is diagnosed before retry. At most one new
  explicitly configured live invocation is made after deterministic gates;
  its success or failure is persisted and reported without inflation.
- Any promoted live probe contains only project input and P036-produced
  evidence. No credentials, absolute paths, case Python, or external-platform
  claims enter the probe.

## Tests

- Model receipt duration, legacy read, status, budgets, malformed output, and
  Codex JSONL parsing.
- CLI/API-shaped provider conformance, P053 identity binding, no fallback, and
  stale envelope rejection.
- Failed-attempt persistence, reload, retry separation, orphan handling, and
  successful-transition recovery.
- One opt-in live Agent CLI invocation only after deterministic verification;
  architecture firewall, V3 boundary, scope, diff, and full unit discovery.

## Stop conditions

- Stop before increasing a timeout or retry count without new diagnostic
  evidence.
- Stop if failed execution becomes a completed transition or gains canonical
  write authority.
- Stop before persisting credentials, machine paths, raw transcripts, or an
  unbounded provider response.
- Stop before claiming API service execution when only the shared provider
  contract has been exercised.

## Current evidence boundary — 2026-08-17

- Deterministic implementation is present: receipt duration and legacy reload,
  CLI/API-shaped P053 contract parity, P036 failed-attempt reload and retry
  separation, completed-intent exclusion, and successful recovery all pass.
- The shell already carried the earlier live-smoke command variables. Full
  discovery therefore executed the old opt-in smoke before the new persistence
  harness was installed. The test passed, which proves its bounded success
  assertions, but the exact model receipt was not retained and must not be
  reconstructed after the fact.
- A later preflight started another smoke before this inherited opt-in was
  noticed; it was interrupted immediately. The control deviation is retained
  as `ProviderLiveControlIncidentReceipt@1` under
  `probes/p059-provider-runtime/`, with `production_evidence_complete=false`
  and no model, building, API-service, transition, or canonical claim.
- Live smoke now requires both `ARCHFLOW_MODEL_SMOKE_ALLOW_LIVE=1` and a fresh
  absolute P036 project root. Project initialization occurs before provider
  invocation, so an existing root prevents replay. A local command-backed
  contract test proves one successful P053 envelope is retained through P036,
  an existing root prevents a second subprocess call, and a malformed provider
  response becomes a reloadable failed attempt rather than completion.
- After explicit additional authorization, exactly one fresh Agent CLI smoke
  ran with the existing 120-second limit, `gpt-5.6-sol`, Codex CLI 0.145.0,
  and low reasoning. It succeeded in 116,515 ms with 537 input bytes, 976
  output bytes, 18,606 input tokens, and 47 output tokens. P053 envelope digest
  `0de1451fb4298c1508de44c3e93c46549d7d39f3c5e0b83c003596ef54b626a6`
  is retained at
  `project://p059-provider-runtime-live/runs/live-agent-cli-001/records/provider-live-invocation-5539b49bc4f6c696c929182f68ff7108d10e8138d213e7bee65258514940f581.json`.
  The P036 project remains at HEAD version 0. The receipt explicitly makes no
  API-service, building-execution, lifecycle-successor, transition-checkpoint,
  or canonical-write claim.


## Completion

- Completed: 2026-08-17
- Evidence: Agent CLI 0.145.0 gpt-5.6-sol live smoke succeeded once under the unchanged 120-second limit in 116515 ms; P053 envelope and ModelInvocationReceipt@2 are retained through P036 at project://p059-provider-runtime-live/runs/live-agent-cli-001/records/provider-live-invocation-5539b49bc4f6c696c929182f68ff7108d10e8138d213e7bee65258514940f581.json with HEAD v0 and no API-service building lifecycle checkpoint or canonical-write claim. Deterministic CLI/API contract failure retry and replay guards pass; 539 full tests with 3 skips architecture V3 scope and diff checks pass.
