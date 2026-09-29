# Algorithm team charter

This is the durable charter of the external/supporting algorithm research team. It was
migrated from umbrella issue #119 (opened 2026-09-15), which closes when PR #470 merges.
"Parent: #119" and "umbrella #119" references in child issues (#50, #51, #73, #120, #121,
#122, #268) now mean this document.

- Cross-issue research sequencing, commitments and checkpoints:
  [Research roadmap 2026–2027](ROADMAP_2026_2027.md).
- Live status, priority and dates: the GitHub Project, see
  [Project tracking](../PROJECT_TRACKING.md).
- Concrete work stays in independently closable, benchmark-driven issues.

## Purpose

The team owns algorithmic questions that can be evaluated behind stable interfaces.
MonkeyHub core continues to own project truth, product interaction, CAD/runtime
integration and release engineering.

The boundary prevents two failure modes:

1. the algorithm team becoming a second product team that edits UI/runtime/state
   architecture ad hoc;
2. MonkeyHub core reimplementing research algorithms that should be benchmarked and
   improved independently.

## Ownership

### Algorithm team owns

Research and implementation of replaceable algorithmic capabilities such as:

- model/provider benchmarking;
- vision / raster-to-structured-geometry proposals;
- evidence extraction and semantic classification;
- architectural reasoning experiments;
- retrieval / RAG quality and evidence ranking;
- candidate generation strategies;
- evaluator / ranker metrics;
- bounded search / MCTS / beam-search style exploration;
- mesh analysis / repair / semantic-decomposition research where needed;
- benchmark harnesses, datasets/fixtures and quantitative evaluation;
- local/private model deployment experiments;
- algorithm papers, ablations and reproducible experiment reports.

### MonkeyHub core owns

The algorithm team is not responsible for:

- Canonical State / StateRecord authority;
- Stage ownership, dependency locks or revision semantics;
- project repository / archive / migration;
- Board / Arch / Study / Diagram product UI;
- desktop shell / installer / runtime lifecycle;
- ACP/session/product chat orchestration;
- OCCT/Rhino/SketchUp/Blender product integration itself;
- release/security/update infrastructure;
- broad performance plumbing such as #32 orchestration, unless an algorithm experiment
  explicitly depends on it.

An algorithm may propose a result. MonkeyHub decides how that proposal is represented,
validated, accepted, rejected and retained.

## Shared experiment contract

Every algorithm lane follows the same research discipline:

```text
input refs / exact fixture
        ↓
algorithm or provider
        ↓
proposal / score / retrieval / candidate
        ↓
verification + metrics
        ↓
receipt / provenance
        ↓
MonkeyHub integration boundary
```

Minimum retained evidence for a serious experiment:

- exact input fixture and SHA/digest where applicable;
- exact model/provider/version or algorithm revision;
- parameters / seed when meaningful;
- start/end timing;
- raw output before manual cleanup;
- success/failure/refusal state;
- quantitative metrics;
- human-review notes when the metric is not sufficient;
- code/commit used for the run;
- an explicit statement of what the experiment does **not** prove.

Do not report only screenshots of successful examples.

## Lanes (snapshot 2026-09-29)

| Lane | Issue | Team role | State |
| --- | --- | --- | --- |
| A · Direct 3D generation | #50 | Benchmark specialized text/image/sketch → 3D generators (HY-3D / Tripo / Rodin / local models) and provider-neutral adapters, with editability, topology and provenance | open |
| B · Reasoning-to-3D | #51 | Benchmark general multimodal/reasoning models on the same bounded, editable 3D tool surface | open |
| C · Study reasoning | #73 | Contribute evidence/reasoning algorithms; core keeps Study UI and storage | open |
| C · Visual perception / tracing | #120 | Raster/hand-sketch → editable evidence; geometry/topology/ambiguity benchmark | open |
| D · Search / evaluator / ranker | #121 | Bounded search (greedy/beam/MCTS/etc.) over explicit decisions, constraints and objectives | open; on hold since 2026-09-22 (see the roadmap's checkpoint history) |
| E · Retrieval / RAG | #122 | Evidence retrieval/ranking for regulations, precedents, project knowledge and technical docs | open |
| Sequential compute allocation | #268 | State-conditioned sequential budget allocation across LLM strategies | open |

The first assignment package is closed and remains evidence: #123 evaluator (closed
2026-09-21), #124 OCBA-style allocation (closed 2026-09-23) and #125 progressive semantics
→ FEA evidence (closed 2026-09-21); see [History](#history).

Related issues owned by core: #32 (agent latency / ContextPack / DesignTransaction) is a
core dependency and measurement source, not algorithm-team ownership. #46 (the
Board/sketch-to-model product path, closed 2026-09-21) was a consumer of perception work, not
algorithm-team UI ownership.

