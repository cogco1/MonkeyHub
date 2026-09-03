# The authored record, the stage rules, the name of the published design, and two leftovers

2026-09-03. Facts from two code traces (spine only; archive/ ignored) and the villa workspace. Judgment
follows each block; the plan at the end is pinned for an implementer.

## 1. The authored record

**What it is.** `input/runner/state-record.json` is the designer's statement of the design: the
`StateRecord@1` whose content digest (`StateRecord.digest`, ADR-003) is the content identity of every
run derived from it. It is a loose file inside the project root; the repository never indexes, digests
or verifies it.

**Who reads it (by path).**

| reader | how the path is chosen | what happens next |
|---|---|---|
| `tools/run_project.py:132-134` | CLI `--packs <dir>`, any directory | `StateRecord.from_dict`, handed to `run_project` |
| `tools/verify_state_record.py:85-86` | CLI `--packs` | same, plus `record.digest` into the equivalence receipt |
| `apps/…/application/projection.py:38,203,212` | constant `RUNNER_RECORD_PATH` through `layout.resolve_relative` | projection; 404 / 422 on absence or invalidity |
| `apps/…/application/candidate.py:52,145` | reuses the projection's private `_load_authored_record` | successor record → candidate run |
| `apps/…/adapters/seats.py:27,68` | constant `RUNNER_SEATS_PATH` | seat pack |

The runner itself never touches the filesystem for it: `record` is a parameter, and the runner retains
the exact record it used as run-level `state-record` (`project_runner.py:478`) with
`state_record_ref` / `state_record_digest` in `RunnerRunReceipt@3` (`:592`). That part is right: every
shared container cites the content it was derived from.

**Module carrier.** None. Three readers and two path constants, no owner. The `StageBinding` field on
the record (`state_record.py:247-260`) is read by no guard, only by a Studio honesty line and a DTO,
and it is excluded from `record.digest`. It is dead.

**Where it attaches.** As the *work-in-progress* container (ADR-007), at a path the layout owns, read by
one module. The path stays `input/runner/state-record.json` so nothing in the workspace moves. Nothing
about it is retained until a run uses it, which is exactly ISO 19650's WIP rule: the shared area holds
what was issued to it, not the working file.

**Judgment.** The finding "read by path outside P036" was mislabelled. Reading WIP by path is correct;
what is wrong is three readers, a CLI that accepts any directory, and a record field pretending to a
stage. Fix the ownership, not the location.

## 2. Stage rules

**What exists.** `ProjectStageWorkflow`, `StageRunEnvelope`, `StageExitBinding`,
`CompositeStageClosureReceipt` in `archflow/state/stage_workflow.py`; `StageExecutionGuard` in
`project_runner.py:186-331` requires the workflow, the envelope, the base, the branch, the binding
digest and (for stage N>0) the retained predecessor envelope, exit binding and closure, all loaded back
through `repository.load_json` and compared for equality. The rules for opening a stage are complete
and pinned by `tests/test_stage_workflow.py` (14 cases), `tests/test_freeze_project_stage_workflow.py`
and two runner tests.

**What does not exist.** No spine code writes a `StageExitBinding` or a closure; the only constructors
are in tests. So every stage a spine tool can run is stage 0. `tools/run_project.py` demands
`--workflow-ref` and `--stage-envelope-ref`; the Studio and `verify_state_record` open single-stage
harness workflows (`studio-candidate-harness`, `equivalence-harness`) that close nothing and are
excluded from ever being the reference run.

**The villa.** `runs/workflow-001/records/project-stage-workflow-6c15bb17….json` freezes
`villa-rotonda-as-built-stage-0-5-v1`: six stages, 32 required roles, 34 required checks, in the
monuments lane's vocabulary (`visual-deduplication`, `evidence-denominator`, `frame-glazing-separation`).
The spine measures one check (`support_contact`). The workflow is cited by nothing but its own freeze
receipt. Its source file sits in `inputs/` (plural), a folder the layout does not own. The four spine
runs (`runner-002`, `equivalence-001`, `equivalence-003`, `array-patch-001`) all report `stage_id: None`
and cite a harness workflow.

**Frozen rules.** ADR-007. The one addition to the code that the rules require: a workflow may only
require checks the spine can measure, and the runner writes the closure.

## 3. What to call the published design

ISO 19650 (BS EN ISO 19650-2 and the UK national annex) names the container states *work in progress*,
*shared*, *published*, *archived*, with status codes S0 (WIP), S1 to S7 (shared: coordination,
information, review and comment, stage approval, …) and A1 to An (published: authorized and accepted),
and revision codes P01… (preliminary) and C01… (contractual). Practice adds *issue* (a drawing issue,
the issue register), *design freeze*, *issued for construction* (revision 0) and the *record set*.

