# M088 — Repository slimming and typology audit

- Origin: Modify
- Status: Ready after P088
- Depends on: P057, P088

## Goal

Bounded hygiene repair of the active path, no behavior change: migrate
retained probe run data (p026-sandbox-gold 8.2MB, p058-progressive-
pantheon 6.3MB, test_pantheon 2.7MB) to the runtime workspace with repo
anchors following the p066 precedent; slim `tools/state_tree_viewer.py`
(3,772 lines) by importing the P088 single-source schema contracts in
place of hand-copied key sets; audit
`archflow/validation/vertical_circulation.py` (3,453 lines — larger than
most packages) for embedded typology constants and extract any found
into project records.

## Acceptance

- The repository sheds the migrated run data; anchors resolve; no test
  or tool reads the old paths.
- The viewer holds no hand-copied schema key sets and drops below half
  its current line count without losing a rendering behavior.
- A vertical-circulation audit receipt either reports zero instance
  defaults or lists each extraction with its destination record.
- Full unittest suite and the architecture firewall pass.

## Write scope

- `probes/`
- `tools/state_tree_viewer.py`
- `archflow/validation/vertical_circulation.py`
- `tests/`
- `docs/mapping/`
- `governance/work_registry.json`

## Tests

- Anchor resolution for migrated probe data.
- Viewer regression against retained records.
- Vertical-circulation behavior equivalence before and after any
  extraction.
- Architecture firewall.

## Stop conditions

- Stop if migration would orphan any record referenced by a test or
  anchor.
- Stop if the typology audit finds a constant whose extraction would
  change validator behavior — record it and hand the decision to a
  human rather than deciding in the card.
