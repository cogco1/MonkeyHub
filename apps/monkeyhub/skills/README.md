# MonkeyHub skills

Each folder here is a skill package that ships with MonkeyHub, at this same path in
the release. A project takes
one in with `POST /api/skills/import` (`source: "bundled"`, `name`: the folder),
which registers it as a version of that project's skill. The user's own
packages live in `%LOCALAPPDATA%\MonkeyHub\skill-packages\` (or the folder
`MONKEYHUB_SKILL_PACKAGES` names) and are imported the same way with
`source: "user"`. `GET /api/skills/library` lists both, with the version each
project holds and whether it matches the package.

A package folder holds:

- `skill.json`: `name` (lowercase words joined by hyphens), `description` (one
  line), `entry` (the `.py` file that runs), `manifest` (`inputs`, `outputs`,
  `permissions`, in words for a reader) and `access` (what the run may do beyond
  its own folders: `programs`, `network`, `ssh`, `read`, `write`, `packages`;
  `null` when it needs nothing).
- `SKILL.md`: the instructions; a frontmatter block is dropped.
- every other file, UTF-8 text only (`__pycache__` and dot-files are skipped).

Importing grants nothing. A person allows each version before it runs, on the
Hub page or in the chat when an Agent asks; see "Live Rhino and skill runs" in
`docs/protocols/project-runtime-api.md`.

This repository is public. A package here carries no project's own data
(paths, presets, names of a project's buildings or sites); those stay in the
user's own package folder. None ships yet: which packages are made generic and
published here is decided after the 10-25 freeze.