Mapping onto P036:

| P036 today | say instead | ISO 19650 |
|---|---|---|
| `HEAD` (the file) | stays `HEAD`; it is the format's name | — |
| "the HEAD state", "promote to HEAD" | **the published design**, **issue** (出图 / 发布) | Published, A-code |
| a candidate run | **shared** run; *suitable for stage approval* when its stage checks held | Shared, S1 / S4 |
| `input/runner/state-record.json` | **work in progress** | WIP, S0 |
| superseded `canonical/state-v…` | archived | Archived |
| `ProjectVersionRef.version` | the issue number; render as `issue 3` (or C03 if a revision code is wanted) | revision |

`PromotionDecision@1`, `prepare_transition` and `compare_and_swap` keep their names (retained digests
bind them); the verb the tools and the Studio expose is `issue`.

## 4. Model calls: the intent agent and the dead control plane

**Facts.** `provider_runtime.py` has zero spine callers; `responsibility.py` (1254 lines, the P053
control plane) and `adapters/model_provider.py` reach the spine only through it. All three live only in
tests and archive. `ports/model.py` is live (`geometry_proposal.py`, `project_runner.py`). The Studio's
`intent_agent.py` has three compilers of its own (deterministic, `codex exec`, Anthropic SDK), chosen by
four env vars that `settings.py` does not know; a compilation is never retained anywhere and only feeds
the deterministic proposal path.

**A provenance defect.** `RecordedProposalProvider` (`project_runner.py:127-148`) returns the proposal
the deterministic element producers built, stamped with the seat pack's declared identity
(`claude-fable-5-1-live-seat`, `claude-fable-5-1`). Every `geometry-proposal-round-01` record in a spine
run therefore says a model was invoked that was not. For the paper this is the one thing to fix before
any run is cited.

**Judgment.** One model contract (`ports/model.py`), no parallel control plane: archive the three dead
modules and their tests. The intent compilers emit a `ModelInvocationReceipt` so the Studio and the
runner share one receipt shape; the receipt is retained (`intent-compilation`) only when its proposal
becomes a candidate run, because a chat turn is WIP and a run is shared. The runner declares its own
identity on the receipts it fabricates, and the seat pack's `provider_identity` is used only when a live
provider is actually invoked.

## 5. Free vocabularies

| vocabulary | today | rule |
|---|---|---|
| record kinds | ~30 string literals in five files; `put_json` accepts any identifier; the URI parser exists three times (`refs.record_ref_from_uri`, `run_project._record_ref`, `binding.RECORD_NAME`) | one table `kind → schema` that `put_json` checks; readers import the constants; one parser |
| `ValidatorBinding.check_kind` | 7 accepted values, 1 checker; the villa's 14 relations all carry `validator: null`, so all report `unchecked` | the accepted set *is* the checker table; add `clearance_interval` and `aperture_exists` checkers, then the villa's relations declare validators |
| `Level@1.role`, `GridAxis@1.role` | free identifiers, unique per project; `ReferenceContext.axis` falls back from role to `axis_id` silently | project vocabulary, declared once by the record's entities, matched exactly; drop the fallback. No global table: grid names are the project's |

## Pinned plan (in order; each step green before the next)

1. `archflow/project/inputs.py` owns the WIP readers (`load_authored_record`, `load_seat_pack`) at
   layout-owned paths; `run_project`, `verify_state_record`, projection, candidate and the seats adapter
   import it; `--packs` goes. Delete `StageBinding` from `StateRecord@1` and its DTO. Registry entry,
   archcheck green, villa projection digest unchanged.
2. `RecordedProposalProvider` declares `provider_id="runner-recorded-proposal"`; the identity check uses
   it; the seat pack's `provider_identity` applies only to a live provider. Re-run the villa
   (`runner-003`) so a cited run tells the truth.
3. The runner writes the closure (`stage-closure`) and the exit binding from its relation checks when
   every `required_check` of the envelope's stage held; `freeze_project_stage_workflow` refuses a
   workflow naming a check outside the checker table. Villa workflow v2 in `input/` with spine checks
   only; freeze it; retire v1's file from `inputs/`.
4. `issue`: `tools/issue_project.py` (and a Studio route later) builds `PromotionDecision@1` from a
   satisfied closure and calls `prepare_transition` + `compare_and_swap`. The word HEAD leaves the
   tools' and the Studio's vocabulary.
5. Record-kind table, one URI parser, checker table = accepted set, two new checkers, exact role match.
6. Archive `production/provider_runtime.py`, `production/responsibility.py`,
   `adapters/model_provider.py` and their tests; intent compilers emit `ModelInvocationReceipt`.
