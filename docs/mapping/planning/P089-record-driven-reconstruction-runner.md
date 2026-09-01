# P089 — Record-driven reconstruction runner

- Origin: Planning
- Status: Ready after P069, P083, P087, P088
- Depends on: P069, P083, P087, P088

## Goal

Extract the reconstruction execution protocol — stage loop, record
writes, gate invocation, CAD execution, readback — that today exists as
three parallel per-building monoliths (`run_pantheon_reconstruction.py`
5,255 lines, `run_parthenon_reconstruction.py` plus its stage-4 variant
8,829 lines, and villa workspace authoring scripts; zero shared function
signatures between the pantheon and parthenon pair) into one
record-driven runner whose per-building input is a project record pack.
Record-ref loading verifies digests at the repository port, retiring
hand-pinned SHA constants (116 in villa authoring scripts alone). The
runner owns orchestration only: no derivation, gate, or authority
semantics change, and no building answer enters the framework.

## Acceptance

- One runner replays the pantheon, parthenon, and villa stage flows from
  their retained records, producing digest-equal stage packs wherever
  predecessors are unchanged.
- Per-building runner tools retire to archive stubs; building-specific
  behavior lives only in project records.
- Hand-pinned SHA constants and load-module-by-SHA patterns reach zero
  on the authoring path; loading is by verified record ref.
- Per-stage wall time is reported in the run summary.
- Full unittest suite and the architecture firewall pass.

## Write scope

- `tools/`
- `archflow/runtime/`
- `archflow/project/`
- `tests/`
- `probes/`
- `docs/mapping/`
- `governance/work_registry.json`

## Tests

- Runner replay equivalence against retained pantheon, parthenon, and
  villa records.
- Record-ref digest verification fail-closed paths.
- Architecture firewall.

## Stop conditions

- Stop before changing any gate or acceptance semantics.
- Stop if any building-type answer must be hardcoded for the runner to
  proceed.
- Stop before touching `run_pantheon_reconstruction.py` while P069 holds
  it in an active write scope — sequencing is enforced by the dependency.