Closed experiments and results remain useful evidence; a research direction does not need
an open umbrella issue to remain valid.

## Lane rules

- **#50 · direct 3D generation.** Key question: which generator produces geometry that is
  most useful as an inspectable, editable MonkeyHub source, not merely the prettiest mesh?
- **#51 · reasoning-to-3D.** Key question: which model can reliably reason → call tools →
  verify → continue editing structured 3D over long sequences? Keep #51 separate from #50
  direct mesh generation.
- **#73 / #120 · Study reasoning and perception.** Study owns the evidence-grounded reasoning
  environment. The team contributes the research components — raster / hand-sketch →
  editable evidence proposal; primitive classification / topology extraction; comparison
  features; counterfactual selection; DesignPrior / CompositionPattern reasoning experiments;
  model-vs-deterministic-method ablations — not the Study UI or project storage. #120 isolates
  the tracing problem from the larger #73 product loop.
- **#121 · search.** Search does not replace the explicit grammar / constraints / evaluator.
  MCTS, beam or other search chooses among inspectable candidate decisions; it is not an
  opaque generator of final architecture.
- **#122 · retrieval.** Retrieval must retain exact evidence and must not turn a retrieved
  text fragment into design authority by itself.

## Integration rule

Algorithm code should live behind narrow contracts so it can be replaced without rewriting
project semantics. Example boundaries named in #119:

```text
ThreeDGenerationProvider       (#50)
GeneralAgentProvider           (#51)
EvidenceExtractor              (Study perception)
Retriever / EvidenceRanker     (RAG)
CandidateEvaluator             (search/evaluation)
SearchPolicy                   (MCTS / beam / other)
```

These are examples, not a list of existing modules. `CandidateEvaluator` and
`EvidenceRanker` exist today as lab code under `labs/candidate_evaluation/` and
`labs/retrieval/`.

MonkeyHub retains provenance and owns acceptance. No algorithm provider becomes Canonical
State authority.

## Sequencing

Do not start all research tracks at once.

- **Phase 1 · benchmark discipline.** Freeze a common experiment receipt / result format;
  run one small, fixed benchmark in #50 and #51; establish repeatable artifact + metric
  retention; make one Study perception benchmark reproducible.
- **Phase 2 · algorithms that improve the current workflow.** Visual tracing / structured
  evidence; local candidate evaluator + bounded search; retrieval/evidence ranking.
- **Phase 3 · larger research questions.** Gated; see below.

The first team milestone is **reproducible benchmarks + stable algorithm boundaries**, not a
new autonomous agent or a large dataset. Expand the #50/#51 matrices only after the harness
is trustworthy; connect #73 reasoning experiments to real extracted evidence; add
model-assisted search/reranking only after deterministic baselines exist.

## Gated topics

These are legitimate future topics. None is silently folded into an existing lane; each gets
its own issue when needed.

- Local/private models and fine-tuning: only if evidence shows prompting/tooling is
  insufficient.
- Learned evaluator / preference model: only after enough decision evidence exists.
- Richer structural, site and world algorithms: separate research lanes, never a silent
  expansion of core scope.
- Explicitly deferred on 2026-09-15, to be created separately when needed: structural /
  load-path pre-check research; external FEA solver integration; site/world model
  generation; a learned user-preference evaluator; custom fine-tuning/training.
- A lightweight architectural load trace is never presented as real FEA or structural
  certification.

The research roadmap's 2026-09-21 alignment additionally deferred reward models, LoRA, a
universal architectural evaluator, MCTS as default controller, OCBA as global harness and a
full semantic ontology as near-term prerequisites.

## What counts as a useful deliverable

Good:

> "On these 40 fixed cases, method B improves editable wall/opening F1 from X to Y, reduces
> invalid topology from A% to B%, and produces exact proposal receipts through this stable
> interface."

> "Search policy C reaches the same evaluator score with 37% fewer candidate evaluations than
> beam baseline, while preserving all hard constraints."

Bad:

> "This model looks smarter in three screenshots."

