# ArchFlow V4

ArchFlow V4 is a framework for **bounded architectural agency**: agents work
on explicit architectural state, produce candidate models, and submit evidence
for validation and controlled promotion. Project history and canonical state
are persisted through one project repository, P036.

The current implementation is **StateRecord-driven**. Its working chain is:

```text
StateRecord → architectural elements → geometry program → Rhino export
            → independent model readback → relation checks → retained run evidence
```

**ArchFlow Studio (MonkeyArch)** provides the browser interface: a FastAPI API
and React/three.js client for inspecting 3DM models, selecting semantic objects,
editing through typed operators, running candidates, and reviewing results.
The client and server speak `archflow/2`.

Studio uses the exact StateRecord retained by the selected reference run.
Historical or incompletely bound references remain inspectable but cannot
base new candidate work. Candidate review readiness does not issue a run or
advance a stage; that remains the responsibility of `project.issue`.

## Start here

- [System map](docs/SYSTEM_MAP.md) — current capability owners and public APIs.
- [Canonical spine](docs/CANONICAL_SPINE.md) — the record-driven architecture
  and consolidation decisions.
- [Studio guide](apps/archflow-studio/README.md) — setup, model interaction,
  candidate execution, and validation.
- [API protocol](docs/PROTOCOL.md) — client/server contracts and versioning.
- [Dynamic map](docs/DYNAMIC_MAP.md) — active work and remaining plans.
- [Project document boundary](archflow/project/README.md) — project identity,
  storage layout, references, and persistence.
- [Building probes](probes/README.md) — retained building cases and evidence.

## Run Studio

Use Python 3.12 and a Node.js version compatible with the web package's Vite
and TypeScript dependencies. From the repository root, install the backend
and optional 3DM reader:

```powershell
py -3.12 -m pip install -e ".[cad-inspection]"
py -3.12 -m pip install -r apps/archflow-studio/api/requirements.txt
```

In one terminal, start the API against an **existing P036 project directory**.
Replace the placeholder with its absolute path; the server has no default project:

```powershell
cd apps/archflow-studio/api
$env:ARCHFLOW_STUDIO_PROJECT_DIR = "<absolute path to a P036 project>"
py -3.12 -m archflow_studio_api.main
```

In another terminal, starting from the repository root:

```powershell
cd apps/archflow-studio/web
npm install
npm run dev
```

Open [Studio](http://127.0.0.1:5174). The web development server proxies API
requests to port 8000. See the [Studio guide](apps/archflow-studio/README.md)
for reference-run selection, model providers, and optional Rhino execution.
Opening retained models does not require launching Rhino; producing Rhino
exports requires the separately configured executor.

## Development checks

From the repository root, with the backend dependencies installed:

```powershell
py -3.12 -m pip install pytest httpx2
py -3.12 tools/archcheck.py
py -3.12 -m pytest apps/archflow-studio/api/tests -q
```

For the web client, from `apps/archflow-studio/web`:

```powershell
npm --prefix tools/openapi-ts install
npm test
npm run typecheck
npm run api:check
```

## Project data and current scope

`archflow/` contains reusable mechanisms; `apps/archflow-studio/` contains the
product interface. Active building projects live in an explicitly configured
external project root. Promoted regression evidence belongs in `probes/`.
Both use the same P036 layout and persistence owner.

Studio viewport captures are PNG inspection images saved under the selected
run's `workspaces/studio-captures/`. They are not certified model exports and
do not change canonical HEAD. Runtime files, private project models, and
machine-specific configuration do not belong in source commits.

The current Studio workflow ends at candidate validation and human review.
The richer canonical-state projection needed for complete obligation and
commitment validation remains tracked by
[P110](docs/mapping/planning/P110-canonical-state-projection.md).
Planned capabilities and retained experiments are listed separately in the
[dynamic map](docs/DYNAMIC_MAP.md); they are not a claim of a complete,
autonomous brief-to-accepted-building workflow.
