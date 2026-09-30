# Drawing discovery during run publication — bounded reliability fix

This independent follow-up starts from PR #477 at `a4fe031964150936dd4473a6aec2930fee3dbfdc`. It addresses the concurrent drawing-list failure recorded there, related to #319 and #327. It does not complete either Issue, Board association, conversion admission/recovery, or D5 rendering. #321 and #324 receive no new functionality.

## Cause and change

P036 `create_run` holds its existing `working_draft_guard` while installing the immutable manifest. The installer creates the run directory, writes a temporary manifest, then publishes `run.json`. `ProjectBinding.run_ids` previously enumerated directories without that guard. A concurrent document/drawing read discovered the new directory before its manifest existed and answered `404 RUN_NOT_FOUND`.

`ProjectBinding.load_run` now takes that same existing guard only when the requested run directory exists and its manifest is absent. It waits for an in-flight publication and performs the normal manifest read. Complete runs retain their nonblocking reads. No new lock, writer, persistence format, repeated retry, exception suppression or filtering of missing manifests is introduced. Geometry source, geometry revision, drawing recipe/revision and scene revision contracts are unchanged. The guard covers only the incomplete manifest read, not drawing records or projection; it does not serialize the full four-view generation as one transaction.

Owner: `studio.binding`, extending its existing read-only run resolution. `studio.artifacts` continues to discover registered documents; P036 continues to create and publish runs. A truly damaged retained run remains discoverable and raises its original explicit error.

## Reproduction and local results

The regression pauses the real immutable-manifest `os.link` operation after the new mesh-view directory exists and before `run.json` is published. A second Runtime application/binding concurrently requests `GET /api/render/drawings`. The read must wait for publication, then succeed; completed generation and a cold application must return the four current view recipes bound to the selected geometry. Canonical HEAD must stay unchanged.

Before the production fix, the architectural-wall case deterministically returned:

```text
404 != 200
RUN_NOT_FOUND: demo-project: run 'mesh-view-3daef9561ec328f2b59617d5967254a6dfd70030'
does not exist in the bound project: cannot read project record: run.json
1 failed, 1 passed, 5 deselected
```

The earlier real-browser failure is preserved at [the immutable PR #477 evidence](https://github.com/cogco1/MonkeyHub/blob/a4fe031964150936dd4473a6aec2930fee3dbfdc/probes/camera-link/failure-concurrent-drawing-read.log).

The initial guarded-enumeration attempt passed the targeted `test_render_scene.py` plus `test_binding.py` suite (**23 passed, 1 skipped, 2 subtests passed**) on Windows/Python 3.12.14. Both controlled concurrency subtests ran:

- A generated 4 m × 0.2 m × 3 m architectural wall mesh through actual GLB → 3DM conversion. This is an architectural component, not whole-building acceptance.
- The original Penguin GLB, SHA-256 `b8fac80bbbca66bd0ed0ab6f3544e46eab29ad840ceb21f71e9ab89d4e7a6c54`, read without modifying it.

Broader consumer regression then found a real incompatibility: **1 failed, 102 passed**, with `WorkingSourceTests.test_resolving_never_waits_for_a_project_writer` timing out at its existing five-second bound. Locking all enumeration was rejected. The final implementation narrows waiting to the incomplete-manifest case above and keeps that original nonblocking assertion unchanged. Final combined results are recorded below and in the PR.

Final local combined run: **126 passed, 1 skipped, 46 subtests passed** in 105.54 s, covering `test_render_scene.py`, `test_binding.py`, `test_artifacts.py`, `test_documents.py`, `test_model_source_index.py`, `test_working_source.py`, and `test_boards.py`. The actual Penguin and wall concurrency cases both passed in this run. Final architecture check: **PASS (580 files)**. No original test was removed, skipped or given a longer timeout.

The skipped test requires `ARCHFLOW_BLENDER_EXECUTABLE` for real Cycles host acceptance. It is not a PASS. The missing-manifest negative regression separately confirms a damaged retained run is not silently omitted. Full current-HEAD CI results and links belong in the PR; earlier-head results do not count.

To reproduce using the repository's installed API/geometry dependencies:

```sh
PYTHONPATH=.:apps/archflow-studio/api PENGUIN_TEST_GLB=/path/to/original.glb python -m pytest apps/archflow-studio/api/tests/test_render_scene.py apps/archflow-studio/api/tests/test_binding.py -q -rs
```

On Windows use `;` between PYTHONPATH entries. Without `PENGUIN_TEST_GLB`, CI runs the architectural-wall subtest only and must not be described as Penguin acceptance. No private model or large generated asset is added to Git.

## Limits and next work

This tests two independent Runtime bindings in one process using P036's shared guard; a new cross-process concurrency campaign is not claimed. Existing filesystem publication failures remain errors. The fix does not recover already-damaged runs, make all four views atomic, change view freshness, or implement Board model/revision associations. No new browser/UI acceptance is claimed for this backend-only change. Board engineering-drawing association and the distinction from Cycles images remain the next #319 functional slice; generic conversion admission/recovery remains #327. The earlier draft stack and its unresolved main integration/CLA remain unchanged.
