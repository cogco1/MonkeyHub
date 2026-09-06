# ArchFlow V4

New teammate or agent? Start with the [团队与 Agent 接入清单](docs/TEAM_ONBOARDING.md):
which repository to use, the first runnable task, setup commands, and the current release boundary.

**Helping architects carry design work forward with AI they can understand,
take over, and change.**

ArchFlow investigates how people and AI can carry architectural design work
forward together. Taking a concrete design task or revision as its unit of study,
it explores how modeling, related changes, representation and checking can
connect with less repeated explanation, manual transfer and follow-up work.
The aim is to extend what architects and small teams can develop, while keeping
them able to understand, take over and change the machine's work.

## The work behind a model

An architectural idea has to become specific objects, dimensions, relationships
and operations. When a design changes, some relationships can carry the change;
others need to be reconsidered or rebuilt. Architects also discover what they
want by drawing, moving, cutting and comparing, so the original question can
change during the work.

Moving an entrance, for example, may involve its approach, levels, canopy,
internal circulation and related drawings. Moving the door is one operation;
working out and addressing the relevant consequences is a larger design task.

Existing modeling tools already provide powerful geometry, parametric
relationships and automation, and AI tools increasingly help author operations.
ArchFlow asks what work still falls between those steps, and how people and
machines can carry it through together.

## Research: connecting work across a design change

The research asks how an environment can connect the operations, dependencies
and decisions involved in a design task. Machines may help establish or revise
modeling methods, execute defined changes, and return unresolved questions to
the architect. Retained explanations alone cannot establish that the affected
parts have been addressed. An architect must also be able to try an idea before
every judgment has become a formal rule.

Persistent project state is one method for connecting models, conditions,
questions, alternatives and judgments across this work. Its value must be tested
through actual design changes, including setup, handoffs, checking and correction,
not only the speed of an individual operation.

## MonkeyArch: a place to test the interaction

**MonkeyArch (ArchFlow Studio)** is the modeling environment used to test these
methods. People should be able to work directly on a model, inspect what the
machine understood, compare proposed changes, and take over when necessary.
**Continue** supports further design work across candidates and sessions.
Continuing a candidate, endorsing a direction and formally issuing a project
version are distinct actions.

Rhino/Grasshopper, Revit and Blender are potential foundations and comparison
paths. An independent interface does not require an independent geometry engine.
If an extension can meet the same research and practical needs, it is a valid
outcome. A standalone environment must demonstrate what it adds.

## Why an open-source direction

We want architects and researchers to be able to inspect the methods encoded in
the tool, challenge their assumptions, and adapt them to different practices.
This includes allowing others to develop different rules and implementations.
Portable project data, inspectable and modifiable code, and replaceable tool
interfaces each contribute to that aim; none substitutes for the others.
Specific licensing and release scope are separate project decisions.

The [vision](docs/VISION.md) develops this direction in Chinese, with a concise
English statement and sources for the existing-tool comparison.

## Where V4 stands

V4 currently implements a **StateRecord-driven** chain: architectural elements
become geometry programs, Rhino exports are independently read back, and
relation checks and run evidence are retained through the P036 project
repository.

Studio provides 3DM inspection, semantic object selection, typed edits,
candidate execution, and review. Its current workflow ends at candidate
validation and human review; review readiness does not advance canonical
project state. The broader modeling and collaboration aims remain research and
development work. Current tasks and acceptance criteria are tracked in the
[dynamic map](docs/DYNAMIC_MAP.md).

## Start here

- [Vision](docs/VISION.md) — long-term research, technology and product direction.
- [Development map](docs/DYNAMIC_MAP.md) — live work and acceptance criteria.
- [System map](docs/SYSTEM_MAP.md) — current capability owners and public APIs.
- [Canonical spine](docs/CANONICAL_SPINE.md) — the record-driven architecture
  and consolidation decisions.
- [Studio guide](apps/archflow-studio/README.md) — setup, model interaction,
  candidate execution, and validation.
- [API protocol](docs/PROTOCOL.md) — client/server contracts and versioning.
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
