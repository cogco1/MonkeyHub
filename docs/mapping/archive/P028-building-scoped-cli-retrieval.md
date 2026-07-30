# P028 — Building-scoped CLI retrieval

- Origin: Planning
- Status: Ready after M007
- Depends on: P019, M007

## Goal

Expose bounded CLI research as a building-scoped evidence capability. Queries,
providers, versions, excerpts, uncertainty, and digests become receipts for the
current run; retrieved answers never become framework defaults.

## Write scope

- `archflow/adapters/cli_retrieval.py`
- `archflow/capabilities/retrieval.py`
- `archflow/adapters/README.md`
- `archflow/capabilities/README.md`
- `tests/test_cli_retrieval.py`
- `tests/integration/test_cli_retrieval_smoke.py`
- `docs/mapping/`

## Acceptance

- Each query binds building ID, exact design-state base, provider, command,
  version/fingerprint, bounded output digest, and evidence references.
- Retrieval is read-only and cannot write `D_v,k`, `C_v`, or a world.
- Failure, timeout, malformed output, oversized output, and missing provider
  produce named receipts with no silent fallback.
- Retrieved facts remain hypotheses until compiled and authorized.
- Two building runs cannot share retrieved instance answers without an explicit
  evidence reference.

## Tests

- Deterministic fake CLI receipt.
- Timeout, malformed, oversized, and offline cases.
- Cross-building evidence-isolation check.
- Explicit opt-in live CLI research smoke.

## Stop conditions

- Stop before uploading private files or calling an unapproved remote service.
- Stop if retrieval output bypasses the brief/commitment compiler.

## Implementation evidence

- Added a shell-free JSON CLI adapter bound to project, run, exact base,
  provider id/version/fingerprint, command, output digest, and evidence.
- Successful results remain `hypothesis`; the adapter exposes no canonical,
  design-state, project-repository, or world writer.
- Timeout, offline, non-zero exit, malformed schema, oversized output, and
  missing provider return named receipts with no fallback.
- Retrieval discovery is topic-responsive metadata rather than a fixed
  provider schedule.
- Five focused tests cover success, bounded failures, missing-provider
  behavior, cross-project isolation, and malformed authority fields. One
  explicit opt-in live-provider smoke is available through environment
  configuration.
- The opt-in smoke was executed against the public Wikipedia API through the
  configured CLI command and returned bounded cited hypothesis evidence; no
  project files or private data were sent.


## Completion

- Completed: 2026-07-25
- Evidence: Implemented project/exact-base JSON CLI retrieval with provider command/version/fingerprint, bounded hypothesis evidence, topic discovery, named timeout/offline/exit/malformed/oversized/missing-provider receipts, no fallback or writers, cross-project isolation; 112 full tests pass with optional smoke skipped, and a separate explicit public Wikipedia CLI live smoke passed.
