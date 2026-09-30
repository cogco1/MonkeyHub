# ADR-007 — Four container states; a stage belongs to the run

**Decision (2026-09-03):** the project speaks ISO 19650's four information-container states, and each
maps to exactly one place in the P036 layout:

| state | where | who writes it |
|---|---|---|
| Work in progress | `input/runner/state-record.json` (and `seats.json`), the designer's authored files | the designer; read by one module, never digested by the repository |
| Shared | `runs/<run_id>/` — the exact record used (`state-record`), the developed state, programs, checks, receipts | the runner, through `put_json` |
| Published | `HEAD` — the one compare-and-swap position | `prepare_transition` + `compare_and_swap`, from a `PromotionDecision@1` |
| Archived | superseded `canonical/state-v…` snapshots and old runs | nobody deletes; the chain is the archive |

The act that moves a run to Published is called an **issue** (出图), never "moving HEAD"; `HEAD` remains
the file name the repository format owns, and nothing else uses the word. A run that passed every
required check of its stage is *shared, suitable for stage approval*; a run that did not is *shared for
coordination*. Neither is "the current design".

**Stage rules, frozen:**

1. A stage is a property of a run, stated by the `StageRunEnvelope` retained in that run. The authored
   record carries no stage; a record that claims its own stage is a third identity (ADR-003).
2. A stage opens only against a `project-stage-workflow` retained in P036, and stage N>0 only with the
   retained predecessor `StageExitBinding` and closure. `StageExecutionGuard` refuses everything else
   before the first write; a raw `create_run` never acquires a stage by side effect.
3. A stage closes only through a closure record the **runner** writes from its own checks, with the
   `StageExitBinding` derived from it. A workflow may require only checks the spine can measure, so a
   frozen workflow that names an unmeasurable check is refused at freeze time.
4. Harness workflows (`equivalence-harness`, `studio-candidate-harness`) are shared containers for
   coordination and review. They never close a project stage and never become the reference run.
5. An issue cites the closure of the stage the workflow says is next. There is no issue without a
   satisfied closure, and no closure without a retained envelope.

**Why:** every spine run to date is stage 0 or a harness; no spine code writes a closure; the villa's
frozen six-stage workflow names 34 checks the spine cannot measure and is cited by no run; and the
authored record is read by path in three places and by nothing that owns it. The vocabulary git lent
us ("HEAD", "promote") hid that a run is not a version and a version is not a design.

**Do not:** read the authored file from more than one module; let a tool or the Studio close a stage
or issue on the runner's behalf; add a "current record" pointer beside HEAD; rename the `HEAD` file.
