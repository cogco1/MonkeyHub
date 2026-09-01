# M088 — Repository slimming and typology audit

- Origin: Modify
- Status: Ready after P088
- Depends on: P057, P088

## Goal

Bounded hygiene repair of the active path, no behavior change: migrate
retained evidence probes (p026-sandbox-gold 8.2MB, p058-progressive-
pantheon 6.3MB) to the runtime workspace with `ProbeRelocationAnchor@1`
anchors on the p065/p066 precedent, repointing their tests through
`resolve_probe_root` with explicit external skips; keep `test_pantheon`
in the repo as a declared live fixture probe (unit tests read it
directly — reclassified during execution, 2026-09-01); single-source
the viewer's remaining hand-copied schema key sets at their definition
sites following the P088 pattern; audit
`archflow/validation/vertical_circulation.py` (3,453 lines — larger
than most packages) for embedded typology constants, extracting only
what does not change validator behavior and recording the rest for
human adjudication.

## Acceptance

- p026 and p058 live in the runtime workspace; their anchors resolve
  through `locate_project`; no repo test or tool reads the old paths
  directly, and affected tests skip explicitly when the workspace is
  absent (existing external-skip pattern).
- `test_pantheon` is documented as a fixture probe in the probes
  README.
- The viewer holds no hand-copied schema key sets whose schemas are
  defined in this repository; the line delta is reported.
- A vertical-circulation audit receipt either reports zero instance
  defaults or lists each finding with its disposition (extracted, or
  recorded for human adjudication under the stop condition).
- Full unittest suite and the architecture firewall pass.

## Write scope

- `probes/`
- `tools/`
- `archflow/capabilities/stage_evidence_pack.py`
- `archflow/evidence/stage_pack.py`
- `archflow/validation/vertical_circulation.py`
- `tests/`
- `docs/mapping/`
- `governance/work_registry.json`

(Scope amended 2026-09-01 during execution: key-set single-sourcing
requires exporting at definition sites — `stage_evidence_pack.py` and
the pantheon snapshot builder in `tools/`; `test_pantheon` reclassified
as a live fixture and removed from the migration list.)

## Tests

- Anchor resolution for migrated probe data.
- Viewer regression against retained records.
- Vertical-circulation behavior equivalence before and after any
  extraction.
- Architecture firewall.

## Vertical-circulation audit receipt (2026-09-01)

Exhaustive numeric sweep of
`archflow/validation/vertical_circulation.py` (3,453 lines): the only
literals are `0.0`/`1.0` guard values (3 occurrences each) and
`_EXACT_DATUM_TOLERANCE = 1.0e-9`, a numerical epsilon.
`min_headroom` and every riser/tread quantity are nullable
project-supplied criterion fields, matching the module docstring
("Project knowledge may supply adopted criterion ranges"). **Zero
instance defaults found; nothing to extract; no human adjudication
required.** The validator is contract machinery, not embedded
typology — its size is contract surface, not smell.

## Stop conditions

- Stop if migration would orphan any record referenced by a test or
  anchor.
- Stop if the typology audit finds a constant whose extraction would
  change validator behavior — record it and hand the decision to a
  human rather than deciding in the card.


## Completion

- Completed: 2026-09-01
- Evidence: p026 (185 files, 8.6MB) and p058 (54 files, 6.6MB) relocated to the runtime workspace with ProbeRelocationAnchor@1 anchors resolving via locate_project; 10 unit + 15 integration tests repointed through resolve_probe_root with explicit external skips, all green; test_pantheon documented as a live fixture probe in probes/README; StageEvidencePack@1 and PantheonStageProgressSnapshot@1 key sets single-sourced at their definition sites with a builder self-check (viewer 3772 -> 3739 lines net of P088 imports); vertical-circulation audit receipt: zero instance defaults, only 0.0/1.0 guards and a 1e-9 epsilon; full suite + firewall pass
