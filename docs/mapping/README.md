# RMPA mapping ledgers

This directory splits the long V3 construction map into four small ledgers. It
is project-development documentation, not an executable architectural-design
pipeline.

- [Retirement](retirement/INDEX.md): replacement-first withdrawal of old
  responsibilities.
- [Modify](modify/INDEX.md): bounded repairs to the active path.
- [Planning](planning/INDEX.md): work not yet implemented or accepted.
- [Archive](archive/INDEX.md): completed work with evidence; this is the
  cleared construction log, not a dump of obsolete files.

R, M, and P are parallel work classifications. Archive is a completion state,
not a mandatory fourth stage. A completed card keeps its original `R`, `M`, or
`P` identity after it moves to Archive.

Machine status lives in
[`governance/work_registry.json`](../../governance/work_registry.json). Use
`python tools/devctl.py ...` from the repository root for controlled state
transitions and regenerate the short [Dynamic Map](../DYNAMIC_MAP.md).
