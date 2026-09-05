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

P052 adds only the external-root selection seam. `RuntimePaths` loads three
explicit absolute roots for workspace, rebuildable cache, and temporary data.
An active project resolves to `workspace/projects/<project_id>/` and is then
created by the unchanged P036 repository. The runtime layer does not store an
absolute path in `project.json`, add another writer, or change `HEAD` authority.
Committed probes use the same envelope and remain explicitly promoted evidence.

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
| alternative design branches | `runs/<run_id>/branches/` |
| candidate packages and plans | `runs/<run_id>/candidates/` |
| hard/commitment/aesthetic reviews | `runs/<run_id>/reviews/` |
| speculative tool files | `runs/<run_id>/workspaces/` |
| external-world reconciliation | `runs/<run_id>/recovery/` |
| non-authoritative share packages | `exports/` |

If an output does not fit exactly one row, stop before writing and ask the
project owner to assign it.

Cache and temp data do not fit this table because they are not project records.
They live outside the project root and may be deleted or rebuilt without
changing canonical project state.