> "We added another agent that edits project files directly."

Metric deltas on fixed cases through a stable interface count; impressions and new agents
with direct write access do not.

## Team-level acceptance

- All algorithm work maps to a named lane / issue rather than ad-hoc repo changes.
- Every lane has fixed fixtures and explicit metrics before large-scale integration.
- Raw failures are retained alongside successes.
- Providers/models/algorithms are replaceable behind stable boundaries.
- No algorithm component directly owns Canonical State, Stage, accepted design truth or
  project persistence.
- Product UI/runtime changes required by an algorithm are specified as integration
  requirements and handed back to MonkeyHub core.
- At least one reproducible benchmark report exists for each active lane before claiming a
  research result.
- Algorithm experiments distinguish model capability from tool/interface/runtime limitations.

## History

- **2026-09-15 · decomposition.** The work was split into the six lanes #50, #51, #73, #120,
  #121 and #122. First wave, instead of a speculative track per person: one person
  establishes shared benchmark/receipt conventions and helps #50/#51 use the same
  discipline; one or two people take #120 because it directly unlocks the #46/#73 product
  path; one person prototypes #121 on a deliberately tiny exhaustive-search fixture; #122
  starts with corpus/query construction and lexical/hybrid baselines, not a giant RAG stack.
- **2026-09-15 · first assignment package.** #123 Evaluator (hard validity separate from soft
  score; explicit objective vectors; deterministic vs stochastic declaration; sample count /
  mean / variance / standard error / per-sample cost; Pareto / normalization / optional
  utility projection; reproducible evaluation receipts) → #124 OCBA-style
  EvaluationBudgetAllocator (equal / round-robin baselines; classical OCBA; heterogeneous
  variance; cost-aware allocation; PCS / regret vs budget Monte Carlo benchmark; later
  batch/parallel and Pareto-set extensions) → #121 broader search / MCTS / beam. Chosen
  because it is mathematically bounded, independently benchmarkable and needs no change to
  MonkeyHub UI, Canonical State or CAD/runtime plumbing.
- **2026-09-15 · physically grounded entry point.** #125: a Stage 1→4 progressive semantic
  enrichment demo reaching an FEA-ready structural projection — geometry → role → semantic
  material → sourced physical material profile → StructuralAnalysisSpec → FEA evidence — as
  the future physical evidence source for #123 and #124. OCBA/search stayed out of that
  first slice until the evidence path was real.
- **2026-09-19 · unified experiment roadmap.** Cross-issue planning moved to #176, now
  [ROADMAP_2026_2027.md](ROADMAP_2026_2027.md); it complements this charter. Near-term
  sequence: shared records / #123 evaluator → #124 allocator adapter + #125 validated FEA
  evidence, with #32 supplying exact-state context and real trace/cost measurements. Reuse
  the external OCBA support rather than reimplementing the algorithm from scratch.
  Fixed-noise allocation is the first benchmark; candidate × evaluator × fidelity × budget is
  an explicit later extension requiring separate error/bias assumptions. #173, #170 and #73
  share the same inspectable decision/evidence records but are not all activated as parallel
  paper projects. No assignees, issue states, production state authority or implementation
  claims changed.
- **Outcomes.** #123 closed 2026-09-21 with the base evaluator contract; it is not a complete
  architectural evaluator. #125 closed 2026-09-21 with a real PyNiteFEA example on retained
  entities; it is not structural certification, and surrogate, calibration, OOD and
  multi-fidelity FEA went to #124 or new narrow experiments. #124 closed 2026-09-23 after
  its phase-one experiments and collaborator handoff merged through #243; the paired results
  do not establish a reliable advantage over equal allocation. See the
  [OCBA discussion](ocba-discussion.md). #121 remains open and on hold.
- **2026-09-29 · allocation follow-up.** The algorithm/statistics discussion was recorded in
  #268 rather than a new allocator issue; see the roadmap's
  [near-term allocation work](ROADMAP_2026_2027.md#near-term-allocation-work-added-2026-09-29-from-268).

## Related documents

- [Research roadmap 2026–2027](ROADMAP_2026_2027.md)
- [Project tracking and roadmap policy](../PROJECT_TRACKING.md)
- [OCBA discussion](ocba-discussion.md) and the
  [candidate evaluation lab](../../labs/candidate_evaluation/README.md)
- [Study evidence method](study-evidence-method.md) (#73)
