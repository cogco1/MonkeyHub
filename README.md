# ArchFlow V4

> 当机器越来越擅长生成答案，我们想让它重新学会陪人推敲。
>
> As machines become better at generating answers, we want them to learn
> to deliberate with people.

ArchFlow explores a computational representation of design as an ongoing
process of deliberation. It seeks to make the questions, commitments,
alternatives, evidence, judgments, and uncertainty of unfinished design
understandable, persistent, and available for humans and machines to work on.

## Research: representing unfinished design

What should a building that has not yet been fully thought through be inside
a computer?

Architectural design develops through questions, competing proposals, partial
judgments and revisions. ArchFlow studies how that process can become part of
an evolving design state alongside models and drawings. An unresolved question
can remain open; a rejected proposal can still inform the next attempt; an
accepted decision can become a condition for further work.

The research concerns how a building gradually becomes itself, from early
spatial exploration through technical coordination and construction.

## Technology: project understanding that persists

The long-term architecture places human and agent proposals around a persistent
design state, from which compilers and execution tools produce geometry.
Models contribute intelligence; project records preserve what was proposed,
why it changed, what remains uncertain and what people accepted.

The goal is to continue the same project across sessions and model providers.
With a model disconnected, the retained design and its reasoning should remain
available for inspection and further human work. Human architects set direction,
weigh competing values and decide what becomes a project commitment.

## Product: Continue

**MonkeyArch (ArchFlow Studio)** is the professional modeling environment used
to test this research in architectural practice. Its long-term core loop is:

```text
Notice → Question → Propose → Externalize → Judge → Revise → Commit
```

These activities can be revisited. When an architect says an entrance feels
heavy, the environment should help retain the question, explore interpretations,
make alternatives visible, compare them and carry the architect's judgment into
another round. Reopening the project should make that work easy to continue.

The [vision](docs/VISION.md) defines the research, technical and product direction
in Chinese, with a reusable English statement. The conceptual design state is a
research framework; it does not prescribe a new schema or one module per concern.

## Where V4 stands

V4 currently implements a **StateRecord-driven** chain: architectural elements
become geometry programs, Rhino exports are independently read back, and
relation checks and run evidence are retained through the P036 project
repository.

Studio provides 3DM inspection, semantic object selection, typed edits,
candidate execution, and review. Its current workflow ends at candidate
validation and human review; review readiness does not advance canonical
project state. The full deliberation and cross-session continuation loop remains
development work. [P111](docs/mapping/planning/P111-continuing-design-cycle.md)
defines the next cycle, with P108 and P110 as acceptance prerequisites. Current
work and remaining capabilities are tracked in the [dynamic map](docs/DYNAMIC_MAP.md).

## Start here

- [Vision](docs/VISION.md) — long-term research, technology and product direction.
- [Continue development cycle](docs/mapping/planning/P111-continuing-design-cycle.md) — staged work and acceptance criteria.
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
