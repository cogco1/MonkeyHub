# Project document boundary

This package is the generic document-management seam between reusable ArchFlow
mechanisms and one building project. It contains no room list, dimensions,
topology, palette, coordinates, or building-type routing.

P035 components remain intentionally side-effect-free:

- `manifest.py` defines immutable project identity and format metadata;
- `refs.py` separates project versions, runs, branches, records, and artifacts;
- `layout.py` maps every known project-owned area without creating it;
- `ports.py` defines injected persistence interfaces and fails closed when no
  destination has been assigned.

P036 adds `FilesystemProjectRepository`, the only generic filesystem owner.
It installs immutable JSON and binary content by digest, records exact-base
runs, prepares accepted canonical transitions, atomically compare-and-swaps
`HEAD`, verifies the event/snapshot chain on reopen, and reports unreferenced
pre-commit records after a crash. Producers receive its sink interfaces and
an assigned `PersistenceDestination`; they never choose a path.

`location.py` resolves existing projects under roots supplied explicitly by the
caller. `FilesystemProjectRepository.initialize` creates a project at an explicit
root. These paths do not change `project.json` identity or `HEAD` authority.
Active projects live outside the source repository. Section 8 of the
[work environment and onboarding guide](../../docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md)
covers installation, project creation and Studio's explicit project binding.
Tests bootstrap their own disposable projects through the same repository under `tempfile`.

From the source root, using the installed Python environment, create an external
project through the production [CLI](../../tools/create_project.py):

```powershell
python tools/create_project.py --project D:/ArchFlowRuntime/workspace/projects/my-project
```

The final directory name is the project id. The target must be outside the source
repository and absent or empty; creation never overwrites an existing project.
This command creates version 0 and an empty authored `StateRecord@1`, with no run,
model or seat pack. Studio can connect and inspect that empty state. Program and
modeling candidates need suitable design inputs and execution seats; saving a PDF
also needs an existing run.

For a new project with caller-authored inputs, use this invocation **instead of**
the empty initialization:

```powershell
python tools/create_project.py --project D:/ArchFlowRuntime/workspace/projects/my-project `
    --state-record D:/design-inputs/state-record.json `
    --seats-file D:/design-inputs/seats.json
```

The state record must name the target project and have `base` null or omitted; the seat
pack uses the existing runner format. UTF-8 input with or without a BOM is accepted.
The CLI validates these inputs and passes them to P036's optional `authored_record`
and `seat_pack` arguments. The repository writes them only during initialization;
it does not infer missing architectural content or manufacture a completed run.
To continue an existing retained design, open its complete project copy instead.

The project files have distinct roles:

| File or area | Role at creation |
| --- | --- |
| `project.json` | immutable project identity and format version |
| `HEAD` | version 0's canonical position; unrelated to a Git branch |
| `design/branches.json` | created by explicit design acceptance or fork; project-wide design branch pointers, independent of canonical `HEAD` |
| `canonical/`, `events/` | the initial snapshot and initialization event |
| `input/runner/state-record.json` | caller-authored input, or the CLI's empty record |
| `input/runner/seats.json` | execution seats, written only when supplied |
| `objects/sha256/`, `runs/`, `exports/` | prepared storage areas; initially no run or model |

Other project areas appear as the operations that own them run. Older projects
need not have every empty directory; repository reopen checks their retained state.
There is no separate workspace manifest: a suggested layout is
`<workspace_root>/projects/<project_id>`, and callers supply the roots explicitly.
Studio's [configuration template](../../apps/archflow-studio/runtime.example.json)
selects one project; it does not manage the surrounding workspace, cache or temp roots.

Canonical crash order is:

1. write immutable decision receipt and candidate records;
2. write the replacement canonical snapshot;
3. append the accepted exact-base event;
4. atomically replace `HEAD`.

A crash before step 4 leaves safe, reported orphans. A stale or rejected
writer cannot replace `HEAD`. This is a project-document transaction only; it
does not claim an atomic transaction with Minecraft or another external tool.

Known ownership:

| Output | Destination |
| --- | --- |
| imported request/evidence | `input/` |
| immutable content-addressed data | `objects/sha256/` |
| accepted canonical events | `events/` |
| verified canonical snapshots | `canonical/` |
| derived/expert/model/tool records | `runs/<run_id>/records/` |
| execution branch bindings | `runs/<run_id>/branches/` |
| persistent design branch fork/head refs | `design/branches.json` |
| candidate packages and plans | `runs/<run_id>/candidates/` |
| hard/commitment/aesthetic reviews and immutable accepted design Stages | `runs/<run_id>/reviews/` |
| speculative tool files | `runs/<run_id>/workspaces/` |
| external-world reconciliation | `runs/<run_id>/recovery/` |
| non-authoritative share packages | `exports/` |

If an output does not fit exactly one row, stop before writing and ask the
project owner to assign it.

Cache and temp data do not fit this table because they are not project records.
They live outside the project root and may be deleted or rebuilt without
changing canonical project state.

Design acceptance stores a `DesignStage@1` in the accepted candidate's reviews,
pinning its complete model, StateRecord and runner receipt, then compares and
atomically advances the named design branch. P036 uses the existing process and
file lock primitives around `design/branches.json`. A failed pointer update leaves
an unreachable prepared Stage; `verify()` reports it and history does not show it
as accepted. Forking creates a branch pointer to a reachable historical Stage and
generates no model. Candidate operators remain under their run's records.
Neither operation changes canonical `HEAD`; formal issue retains its exact-base
check. `load_version_state()` reads a specified published ancestor from the
retained event/snapshot chain for historical design validation.

An architect's original Rhino file may stay in its existing working directory.
Copies, exports and records that must reopen or travel with an ArchFlow project
are retained through P036 in the assigned project area; an external source path
does not replace a project artifact reference.
