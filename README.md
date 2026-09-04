# ArchFlow V4

**Toward an architectural practice where intent, reasoning, and construction
remain connected.**

ArchFlow explores how an Architect Agent could carry a design through an
evolving building project: interpreting a brief, investigating a site,
developing spatial alternatives, coordinating technical knowledge, and
revising the work as constraints and ambitions change.

The long-term goal is a working environment in which architects and agents
can develop a building together, with every meaningful decision connected
to the model it changes, the evidence behind it, and the consequences that
follow. Human architects set the direction, negotiate competing values,
and decide what is ready to become a project commitment.

## A building that remembers its reasoning

Architectural work moves between conversations, sketches, models, drawings,
calculations, specifications, and decisions. The relationships between these
representations are often held in people's memory. A change to one can leave
the others behind.

ArchFlow aims to make those relationships part of the project itself. A bay
dimension should remain connected to the spaces it organizes, the structure
it spans, and the envelope it supports. Revising it should expose which
decisions need to be revisited and which commitments must be preserved.

This would let a project accumulate understanding across design iterations:
what was proposed, why it changed, what was checked, and what the team accepted.
Models and drawings would become coordinated expressions of that evolving
architectural understanding.

## An agent with room to design

The research premise is **bounded architectural agency**. A primary Architect
Agent should have room to formulate a design approach, discover capabilities,
consult specialist knowledge, use modeling tools, and test alternatives in
an isolated workspace.

Its freedom to explore is paired with explicit responsibility for the result.
Before a proposal becomes accepted project state, it must be checked against
the project's constraints, existing commitments, and required evidence.
Unresolved questions return to the architect for judgment; accepted decisions
become the basis for the next iteration.

The ambition extends from early spatial exploration toward technical
coordination and construction documentation. Across those stages, ArchFlow
seeks to preserve a continuous relationship between design intent and the
building that can actually be made.

## From conversation to a shared project

**ArchFlow Studio (MonkeyArch)** is the interface through which this research
is becoming a working tool. Its intended experience brings conversation,
model interaction, design alternatives, and review into one place. An
architect should be able to point to part of a building, discuss a change,
inspect its consequences, and choose how the project proceeds.

The framework beneath that interface is intended to support different model
providers and discoverable capabilities while keeping the project coherent
across tools, sessions, and collaborators.

## Where V4 stands

V4 currently implements a **StateRecord-driven** chain: architectural elements
become geometry programs, Rhino exports are independently read back, and
relation checks and run evidence are retained through the P036 project
repository.

Studio provides 3DM inspection, semantic object selection, typed edits,
candidate execution, and review. Its current workflow ends at candidate
validation and human review; review readiness does not advance canonical
project state. The broader brief-to-construction vision remains the research
direction. Current work and remaining capabilities are tracked in the
[dynamic map](docs/DYNAMIC_MAP.md).

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

<details>
<summary>Installation and local development</summary>

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

### Development checks

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

</details>

## Project data

`archflow/` contains reusable mechanisms; `apps/archflow-studio/` contains the
product interface. Active building projects live in an explicitly configured
external project root. Promoted regression evidence belongs in `probes/`.
Both use the same P036 layout and persistence owner.

Runtime files, private project models, and machine-specific configuration
stay outside source commits. Each project's retained state and evidence
remain under its explicitly assigned project root.
