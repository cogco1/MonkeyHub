# M030 — Architecture firewall single-pass indexing

- Origin: Modify
- Status: Ready
- Depends on: P045

## Goal

Restore deterministic full-suite verification by making the mandatory
architecture firewall reuse one AST index per source file instead of walking
the same tree repeatedly. Preserve the existing three-second wall-clock budget,
policy, findings, and CLI behavior.

## Acceptance

- Every Python source file is parsed once and its nodes and parent links are
  indexed once per `run_checks` call.
- Import, instance-answer, filesystem-write, authority-symbol, and commit-layer
  checks consume that shared index without dropping or weakening a finding.
- The existing `perf_counter` wall-clock assertion remains below 3.0 seconds;
  it is not replaced with CPU time and the threshold is not increased.
- Probe scanning, policy validation, JSON output, and exit semantics remain
  unchanged.

## Write scope

- `tools/archcheck.py`
- `tests/test_archcheck.py`
- `docs/mapping/`

## Tests

- Existing positive and adversarial architecture-firewall tests.
- Shared index covers every AST node and records exact parent links.
- Standalone architecture check and full unittest discovery.

## Stop conditions

- Stop before weakening a policy, excluding a checked source, or raising the
  three-second threshold.
- Stop if a pre-existing finding disappears under the indexed path.
- Stop after two failures of the same verification path.


## Completion

- Completed: 2026-08-03
- Evidence: Reused one AST node and parent index per source file without changing policies findings files or the 3.0-second perf_counter gate; focused tests passed, archcheck covered 100 files in 0.522 seconds unprofiled and 1.205 seconds under cProfile, function calls fell from about 18.9 million to 4.8 million, and formal three-command verification including full discovery passed.
