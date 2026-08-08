# P052 — External project runtime environment

- Origin: Planning
- Status: Done
- Depends on: M006, P035, P036, P037

## Goal

Add an explicit, cross-platform external runtime environment for active
ArchFlow projects without changing project identity, persistence authority,
canonical `HEAD`, event ordering, or the role of committed probes.

Active projects use an injected runtime configuration. The existing P036
repository remains the sole project-document writer and preserves the exact
project envelope under `workspace/projects/<project_id>/`. Repository probes
remain compact, explicitly promoted evidence; existing probes are not moved or
rewritten by this card.

The source comparison and city-to-building mapping are recorded in
[`../P052-zoning-city-to-building-runtime.md`](../P052-zoning-city-to-building-runtime.md).

## Boundary

```text
Git checkout
  -> reusable code, tests, governance, compact promoted probes

explicit RuntimeConfig@1
  -> workspace/projects/<project_id>/
       -> existing P036 project envelope and sole canonical HEAD
  -> cache/
  -> temp/

active external project
  -> explicit review/promotion
  -> committed probe evidence (separate operation, not automatic)
```

## Write scope

- `.codex/cloud/`
- `.devcontainer/`
- `.github/workflows/verify.yml`
- `.gitignore`
- `AGENTS.md`
- `README.md`
- `pyproject.toml`
- `archflow/project/`
- `config/`
- `governance/architecture_policy.json`
- `governance/work_registry.json`
- `tests/test_project_runtime.py`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`

## Acceptance

- A typed `RuntimeConfig@1` supplies absolute, distinct workspace, cache, and
  temp roots and rejects roots inside the source checkout.
- Project roots resolve only to
  `workspace/projects/<project_id>/`; cache and temp paths remain explicitly
  non-canonical.
- Runtime initialization creates only the assigned external roots. Building
  records are still created through `FilesystemProjectRepository` and its
  existing bootstrap/ports.
- One external project initializes, verifies, reopens, and retains exact
  `HEAD` identity without writing a project into the Git checkout.
- Existing probes remain unchanged and continue to load as frozen regression
  evidence.
- Codespaces and Codex Cloud use environment-specific, credential-free config
  files and run the same architecture and deterministic test gates as CI.
- No database, shared global Artifact Store, Drive bridge, model call, or
  automatic probe promotion is introduced.

## Tests

- Runtime config parsing and absolute/distinct/external-root rejection.
- Safe project/cache/temp path resolution.
- External raw-request bootstrap, verify, reopen, and exact `HEAD` identity.
- Repository checkout remains free of generated project data.
- Cloud shell syntax and JSON configuration parsing.
- Architecture firewall and full unittest discovery.

## Stop conditions

- Stop if runtime setup needs a second project writer or duplicate canonical
  state authority.
- Stop if an absolute machine path would be persisted as project identity.
- Stop if existing probes must be bulk-moved or rewritten.
- Stop if a cache, temporary file, or cloud `/tmp` directory would be presented
  as durable project evidence.
- Stop before adding a database or cross-project shared Artifact Store; those
  require separate ownership and migration contracts.


## Completion

- Completed: 2026-08-04
- Evidence: Added explicit RuntimeConfig@1 and external project bootstrap through unchanged P036; verified credential-free Codex/Codespaces/CI setup, six focused runtime tests, architecture firewall, full suite, exact HEAD reopen, and unchanged promoted probes.
