# Research roadmap 2026–2027

This document holds the cross-issue research plan that lived in issue #176 (opened
2026-09-19), which closes when PR #470 merges. References to #176 as the research roadmap now
mean this document. It records the research thesis, commitments, shared experiment rules and
the dated checkpoint history.

- Team boundaries stay in the [algorithm team charter](algorithm-team.md); this roadmap
  complements it and does not replace it.
- Live status, priority and dates: the GitHub Project, see
  [Project tracking](../development/project-tracking.md).
- Implementation stays in the existing issues. The plan does not assign people, change
  assignees, or authorize experiments to rewrite core in parallel, and it is not a pipeline
  that every experiment must run through.

## Research thesis

> Study how AI forms judgement inside a persistent, inspectable, revisable design state:
> the basis, dependencies, consequences and revision of design decisions, not only the
> generation of a final building.

What is unified is the **traceability of design decisions and evidence** — not one universal
agent, universal score or universal latent vector. Every method must be able to fail, be
replaced and be evaluated on its own.

## Current commitments

Recorded in #176 on 2026-09-19.

- **CAADRIA 2027 full paper comes first.** Official deadline 2026-10-26, 23:59 AoE, as checked
  on 2026-09-19 against <https://www.caadria2027.org/> and
  <https://caadria.org/openconf2027/openconf.php>. Internal target: submission package
  complete on 2026-10-24. The paper keeps the established state / dependency / replay /
  local-revision claim and its original experiments; the new Critic, latent-cognition, OCBA,
  FEA and Study work is not folded into it.
- **OCBA and FEA are two independent papers on one connected system.** Internal targets
  recorded 2026-09-19; re-confirm after #124 closed: OCBA around 2026-12-05; FEA release
  around 2026-12-27, with 12-29 kept as buffer and no slip to 12-31. These December dates are
  internal targets, not conference deadlines or completed commitments.
