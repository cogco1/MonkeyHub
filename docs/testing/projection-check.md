# Projection check

`tools/projection_check.py` checks that a change to how the decision tree is
projected (ADR-008, [`docs/adr/ADR-008-one-tree-many-projections.md`](../adr/ADR-008-one-tree-many-projections.md))
leaves every answer the same. It needs nothing from the owner's machine. It runs as the `projection` job
of `.github/workflows/verify.yml` on `ubuntu-latest` and `windows-latest` for every pull request and
every push to `main` (issue #376).

## What it guarantees

It runs on one invented project and compares a **base** code root with a **candidate** code root.

In CI the **base** code builds that project (`build_synthetic_project` from the base checkout, with the base
first on `PYTHONPATH`; the step refuses to run if `archflow` or `archflow_studio_api` loads from anywhere else).
The promise is about existing projects: whatever the base wrote, the candidate must answer the same. A candidate
that deliberately changes what new runs record (#402 binds a record without massing by a new digest) writes data
the base cannot read, so building the project with the candidate would fail such a change for the wrong reason
(GH-429). A change to the generator itself is still covered by `test_synthetic_project.py` in `verify`.

1. The project is copied once into a scratch directory under its own folder name, and its file times are
   moved back so the project counts as settled. The project you pass is never written.
2. **Base, fresh process.** The base reads each route and records the status, a sha256 of the body and the time taken.
3. **Candidate, one process.** The candidate reads each route cold. It then retains one review,
   `POST /api/candidate-reviews` endorsing the first candidate in the design history with the reason
   `projection check`, and reads each route again.
4. **Base, fresh process.** The base reads each route again from the same copy, after the write.
5. **Parity.** The candidate's cold bodies must equal the bodies from step 2 byte for byte. Its bodies after the write must equal the bodies from step 4.

It checks these routes: `/api/design-history?branchId=main`, `/api/worktrees`, `/api/artifacts`, `/api/documents`,
`/api/working-source?workspace=modeling` and `/api/board`.

If any route in the candidate's cold reads carries an ETag, the candidate also has to keep the promises
of conditional reads (phase 0a). The check asks nothing of a base that predates them.

- Every route carries an ETag, and `If-None-Match` with that tag answers 304.
- After the review write, the design-history ETag changes and the design history shows the review.
- `/api/artifacts/{sha}/bytes` for the first available artifact answers with `Cache-Control` containing
  `immutable`, and the body hashes to that sha.

**Timings** are reported for every route. A route fails only when the candidate's cold read takes more than twice as long as the base's and is also more than 200 ms slower. Shared CI runners are too noisy for a tighter gate.

**Isolation.** Each side runs in its own interpreter with `<code-root>` and
`<code-root>/apps/archflow-studio/api` first on `sys.path`. A side stops if `archflow` or
`archflow_studio_api` loads from anywhere else. The tool itself imports nothing from either root.

The check does **not** show that a projection is architecturally right. It shows only that the candidate
answers exactly as the base does on this project. A route that both sides get wrong in the same way passes.

## The synthetic project

`apps/archflow-studio/api/tests/synthetic_project.py` builds the project with
`build_synthetic_project(parent, project_id="synthetic-bench", *, runs=30)`. It uses the `support` helpers
and the Studio API, and the model bytes come from `apps/archflow-studio/api/tests/fixtures/*.3dm`. All the
content is invented. At 30 runs the project holds:

- 20 candidates, most of them with an OCCT receipt and a retained `.3dm` preview;
- three accepted Stages on `main`, and a fork `alternative` taken from the second Stage, with its own Stage;
- admissions, candidate reviews and a Stage endorsement;
- six document runs with two sketch pages each, a board, and an adopted working draft;
- 494 JSON files, 457 of them retained records.

Ids, names and bytes are fixed, and so is what the product would take from the machine. The Studio stamps
reviews, admissions, Stages and the working draft with the wall clock and random event ids, and the runner
records how long each round took. While the scenario plays, those modules read a clock that starts at a fixed
instant, ids from a seeded generator and a timer that advances a fixed step per reading. Two generations are
therefore identical byte for byte, which `test_synthetic_project.py` asserts. The job still generates the
project once, and every step reads a copy of it.

## Run it

On any machine with the repository's Python 3.12 dependencies installed:

```
git worktree add ../base origin/main
cd apps/archflow-studio/api
python -c "import pathlib; from tests.synthetic_project import build_synthetic_project; print(build_synthetic_project(pathlib.Path('../../../../projects')))"
cd ../../..
python tools/projection_check.py --base ../base --candidate . --project ../projects/synthetic-bench \
    --out ../projection/result.json --summary ../projection/summary.md
```

The exit code is 0 when every check passes and 1 otherwise. `--work <dir>` keeps the scratch copy and every
response body in a directory you choose. The default is a new temporary directory. A cloud session runs the
same commands, or pushes and reads the job's summary.

The unit tests (`test_projection_check.py`) exercise the verdict on fabricated results. They also run the tool
once end to end, on this checkout against itself, with a small project:

```
python -m pytest apps/archflow-studio/api/tests/test_synthetic_project.py apps/archflow-studio/api/tests/test_projection_check.py -q
```

## Read the summary

The job appends a table to the run's summary page:

| Column | Meaning |
|---|---|
| Parity before write | The candidate's cold body equals the base's first read (✅) or differs (❌). |
| Parity after write | The candidate's body after its review write equals the base's read after that write. |
| Base cold ms / Candidate cold ms | The first read of the route in a fresh process on each side. ⚠ marks a route over the timing gate. |
| Candidate after-change ms | The same route read again in the candidate's process after the write. |

Below the table, a line counts the conditional-read checks that passed. Any failure is listed by route. For each
body that differs, a collapsed block shows the first differing byte with some context from both sides. The full
result, with each side's raw measurements, is written to `result.json` in the runner's temp directory.

## The owner's private judge

The owner still has a local judge that runs on copies of real projects. Those projects cannot be put in the
repository. The judge is now only a spot check before a large projection change is merged. Everyday acceptance
is this job.
