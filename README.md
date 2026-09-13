# MonkeyHub

> **Public Research Preview**
>
> MonkeyHub is under active development. APIs, project formats, installation paths and behaviors may change without backward-compatibility guarantees. The current software is a research/development system, **not a construction-, engineering-, permit-, code-compliance-, or safety-certified product**. Independently verify geometry, quantities, analysis results, fabrication output and any decision that can affect people, property or built work. Do not place secrets, credentials, confidential project data or personal information in public issues, discussions, logs or example files.

New teammate or agent? Start with the [团队与 Agent 接入清单](docs/TEAM_ONBOARDING.md):
which repository to use, the first runnable task, setup commands, and the current release boundary.

**Less work serving the tools. More room to develop architectural ideas.**

**MonkeyHub** is the shared project environment. **ArchFlow** is the underlying state/runtime mechanism used to keep project identity, provenance, dependencies, revisions and tool projections coherent while people and AI work on the same evolving design.

The project starts with repeated explanation, manual transfer and repair that tools leave to architects, and asks how removing that work can make ideas easier to develop, inspect and revise. This is the direction, not a claim that the current application has already achieved the full vision.

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

**MonkeyArch**, hosted in the shared Studio/Hub environment, is the modeling workspace used to test
these methods. People should be able to work directly on a model, inspect what the
machine understood, compare proposed changes, and take over when necessary.
Continuing a candidate, endorsing a direction and formally issuing a project
version are distinct actions. **Continue from this version** explicitly makes the
displayed run the next edit's starting point. Browsing a model alone does not change it.

Rhino/Grasshopper, SketchUp, Revit, Blender and other professional tools are potential foundations, execution environments and projections. An independent interface does not require an independent geometry engine. If an extension can meet the same research and practical needs, it is a valid outcome. A standalone environment must demonstrate what it adds.

## Why an open-source direction

We want architects and researchers to be able to inspect the methods encoded in
the tool, challenge their assumptions, and adapt them to different practices.
This includes allowing others to develop different rules and implementations.
Portable project data, inspectable and modifiable code, and replaceable tool
interfaces each contribute to that aim; none substitutes for the others.

The project is now released under **GNU AGPL v3.0 only (`AGPL-3.0-only`)**. See [License and contributions](#license-and-contributions) below for the practical boundary.

The [vision](docs/VISION.md) develops this direction in Chinese, with a concise
English statement and sources for the existing-tool comparison.

## Where the current system stands

The current implementation has three established code owners composed in the same application:

- **ArchFlow** holds shared project storage, architectural facts and technical contracts.
- **MonkeyArch** owns 3D modeling, typed design edits, candidate execution and relation checks.
- **MonkeyDiagram** owns drawing projection and presentation; its Studio workspace also handles document viewing and annotation.

Product names and design-history terms follow the
[team naming conventions](docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md#产品名称与版本用语),
including MonkeyHub, MonkeyBoard, MonkeyMonitor, MonkeyFab and Stage / Branch /
Candidate. MonkeyHub is the top-level product/system; ArchFlow remains the underlying mechanism.

Modeling produces geometry programs and CAD exports, with saved-file readback and
retained run evidence. OCCT and explicitly selected Rhino execution use the same
project storage. A displayed model or drawing keeps its selected source; continuing
a candidate and formally issuing a project version remain separate actions.

The current implementation does not establish complete architectural reasoning.
Declared relationships can be checked and propagated, but their completeness and
design adequacy still need architectural judgment and real project trials. See the
[architecture](docs/ARCHITECTURE.md), [repository layout](docs/REPO_LAYOUT.md) and
[live work](docs/DYNAMIC_MAP.md) for implemented behavior and remaining work.

## Start here

- **One application entry:** open MonkeyHub. For agent access and source work, follow the [Hub entry and bounded lookup sequence](docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md#0-monkeyhub-统一入口).
  It connects the source checkout, external project, capability registries and existing commands.
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

## Team development on GitHub

The team shares two source repositories:

| Repository | What the team changes there |
| --- | --- |
| [Shared toolbox](https://github.com/cogco1/huaguoshan-digital-infrastructure) | Research CLI, reusable Skills, experiment record tools and figure/report generators |
| [MonkeyHub](https://github.com/cogco1/MonkeyHub) | Shared project infrastructure, ArchFlow runtime, MonkeyArch modeling, MonkeyDiagram drawings and related applications |

Take one scoped task in the relevant repository, work on a short branch, open a
pull request, and have another member review the change and its Actions checks
before integration. Each member has an independent clone and runtime. Shared
code does not mean sharing one live project directory or another member's credentials.
See the [team setup and review path](docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md).

New contributors must also read and agree to the [`CLA.md`](CLA.md) before a contribution is merged. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the working process.

## Run Studio

For the Windows candidate installation without a system Python or Node.js,
see the [MonkeyHub installation guide](apps/monkeyhub/installer/README.md).
The source-development setup below remains available for contributors.

Follow section 8 of the [team setup guide](docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md):
an independent clone, Python 3.12 in an external virtual environment, Node.js 24,
and the locked web dependencies. Create an external project with the production command:

```powershell
python tools/create_project.py --project 'D:\ArchFlowRuntime\workspace\projects\my-project'
```

This creates an empty project container. For candidate editing, provide the project's
authored State Record and seat pack using `--state-record` and `--seats-file`; to continue
existing work, use its complete project directory and selected run. Initialization
alone creates no model or run.

Copy [runtime.example.json](apps/archflow-studio/runtime.example.json) to your own
configuration, fill in `project_dir` and your Python executable, and pass that path to
`launch-studio.ps1 -RuntimeConfig`. A local `apps/archflow-studio/runtime.json` is also
supported and ignored by Git. The template uses the deterministic provider and disables
CAD export, so opening the project requires neither an API key nor Rhino.
See the [Studio guide](apps/archflow-studio/README.md) for interaction and export options.

## Project data

`archflow/` contains shared contracts and project mechanisms; `monkeyarch/` and
`monkeydiagram/` hold the workflow algorithms. `apps/archflow-studio/` composes the interface. Active building projects live in an explicitly configured
external project root. Promoted regression evidence belongs in `probes/`.
Both use the same project layout and persistence owner.

Runtime files, private project models, and machine-specific configuration
stay outside source commits. Each project's retained state and evidence
remain under its explicitly assigned project root.

## License and contributions

Unless a file or third-party notice says otherwise, first-party MonkeyHub source code is licensed under **GNU Affero General Public License v3.0 only (`AGPL-3.0-only`)**. See [`LICENSE`](LICENSE) and [`LICENSING.md`](LICENSING.md).

AGPL permits commercial use; organizations do not have to pay merely because they are companies. They do have to comply with the license. The copyright holders may separately offer commercial terms for proprietary redistribution, OEM use, or modified hosted deployments that do not want to operate under AGPL obligations.

New contributions are accepted under the [`MonkeyHub Contributor License Agreement`](CLA.md). Contributors retain copyright while granting the project the rights required to keep the project open source and, where appropriate, offer separate commercial licenses.

Third-party dependencies, SDKs, models, fonts and assets remain under their own licenses. Public source availability is not a representation that every optional third-party integration can be redistributed under AGPL on identical terms.