- **Study this year is instrumentation and evidence collection.** Full interpretation, prior
  transfer and writing move to 2027 spring–summer (internal target recorded 2026-09-19;
  re-confirm after #124 closed).
- **No fifth paper line.** #170 and #173 are experiments that can fold into the existing
  Study/process research; neither is a paper by default.

## Owner map (snapshot 2026-09-29)

| Open question | Owner | State | Not responsible for (#176 §1) |
| --- | --- | --- | --- |
| Exact-revision ContextPack, transaction/execution boundary, deterministic orchestration, compiled context, cost and wake trace (TurnTrace) | #32 | open; a measurement/runtime dependency, used when the active slice needs instrumentation (2026-09-22 queue) | waking another model for every step |
| Stage results, project-side long-term memory, active decision/commitment slice | #185 | open; owner since 2026-09-21; #183 closed on 2026-09-23 and left the broader Stage-control requirements here | — |
| Bounded candidate / branch / lineage / search policy | #121 | open; on hold since 2026-09-22 | the project-state representation itself; presuming MCTS is optimal |
| Stage-aware breadth vs verification / progressive commitment | #203 | open; on hold since 2026-09-22 (its 2026-09-29 comment separates it from #268 but does not lift the hold) | — |
| Local deterministic event gating / when to wake a model | #204 | open; used only when the active slice needs a wake-policy comparison (2026-09-22 queue) | — |
| Typed cheap judgement (Jev) for routing and on-demand wake-up | #205 | open | — |
| Precedent/evidence prior: case evidence, counterfactuals, competing explanations, rejectable DesignPrior | #73 | open | recovering the architect's single true intent; creating truth directly from generated text |
| Entry points for image evidence and external knowledge | #120 / #122 | open | becoming a blocking prerequisite for the other experiments |
| Native-project bounded execution harness | #260 | open | — |
| Sequential strategy / compute allocation | #268 | open | — |
| Source-bound visual observation / readback for Modeling and project projections (cites #176) | #303 | open | — |
| Sources, model comparison, external execution records, visual projection | #50 / #51 (#118, #159 closed) | #50, #51 open | automatically becoming a unified research claim or a mandatory step |

Closed with retained evidence: #118 and #159 (2026-09-19); #123, #125, #170, #173 and #195
(2026-09-21); #183 and #124 (2026-09-23). Closed issues are not reopened merely to represent
an ongoing topic.

## Shared research record

Share one lightweight, exportable **Decision Episode** view over existing records — candidate
lineage, EvidenceLedger, ModelInvocationReceipt, TurnTrace — instead of a new state system.
The name exists for explanation and export only; it requires no new production storage,
general workflow engine or parallel revision history.

Each episode is minimally locatable to:

```text
exact input project/revision + fixture/version
intent / explicit assumptions / protected relations
parent candidate + proposed decision/patch
relevant evidence refs + stage/readiness context
challenge, if any: target / type / severity / evidence / proposed test
action disposition: branch / revise / defer / candidate-step-commit / reject
evaluation result refs + uncertainty kind + cost
affected dependency closure + actually executed/rebuilt objects
result revision / artifacts / human acceptance status
algorithm/model/code/policy version + seed where supported + timestamps
```

Rules:

1. Fields are preferably references to existing authoritative records; they never copy out a
   second design source of truth.
2. They record publicly reviewable assumptions, operations, evidence and reasons, not a
   model's private chain of thought.
3. `candidate-step-commit` only advances an experimental candidate. It is not project
   acceptance; acceptance, Stage locks and cross-stage revision still go through the existing
   paths.
4. After save/reopen, recorded actions and checks can be replayed. Never promise that
   re-running a stochastic model will say exactly the same thing.
5. The relevant subgraph may be narrowed per task, but a hard constraint that decides
   legality never drops out of the final deterministic validation because retrieval missed
   it.
6. Missing fields are explicitly `unavailable`; a nonexistent source, uncertainty or cost
   never becomes a default zero or pseudo-evidence.
7. Datasets and caches bind to an exact revision. Invalidated historical evaluations are
   marked, not erased, and old evaluations are never reused for new candidates.

First deliverable named in the plan: a field-mapping note, one real episode export, one
failed episode and a reviewable reopen/replay test. Find the existing owners and real callers
before deciding which files to change.

## Evaluation semantics

Applies to #123 / #124 / #125 and constrains #268, whose Phase 2 (strategy × compute
fidelity) is a Tier-2 question. The agreed collaboration direction is wider than #124's first
round (repeated sampling of fixed-noise candidates), so implementation has two tiers:

- **Tier 1:** fixed candidates under one evaluation condition, with repeated noisy samples.
  Validates the existing allocator against baselines.
- **Tier 2:** candidate × evaluator × fidelity × budget, only after an explicit error/bias
  model exists. It is an explicit multi-fidelity extension; Tier-1 theoretical guarantees do
  not transfer without checking their premises.

The evaluation interface distinguishes at least:

```text
candidate/revision + evaluator/version + objective definition
fidelity + analysis assumptions + applicability domain
value(s) + units + hard-valid / invalid / unavailable
uncertainty kind: deterministic | sampling | surrogate/model error | input uncertainty
sample statistics where meaningful
reference fidelity / error-or-bias evidence where available
actual cost + estimated next cost + failure status
```

- Never rerun deterministic FEA on identical input to manufacture "statistical samples".
- Never merge surrogate error, fidelity bias, input-parameter uncertainty and Monte Carlo
  variance into one variance. Without validated calibration information, say "unknown".
- The OCBA line consumes this evidence through the interface and does not intrude into the
  solver. The FEA line validates its model, numerics, error and reference conditions
  independently and does not depend on OCBA succeeding.

## Experiment design rules

### Shared fixtures

Two small shared fixtures instead of one giant all-purpose demo. They do not replace the
CAADRIA cases and experiments.

- **S · spatial decision fixture.** Suggested: two side volumes, one multi-storey void, one
  shared circulation path and one adjustable core position; scale and geometry stay
  checkable, and early objects need not be pre-classified as Wall/Slab. Fixed tasks: enlarge
  usable area, move the core, change the void width, adjust floor height, change a slab
  termination relation, one protected-relation conflict. Each task gets a verified start,
  allowed decision granularity, reference error locations and feasible/infeasible examples.
- **F · physical evaluation fixture.** A small 2D two-bay frame; solver, boundary conditions
  and applicability domain are checked by the FEA collaborator. Topology is fixed first;
  span, section or material parameters vary; topology change is an explicit later/OOD
  condition. Flow: same retained entity + sourced necessary semantics → readiness check →
  structural-analysis projection → reference solver → response + declared uncertainty +
  measured cost + provenance. First checks: reaction equilibrium, an independently checkable
  simplified reference solution, unit consistency, response to parameter change, explicit
  refusal when material/support/load is missing, exact-revision binding.

S and F share records and the evaluation interface; they need not use the same model or one
score to prove every capability.

### Task design

- Separate **decidable-constraint tasks** (可判定约束任务: connectivity, boundaries, fixed
  counts, explicit protection conditions, with a reference result from an independent
  program) from **composition/preference tasks** (构成/偏好任务: spatial hierarchy, whether
  the void still organizes the plan), which keep human judgement and disagreement and are
  never disguised as geometric truth.
- Dimension thresholds are **fixture constraints** (夹具约束), not building codes.
- Seeds are not cases. Formal evaluation covers different layout/parameter families, not
  dozens of retries on one attractive model.

### Process critique (E1, #173)

- Hypothesis: under the same state, tools, model and resource cap, critique targeted at
  decisions with high downstream impact may reduce error propagation and rework compared with
  after-the-fact repair, without suppressing feasible branches.
- Conditions: A single agent + the same deterministic validator, no semantic Critic, free to
  keep generating/revising within the same budget; B critique and repair after a complete
  candidate; C critique interleaved at fixed decision checkpoints; D critique at key decisions
  selected by explicit dependency impact / commitment risk.
- All conditions keep the same hard checks, tools, starting evidence, success conditions and
  stop rules. Compare quality curves at **matched budget**; D must not "win" by spending
  several times more model calls. Add fixed/random-checkpoint conditions with an **equal
  Critic-call count** to separate "checking more" from "checking at a better time".
- 3 tasks × 4 conditions × 3 repetitions is a smoke/pilot, not statistical validity. Formal
  sample size follows pilot variance, layout diversity and a pre-declared effect target.
- Record final hard-constraint pass and incompletion rates; time to the first **valid**
  challenge, steps between the wrong decision and its detection, and the dependency depth of
  descendants already formed; predicted impact closure, actual rework objects and real
  recompute cost, kept separate; unfounded hard blocks, wrong challenges, dead loops,
  timeouts, branch diversity; all model/retrieval/geometry-check cost, latency and human
  correction. **Undetected errors count as failures/censored**, never only the successfully
  detected samples.
- **A Critic never scores itself.** Error origins and challenge correctness come from
  controlled planted errors, deterministic checks or independent review. Stage-appropriate
  hard constraints are tested first; preference is a separate experiment.
- Critic boundaries: ordinary CAD operations are checked by software, not by a model at every
  step; semantic challenge only where an assumption can affect many downstream decisions;
  missing evidence returns missing evidence / defer, never a disguised hard fail; a hard block
  needs verifiable evidence that applies to the current Stage, while preference only creates
  a soft objection or branch; synchronous checkpoints first, and a shadow concurrent version
  only after that protocol is correct, verifying the exact criticized candidate version and
  rejecting stale challenges.
- Gate E1: a repeatable rework or quality improvement under pre-declared budget/constraint
  non-inferiority. If fixed critique is as good, keep the simple mechanism and report that
  dependency gating added nothing. Keep negative results; do not add agents to mask them.

### Retrieval and cognitive projection (E2, #170)

- Compare in order: (1) full relevant state input where it fits the baseline context — if it
  exceeds the budget, record that as infeasible instead of silently changing the task;
  (2) explicit geometric/spatial relations + dependency-graph retrieval; (3) hybrid vector
  similarity + explicit graph; (4) if needed, an ablation without provenance/dependency
  features.
- Start with simple features / off-the-shelf encodings as a replaceable index; do not presume
  training a new model. Vectors only select what to attend to; exact geometry and final
  protection conditions are still verified by canonical/runtime queries.
- Tasks: impact scope of a height change, restrictions near the core, similar relations
  across labels, same-shape objects with different dependencies, updating the projection
  after a revision. Metrics: recall of relevant entities/relations, dangerous constraint
  omissions, end-to-end edit success, input tokens, total index/update/query cost, stale-result
  rate. Attractive embedding clusters alone are not success.
- **Exclude future states:** retrieve only states and history that existed at that time.
  **Split by layout/trajectory family**, never scattering adjacent states of one edit chain
  across train and test.
- Gate E2: the hybrid method shows a measurable advantage over the explicit-graph baseline
  while hard checks and task success hold. Otherwise use the graph baseline; the latent
  direction stays experimental and blocks nothing.

### Physical evaluation (FEA)

A "high-fidelity reference" also rests on declared physical assumptions and numerical
verification. An FEA reference is **not** real-world building safety certification.

### Study priors

- This year, record exact sources, corrected figures, relations and proportions, controlled
  modifications, competing explanations, human choices and counterexamples, consuming #73's
  existing API/comparison projection rather than a new canvas or store.
- 2027 spring–summer experiment: case / tested design experience → inspectable relations →
  multiple explanations → controlled counterfactuals → DesignPrior with applicability
  conditions → candidates and critique in new tasks → external test / human judgement → new
  reusable experience.
- Controls: no prior / human-given prior / Study-derived prior. **Priors must be rejectable
  and revisable** and must be tested on both applicable and **non-applicable** cases, not only
  on successes that fit the theory.
- What is inferred from drawings is a testable explanation, not the original author's mental
  history; a parametric dependency is not automatic proof of real-world causality.

## Gates and phases

Planned 2026-09-19. Later checkpoints changed the queue; see
[Checkpoint history](#checkpoint-history).

| Phase | Dates (plan) | Content |
| --- | --- | --- |
| A · freeze scope and interfaces | 2026-09-19 — 09-22 | List CAADRIA deliverables that new experiments may not crowd out; confirm the episode field mapping and #123's minimal return value; prepare one structured S fixture with reference tasks (no waiting for image recognition/UI); FEA collaborator freezes F's physical scope, solver candidates and validation cases; check the existing allocator's inputs/outputs with the external OCBA support instead of rewriting it |
| B · trustworthy evaluation and records | 2026-09-23 — 10-05 | #123 keeps hard validity / objective vector / unavailable / uncertainty kind / cost separate and implements a few exactly checkable metrics; #124 runs fixed candidates on known-truth synthetic distributions with uniform/simple baselines and the existing OCBA through one interface, keeping failed samples and seeds; #125 solves and validates F, with a minimal evaluator returning performance + uncertainty description + cost; #32 exports one success and one failure trace with checkable replay and exact sources; #73/#170/#173 only reuse existing records to add needed evidence and offline examples — a full set of new modules is not part of Gate B |
| C · protect the CAADRIA paper | 2026-10-06 — 10-26 | Kaiwen focuses on frozen methods, experiments, failure analysis, figures and writing; collaborators may continue batch validation, synthetic trials, data cleaning and regression on frozen interfaces; no significant OCBA/FEA scope growth; #170/#173 are not dependencies of this submission |
| D · complete OCBA and FEA | 2026-10-27 — 12-29 | 10-27—11-09 real evaluator connected, simple surrogate baseline, traceable candidate × evaluator × fidelity trials; 11-10—11-23 OCBA cost/noise/bias ablations, FEA calibration, OOD and false-safe checks, first drafts; 11-24—12-05 freeze OCBA main experiment and preprint; 12-06—12-20 FEA continuous design-revision comparison, error/cost analysis, writing; 12-21—12-29 FEA internal review and release buffer, target around 12-27 |
| E · process critique, then spatial attention | 2027-01 — 02 | E1 (#173) → E2 (#170) |
| F · Study interpretation and transfer | 2027 spring–summer | #73 case evidence, controlled counterfactuals and human judgement produce rejectable DesignPriors, tested for transfer to new layouts/tasks that took no part in building the prior; valid #173/#170 results are components of this study, not extra papers |

- **Gate A:** another member can restate, mock-call and independently check the interfaces and
  tasks, and no new production state authority was added.
- **Gate B:** candidates can be evaluated, recorded, reopened and compared; deterministic
  checks and known-ground-truth synthetic tests are correct. **No complex search or learned
  models before Gate B.**
- **A small offline #173 pilot during Phase D** only if the two established lines are not
  crowded out, the record base passed Gate B and a clearly designated independent executor
  exists; otherwise it is postponed rather than adding another formal workflow to the same
  core owner.
- **Gate D:** each method has reviewable results, failure domains and raw data, not only "the
  system is connected". **Dates are plans; if evidence falls short, shrink the claim or
  scope** instead of forcing a conclusion.
- **One new exploratory main experiment at a time** (Phase E), with separate ablations per
  experimental dimension instead of opening every module at once.

## Roles and parallelism

| Role | Core deliverables | Not responsible for |
| --- | --- | --- |
| Kaiwen / MonkeyHub core | Research questions, architectural state/interfaces, experiment design, cases, integration, writing and judging architectural significance | Implementing and running every algorithm/solver alone from scratch |
| Syracuse FEA collaborator | Structural model, solver integration, numerical validation, batch data, surrogate, calibration/OOD analysis | Changing project truth, Stage permissions or Hub UI |
| External OCBA algorithm support | Wrapping the existing algorithm, allocation logic, assumption checks, theory/algorithm validation | Rebuilding the FEA solver or taking over product orchestration |
| Development agents | Declared local interfaces, fixtures, tests, export/automation; deliver raw artifacts and failure records | Deciding research claims on their own; automatically interpreting unreviewed results as success |

- At most three standing work channels: core/paper, FEA, OCBA. Only one new exploration at a
  time; #170/#173/#73 do not all become formal parallel projects that need daily firefighting
  from Kaiwen.
- One underlying schema/record/runtime owner is changed by only one implementation channel;
  other channels work against stable fixtures/interfaces first.
- Before each implementation task, list the existing owner, exact file scope and test
  commands. Review is never only a written "all tests passed" summary.

## Research sequencing rule

Do not open new conceptual lanes merely because an algorithm is available. First identify a
falsifiable question, its existing owner, the fixed comparison and the evidence required to
close it.

```text
bounded question
→ fixed fixture / real task
→ baseline
→ one changed variable
→ retained raw results
→ close with positive, negative, or conditional result
```

This replaces a permanently open "research roadmap" issue.

## Near-term allocation work (added 2026-09-29 from #268)

Not part of #176. #268 is the active owner for state-conditioned sequential allocation. Its
progression (Experiments A–D in #268's 2026-09-29 handoff; close criteria are in #268):

1. repeated-sample V0 with a fixed per-rollout token cap;
2. strategy × fixed compute-fidelity matrix;
3. adaptive strategy/fidelity allocation under matched total budget;
4. only then, if justified, shared reasoning + judge budget experiments.

This sequencing is intentionally narrower than a universal agent scheduler. The evaluation
semantics above apply: step 2 onward is a Tier-2 question.

## Checkpoint history

### 2026-09-19 · plan (issue body)

The body set out the thesis, commitments, shared record, evaluation semantics, experiment
design, gates and roles above, plus the per-issue roles for that round (§1):

| Issue | Role | Not responsible for | Order in that round |
| --- | --- | --- | --- |
| #32 | Exact-revision ContextPack, transaction/execution boundary, TurnTrace cost records | Waking another model for every step | Shared base; fill only gaps real experiments need |
| #123 | Evaluation interface for validity, objective, preference, uncertainty, cost | Search decisions or accepting project truth | First-priority research interface |
| #125 | Progressive semantics, capability preconditions, structural projection, verifiable FEA evidence | New Stage/structural project state; inventing physical parameters from material names | Connect to the evaluator; numerics by the FEA line |
| #124 | Allocate evaluation budget across candidates; gradually support evaluator/fidelity choice | Rewriting the existing external OCBA algorithm from scratch; a universal task scheduler | Synthetic-distribution baseline first, then a real evaluator |
| #121 | Bounded decision space, branch lineage, replaceable search policy | The project-state representation; presuming MCTS is optimal | Fixed candidates / enumeration / simple beam first; complex search later |
| #173 | Evidence-based challenges near important decisions; compare critique timing | Debating every CAD op; a Critic certifying itself correct | Records first; bounded pilot after mainline delivery |
| #170 | Select relevant spatial/dependency subgraphs; replaceable cognitive projections | Replacing geometric truth with vectors; training large 3D models now | Explicit graph retrieval before latent ablation |
| #73 | Case evidence, counterfactuals, competing explanations, rejectable DesignPrior | Recovering the architect's single true intent; creating truth from generated text | Evidence this year; full transfer experiment in 2027 |
| #120 / #122 | Entry points for image evidence and external knowledge | Becoming a blocking prerequisite for all the experiments above | Connect as consumers need; verified structured input allowed first |
| #50 / #51 / #118 / #159 | Sources, model comparison, external execution records, visual projection | Automatically becoming a unified claim or mandatory step | Optional input/execution/display; no wider claims this round |

At that time #73 already had merged experimental API / comparison-projection evidence, which
did not amount to a complete Study UI or a validated architectural-judgement method; #170 and
#173 were hypotheses, not results; the plan itself ran no new performance or numerical
experiment.

First four bounded work packages, inside the existing issues rather than ten new ones:
**A · Core** in #32/#119 (owner audit, episode field mapping, one success and one failure
trace, reopen/replay test; no UI, no new authoritative store; record which data real runs
cannot yet supply); **B · Evaluator** in #123 (interface and fixture, a few deterministic
metrics, missing vs invalid, a synthetic noisy evaluator, unit/version/cost/uncertainty
tests; one contract for OCBA and FEA; no search); **C · FEA** in #125 (solver choice note,
parameter domain and assumptions, reference checks, batch runs and structured results,
refusal without physical preconditions; no surrogate training first, no Canonical State
authority change); **D · OCBA** in #124 (thin adapter for the existing allocator, uniform
control, known-truth synthetic tests, cost / selection-error / regret curves; before B is
ready, a fixture of fixed return values agreed by both sides, with no second metric
semantics).

Completion standard of the moment: one explicit design change leaves reviewable before/after
state, evaluation, dependency impact and cost, and the same interface serves one synthetic
allocation experiment and one trustworthy physical evaluation.

### 2026-09-20 · experimental checkpoint — from terminal generation to progressive commitment (issue body)

Evidence from the PR round below. At the time of writing these PRs were still under review
against main `3a92b41460b52c04963278a3300a29c34744c43e` and the checkpoint did not treat
them as production facts; all six merged on 2026-09-20.

| Work | What was actually observed | What it does **not** prove |
| --- | --- | --- |
| #183 / PR #202 | A real six-turn GPT-6-Astra retained-candidate loop completed courtyard generation, lowering, setback, an observed correction from a 4 m trial to 2 m, cold continuation, and a later wall task from a fresh session, while accepted HEAD stayed unchanged. | One bounded success does not prove general architectural design quality or low latency. |
| #195 + #170 / PR #198 | In a small synthetic observation pilot, structured exact facts answered 81/81 audited facts; image + exact query answered 80/81; image-only mostly abstained where identity/scale was unavailable and made some wrong assertions. For context selection, full and hybrid selection both reached 5/5 relevant-node completeness; mean context size fell from 7,375 to 3,003 bytes in the hybrid condition. | Vision is not shown to be unnecessary, and one embedding method is not shown to generalize to real projects. |
| #173 / PR #197 | The four-arm real Critic pilot produced 12/12 completed runs but **zero target errors**, so there was no real propagation to prevent. Interleaved review increased model calls, time and API-equivalent cost. A scripted injected-error control showed earlier review can reduce propagation **when an error actually exists**. | No evidence for making an always-on Critic the default design architecture. |
| #123 + #124 / PR #196 | The evaluator/allocation boundary ran 34,200 controlled executions. OCBA improved selection in some close-mean conditions but lost to simpler policies under other variance structures; the tested cost-aware rule did not universally dominate. | No allocation policy is a universal winner; these are not architectural-quality scores. |
| #73 / PR #200 | Study can retain observation → competing interpretation → counterfactual prediction → measured consequence → revised/conditional prior. A real model prediction was preserved when wrong and later recognized as wrong after measurement. | Does not recover unique historical design intent or validate a universal precedent prior. |
| #125 / PR #199 | The same retained entities acquired semantic role, sourced physical properties and a structural-analysis projection progressively; a real PyNiteFEA example changed response when E / Iz changed while geometry identity stayed fixed. | Not structural certification; does not require early BIM semantics. |

Emphasis shifted away from `one proposal → more prompting → always-on Critic → terminal
completion` toward:

```text
persistent exact world
→ selective / local observation
→ multiple low-commitment branches
→ hard validity + multi-objective evaluation
→ preserve Pareto / diverse survivors
→ deepen selected branches
→ stronger verification as commitment and dependency cost rise
```

Working principle: **compute should follow commitment.**

- Low commitment / cheap revision: spend budget on breadth, alternatives and information gain.
- Higher commitment / expensive downstream closure: reduce breadth; spend on verification,
  simulation and targeted critique.
- Critique is an optional intervention, not a default tax on every decision.
- Query or compute exact machine-known facts where appropriate; visual observation stays
  necessary for appearance, composition and still-unformalized design judgement.
- Conversation/session continuity is disposable; project continuity comes from retained
  project state and exact references.

Next falsifiable experiments: **#203** stage-aware branching and progressive commitment —
single-path generation vs single-path review vs multi-branch + evaluator vs multi-branch +
adaptive evaluation under matched total budget; at Stage 1, does spending compute on diverse
alternatives + Pareto/diversity preservation give better feasible-option coverage and less
later rework than repeated critique of one path? It consumes #121, #123 and #124 without
making any policy mandatory. **#204** event-driven design cognition — can exact local events
and derived salience decide when the reasoning model wakes, instead of re-entering it after
every successful tool/update? Compare eager reasoning with deterministic event gating,
measuring missed meaningful changes as well as saved wake-ups/cost.

Revised roles: #121 becomes the main bounded branching/search mechanism for the next Stage-1
experiment, with brute-force / greedy / beam as baselines before MCTS; #123 stays evaluator
semantics without reopening a universal reward-model question; #124 stays evaluation-budget
allocation, a policy used as an experimental variable, not a default scheduler; #173 is no
longer a near-term architecture dependency — once its PR is integrated, its first synchronous
experiment counts as a completed negative/limited result, and future Critic work targets
higher-commitment or error-rich conditions; #170/#195 provide observation/selection evidence
for #204 rather than being required for all design work; #73 contributes one conditional prior
only as a later ablation, and precedent borrowing stays rejectable and source-bound; #125
supports progressive commitment, with deeper semantics/physics available when needed rather
than forced into Stage 1; #183/#32 stay the product/runtime foundations for real
observation-revision and measurable cost.

Stop lines for the next round:

- no other universal controller or second state store;
- no learned latent representation as a prerequisite for Stage-1 exploration;
- evaluator output is never one scalar architectural truth;
- Critic is not made mandatory because a scripted injected-error case benefits from early
  detection;
- lanes are not all expanded at once; first run #203 and #204 against fixed, reviewable
  fixtures.

### 2026-09-20 · closeout checkpoint, #213 (comment 2026-09-21T01:36Z)

Main `26cadb140d1a5b35506d77c610a3efe87447a1d0` was verified and eight issues closed, each with
its completion boundary, evidence and handoff. Closing is not the end of a research
direction.

- #125: same-entity progressive semantics → real PyNite experiment complete; surrogate models,
  calibration and multi-fidelity discussion go to #124.
- #173: four-arm real pilot complete but with no target-error opportunities, so no
  review-repair benefit was shown; a new error-rich / high-commitment ablation goes to #203.
  Extra review frequency increased consumption; this is not generalized to "an independent
  Critic is necessarily more expensive".
- #195: first-round representation comparison and interface blocker answered; the production
  observation loop goes to #183, local observation / reasoning wake-up policy to #204.
- #170: two rounds of fact selection complete; with full guards, the results do not support a
  learned selector as a default dependency; the negative result and key misses are retained.
- #123: base evaluation contract complete; real professional evaluators must be built
  separately, and the four massing metrics are not a comprehensive architectural evaluation.
- #46: hand tracing → editable 3D complete; automatic visual interpretation / perspective /
  real-drawing benchmark goes to #120.
- #90: current Board in-place refresh and update/view prompt complete; cross-workspace,
  mobile and quiet-hours explicitly deferred.
- #148: numeric elevations and explicit stacking complete; datum drag interaction goes to #137.

#185, #183, #32, #124, #203, #204, #205, #121, #73, #176, #137/#136, #120/#122 and #58/#54 kept
their own open boundaries. Answers to finished experiments and next-round questions are
tracked separately; clearing issue counts does not replace research. Verify, generated-client
and Windows candidate build / isolated-install CI passed on that main; CI is not a rerun of
all labs. Desktop update, reopen and retained-state verification continued in #213, with no
formal release.

### 2026-09-21 · research alignment — narrow the open question before adding algorithms (comment 2026-09-21T20:10Z)

After a discussion with the algorithm and FEA collaborators, the main line was narrowed so that
new algorithm names do not end up defining the problem. Four claims stand for now:

1. Design continuity comes from project-side State / dependencies / accepted decisions, not
   from continuous chat.
2. Not every design action first has natural-language semantics. Semantic, role and physical
   commitments can be added progressively as Stages advance; natural language is only one
   input/projection.
3. Much of the work needs no LLM. What geometry, dependencies, rules, solvers, FEA and
   cost/query paths can do deterministically stays in the software layer.
4. The key gap: how a person's keep / reject / prefer / avoid / require / lock / defer decisions
   become persistent, scoped and invalidatable (可失效) project-side records that actually
   change behaviour in the next round.

Owner map at that time:

| Open question | Owner |
| --- | --- |
| Stage results, project-side long-term memory, active decision/commitment slice | #185 |
| The real observe → judgement → persist → next-revision loop | #183 |
| Deterministic orchestration / compiled context / cost & wake trace | #32 |
| Bounded candidate / branch / lineage / search policy | #121 |
| Stage-aware breadth vs verification / progressive commitment | #203 |
| Local deterministic event gating / when to wake a model | #204 |
| Budget allocation for noisy/expensive evaluation | #124 |
| Precedent/evidence prior | #73 |

#123 / #125 / #170 / #173 stayed closed within #213's boundaries rather than re-expanding
because new questions came up.

Deferred (暂缓), not near-term mainline prerequisites: preference models, reward models and
LoRA; a "universal architectural evaluator"; MCTS as the default controller; OCBA as a global
harness; a complete semantic ontology or the assumption that every action must have
semantics. #124 enters real architectural experiments only once `candidate + stochastic
evaluation X + uncertainty + cost` is explicit; deterministic FEA / geometry / constraint
results are not artificially resampled to fit OCBA.

Near-term gate: can one human design judgement, without relying on an old transcript, be
persisted on the project side and correctly constrain or bias the next step in a fresh
session / later Stage, while allowing scope, supersede, revoke and revision invalidation?
Until that loop has a stable data structure and real acceptance, no new search or learning
algorithm becomes a mainline dependency.

### 2026-09-22 · short execution queue (comment 2026-09-22T08:03Z)

Written while Kaiwen stepped back to digest the project: open no new conceptual lanes.

- Active autonomous work — Codex: #230 closeout → #185 narrow Decision/Commitment
  persistence → #183 feedback re-entry acceptance. Claude: #223 owner/state audit; #234
  current UI/code reality audit. Panny: #217 quick visual fix; #216 bounded render-results
  workspace; #218 only after #223 clarifies representation/source persistence.
- Hold: #121 bounded search; #203 stage-aware branching; #124 real OCBA extension; #229
  mobile/cross-device architecture; preference/reward-model training, LoRA, universal
  evaluator work.
- #32/#204 remain measurement/runtime dependencies, used only when the active #185/#183
  slice needs instrumentation or a wake-policy comparison, not as new standalone architecture
  work.

Goal: let implementation and audits move for a few days without new theory decisions from
Kaiwen.

## Related documents

- [Algorithm team charter](algorithm-team.md)
- [Project tracking and roadmap policy](../development/project-tracking.md)
- [OCBA discussion](ocba-discussion.md) — what the allocation experiments allocate, their
  results and the proposed FEA interface.
- [Study evidence method](study-evidence-method.md) — #73 method note.
- [Performance experiments](performance-experiments.md) — end-to-end latency/cost
  comparisons for #32, #170, #185, #204 and #205.
