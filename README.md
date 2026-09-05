# ArchFlow V4

**Less work serving the tools. More room to develop architectural ideas.**

ArchFlow explores infrastructure for people and AI to work on architectural
design together. It starts with repeated explanation, manual transfer and repair
that tools leave to architects, and asks how removing that work can make ideas
easier to develop, inspect and revise. This is the direction, not a claim that
the current application has already achieved it.

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

## What counts as progress

Start with a real task, remove a concrete obstacle, and let the architect use the
result before expanding the system. Ask which tool-imposed steps disappeared and
which design judgments became easier to make. Include the setup, confirmation
and repair work the new tool adds. Evaluate a whole architectural task: whether its
parts serve their functions, their relationships are appropriate, and the result
survives further edits and handover. Counts of editable objects, dependencies,
tests or receipts do not answer these questions.

A drawing or model may look finished while its architectural questions remain
open. Discoveries made through sections, use and construction should be able to
change the original idea. Direct modeling is also a way of thinking, not simply
labor to automate. Reconstructing a known building tests different capabilities
from developing an unresolved design.

## MonkeyArch: a place to test the interaction

**MonkeyArch (ArchFlow Studio)** is the modeling environment used to test these
methods. People should be able to work directly on a model, inspect what the
machine understood, compare proposed changes, and take over when necessary.
Continuing a candidate, endorsing a direction and formally issuing a project
version are distinct actions. **Continue from this version** explicitly makes the
displayed run the next edit's starting point. Browsing a model alone does not change it.

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
project state. Feedback recovery and use in subsequent intents remain incomplete;
the general validation input lacks substantive design requirements, while
declared relations are checked separately. Explicit candidate continuation is
ready for local trial; program-sheet and massing-option generation still use the
default editing base. This establishes continuation, not complete architectural
reasoning: ordinary intent edits still target one numeric value, and following
declared dependencies does not establish that the necessary relationships exist.
See the [continuation handoff](docs/mapping/planning/P111-continuing-design-cycle.md),
[architecture and development order](docs/ARCHITECTURE.md), and [live work](docs/DYNAMIC_MAP.md).

## Start here

- [Vision](docs/VISION.md) — long-term research, technology and product direction.
- [Development map](docs/DYNAMIC_MAP.md) — live work and acceptance criteria.
- [System map](docs/SYSTEM_MAP.md) — current capability owners and public APIs.
- [Architecture](docs/ARCHITECTURE.md) — implemented responsibilities, concrete
  gaps, and the next architectural revision to develop.
- [Consolidation history](docs/CANONICAL_SPINE.md) — why the current spine was
  retained; not a migration plan to execute again.
- [Work environment](docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md) — source,
  runtime and project-data locations; extending an existing capability.
- [Studio guide](apps/archflow-studio/README.md) — setup, model interaction,
  candidate execution, and validation.
- [API protocol](docs/PROTOCOL.md) — client/server contracts and versioning.
- [Project document boundary](archflow/project/README.md) — project identity,
  storage layout, references, and persistence.
- [Repository layout](docs/REPO_LAYOUT.md) — what lives in the repository, the
  external workspace (building projects and their evidence) and the external archive.

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
