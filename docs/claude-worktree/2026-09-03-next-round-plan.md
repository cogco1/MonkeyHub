# Next round — after the container plan landed

2026-09-03, evening. Everything in the container plan (waves A–C), the Studio fixes, the protocol seam,
the MonkeyArch launcher/icon and codex's Chinese pack are on main. This is the plan for the next
round, in the order the dependencies allow. Fable plans and reviews; Opus workers implement pinned
briefs in worktrees (rebase first and last, PYTHONPATH for the api suite); codex owns the Studio's
i18n and settings surfaces.

## Kaiwen's own decisions and acts (nothing below moves without them)

1. **Issue the villa** (the designer's act, not the tool's):
   `py -3.12 tools/issue_project.py --project <villa> --run stage-1-001 --decided-by "Kaiwen Liu"`.
   HEAD becomes issue 1; the Studio shows `published · issue 1`.
2. **PromotionDecision@2 or not.** Today `decided_by` / `note` are printed and retained nowhere
   (the @1 key set is closed and digest-bound). If the record should say who issued, W9 below adds @2
   and the repository accepts both.
3. **Clearance numbers.** The villa's four `corridor-*-hall-clearance` relations can now be measured
   (a space's bounds come from its `Volume@1`), but only against an interval you state from the brief
   (e.g. the passage between corridor and hall must lie in [0, 0.05] m for "adjacent", or a minimum
   clear width). Give the rule; Fable declares the validators.
4. **rocca-pisana**: keep (then name the subject/object of `loggia-to-hall-host-void`) or archive.
5. **Archive suite**: 39 retired-lane tests fail on unregistered record kinds. Accept marking them
   skipped with the rule as the reason, or leave them red as a standing statement.
6. **To codex**: a visible "机译" marker on the translated layer of server prose (BilingualText.tsx),
   so honesty lines are not mistaken for the record.

## Wave D1 — the ladder becomes real (kernel, small, first)

**W7b — the phase is the run's.** `developed_design_view` hard-codes
`active_phase = DESIGN_DEVELOPMENT`; `StageExecutionGuard` requires the envelope's phase to equal it,
so no stage outside that phase can run (villa `workflow-002` / `stage-0-001` died of this). The view
takes the phase from the caller (the runner passes the envelope's), `state_digest` therefore binds
the phase per run, and the ladder's other phases open. Then villa **workflow v4** with real phases
(schematic_design 200 → design_development 300 → candidate_coordination 350), frozen as
`workflow-004`, stages 0–1 re-run and closed, stage 2 closable once decision 3 above is in.
Files: `archflow/state/state_record.py`, `archflow/runtime/project_runner.py`,
`apps/…/adapters/harness.py`, `tools/verify_state_record.py`, tests. Studio API unaffected on the wire.

**W9 — PromotionDecision@2** (only if decision 2 says yes): `decided_by`, `note`, `issued_at`; the
repository's promotion gate accepts @1 and @2; `issue_run` writes @2; the Studio's published card
shows who issued.

## Wave D2 — the research comes back in (the paper's evidence chain)

Per `2026-09-03-research-evidence-bridge.md`, in this order, one worker each:

- **W10 evidence ledger** (`archflow/state/evidence.py`): `research-evidence-ledger` record
  (EvidenceLedger@1: id, kind, url or object ref, status ∈ {confirmed, reported, calculated,
  inferred}, aspects ∈ {existence, morphology, relative_position, topology}, supports → component /
  role via the semantic registry, since); `StateRecord` validates every `evidence_refs` entry against
  the ledger named in `basis_refs`, record- and entity-level; unsupported elements are reported, not
  refused. Villa import from the five research runs' retained records (controlled-source-ledger,
  visual-evidence-inventory, material evidence packs) into a `research-006` run.
- **W11 atlas export** (`tools/export_research_atlas.py`): the ledger out as the codex skill's
  `research.source.json` + `cases.source.json` with the referenced objects, into
  `exports/research-atlas/<date>/`, plus a `research-atlas-export` record. The board itself stays the
  skill's job.
- **W12 Studio Research tab** (after D1 and codex's catalog is stable; strings go through the
  catalog): ledger entries by element, ROI crops served by a read-only artifact route, picking an
  element shows its evidence, an export links the A3 sheets. Stage-0 closure can then require
  `evidence-coverage` (a check the spine measures: every structural-support / weather-enclosure role
  cites at least one confirmed or reported entry).

## Wave D3 — settings that the launcher can read

Codex correctly refused a browser writing `%APPDATA%`. The capability goes in the app-local API,
local mode only: `GET/PUT /api/settings/user` (language, theme, fontScale, model defaults) backed by
`%APPDATA%\MonkeyArch\settings.json`, declared in `/api/protocol` capabilities as `user-settings`,
refused in remote mode; the launcher reads that file at start and turns it into env; codex wires the
panel to it. One owner for the file: the API's settings module; the launcher only reads.

## Wave D4 — debt that is now visible

- Eight registry modules have only archive-side tests (`capabilities.declaration`,
  `capabilities.opening_solver`, `contracts.canonical`, `project.manifest`, `relations.contracts`,
  `state.commitments`, `state.program`, `validation.model`): a spine test each, or an
  `untested_reason` that tells the truth.
- Archive suite per decision 5.
- Studio: the versions strip grows taller than the stage for a run with 26 exports (fold per run;
  the UI worker flagged it); `show run` on such a run parses 24 files at once (cap or warn).
- Two names for the server (`archflow-studio-api` on /api/health, `monkeyarch-api` on
  /api/protocol) — decide one when the health probe consumers are known.

## The paper thread (runs beside the waves, Kaiwen + Fable)

The villa now has a citable chain: authored record (content digest e88eba2a…) → `workflow-003` →
`stage-0-002` closure → `stage-1-001` closure (support_contact measured) → issue 1. Equivalence
runs `runner-002` vs `equivalence-004` are the replay experiment with a truthful provider identity.
Next: write the case section from those records; design experiment A (the three-mechanism
comparison from the CAADRIA status memo) on top of `open_stage_run` + `issue`; when D1 lands,
pantheon-zero as the second monument under the same ladder.

## Order

D1 first (one worker, a day); D2 W10 in parallel with D1 (different files); W11 after W10; D3 after
codex's 机译 marker and the settings note; W12 after D1 + D3; D4 fills gaps between waves. Each wave:
archcheck PASS, spine and api suites green on main, push on Kaiwen's word.
