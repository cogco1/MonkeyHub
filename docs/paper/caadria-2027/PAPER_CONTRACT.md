# CAADRIA 2027 paper contract

**Status:** non-authoritative planning contract  
**Target deadline:** 26 October 2026, 11:59 PM AoE  
**Current evidence owner:** P062 and its P036 project records  
**Paper-production card:** proposed P063, not yet registered

## 1. Decision question

Can an obligation-bearing, dependency-aware architectural generation protocol
bound the repair caused by a local design change while retaining unaffected
commitments and required architectural validity?

This question is narrower than asking whether ArchFlow can autonomously design
arbitrary buildings. It tests one architectural-computation claim: whether an
explicit derivation can remain operational during revision instead of serving
only as a retrospective log.

## 2. Working title

**Committed into Being: Dependency-Scoped Repair in an Obligation-Bearing
Architectural Generation Protocol**

Fallback title if executable dependency-scoped repair is not completed by the
claim-freeze gate:

**Committed into Being: An Obligation-Bearing Protocol for Auditable
Architectural Generation**

The word `replayable` must not appear in the title or contribution statement
unless an executor actually re-runs the affected dependency closure and proves
that unaffected accepted state is retained. Reconstructing state from a trace
is not external model replay.

## 3. Research questions and hypotheses

### RQ1 — production consistency

Can one typed semantic-to-geometry production chain produce reloadable,
evidence-bound outcomes across three non-isomorphic building cases without
fallback or hidden project answers?

P062 answers this as a bounded systems question. It does not establish broad
architectural generalisation or model quality.

### RQ2 — main paper question

For the same accepted local design delta, does dependency-scoped repair retain
more unaffected commitments and recompute less of the design state than a
whole-chain rebuild, without reducing mandatory-validator pass rate?

- **H1 validity:** dependency-scoped repair is not worse than whole-chain
  rebuild on mandatory validation.
- **H2 locality:** dependency-scoped repair touches fewer downstream records
  than whole-chain rebuild.
- **H3 retention:** dependency-scoped repair retains a higher proportion of
  unaffected accepted commitments than stateless regeneration.
- **H4 attribution:** the first failing obligation or validator can be bound to
  the actual affected dependency closure.

### RQ3 — exploratory boundary

Which edit classes produce the largest repair surface, and where do declared
dependencies fail to predict actual downstream impact?

RQ3 is descriptive. The paper must not turn three buildings into a population
claim.

## 4. Contribution boundary

| Claim | Current status | Paper treatment |
| --- | --- | --- |
| Exact-base proposal, validation, commit, and rejection | VERIFIED | System contribution |
| Typed dependency and invalidation closure | VERIFIED | System contribution |
| Separate future-facing state and append-only evidence history | VERIFIED | Conceptual and system contribution |
| Semantic component to geometry-program lifecycle | VERIFIED within current sandbox | System contribution with bounded geometry claim |
| Cross-building provider production consistency | UNKNOWN until P062 completes | Results only after exact outcomes |
| Dependency-scoped executable repair | PARTIAL | Must be completed and measured before headline claim |
| Replay of external LLM/MCP behaviour | NOT PROVEN | Exclude |
| General architectural-design autonomy | NOT PROVEN | Exclude |
| CAD/B-rep validity or construction readiness | NOT IMPLEMENTED | Exclude |
| Aesthetic superiority | NOT TESTED | Exclude |

The novelty is not MDP terminology, action masking, constraint satisfaction,
editability, or provenance in isolation. The paper tests their architectural
combination as an obligation-bearing control surface that bounds revision.

Architectural design is not described as naturally Markovian. ArchFlow builds
a bounded, operationally Markovian approximation by compiling the accepted
state needed for the next move while preserving a separate causal history.

## 5. Evidence architecture

The paper uses three evidence classes and never merges them:

1. **Contract evidence** — unit/integration tests that prove schemas, exact-base
   checks, fail-closed gates, persistence, and reload behaviour.
2. **Mechanism evidence** — bounded case traces demonstrating semantic-geometry
   lifecycle, rejection, revision, identity retention, and deterministic views.
3. **Experiment outcomes** — preregistered P062/P063 assignment records with
   terminal receipts, metric observations, and a reconstructable result index.

Only class 3 supports comparative empirical claims. Studies created while
debugging protocol defects may support a failure taxonomy, but cannot be
silently promoted into a preregistered comparison.

Every empirical row must include:

- case, condition, edit, and attempt identity;
- provider/model/profile identity where a provider was used;
- source record refs and content digests;
- status including failure, rejection, timeout, unknown, or exclusion;
- eligibility for comparison;
- the exact metric method and denominator.

## 6. Two-stage evaluation

### Stage A — P062 production-chain evidence

Use the existing clinic, workshop, and courtyard cases and the frozen full,
generation-context-ablation, and validation-ablation conditions.

Stage A establishes whether the production chain and named mechanisms behave
consistently enough to support the main experiment. It may report completion,
semantic-geometry consistency, architectural usability, repair/retry count,
reload equivalence, wall-clock, and model use.

P062 remains the sole owner of these records. Paper tables stay closed until
its result index is table-ready. If P062 is closed partial, the paper reports
the partial sample and missing cells explicitly.

### Stage B — proposed P063 repair-locality experiment

Unit of analysis: one repair episode beginning from the same accepted baseline
and the same frozen local delta.

#### Cases

Reuse the three non-isomorphic P062 project envelopes:

- clinic;
- workshop;
- courtyard.

#### Edit classes

Pre-register one edit of each class for every case:

1. program-relation edit, such as access, adjacency, or privacy;
2. semantic-component replacement with explicit responsibility transfer;
3. geometric constraint edit, such as clearance, span, opening, or dimension.

Concrete building answers remain project-owned evidence. Framework code owns
only generic edit, dependency, validation, and measurement contracts.

#### Repair strategies

Apply the same accepted delta under three strategies:

1. **target-only patch** — update the named target without dependency closure;
2. **whole-chain rebuild** — recompute the complete downstream design chain;
3. **dependency-scoped repair** — recompute only the typed invalidation closure.

This yields `3 cases x 3 edit classes x 3 strategies = 27` paired episodes.
The primary comparison is protocol behaviour, so the accepted delta should be
frozen once and reused across strategies. This prevents model sampling from
becoming the main experimental variable. Provider-authored proposals may be
shown as a separate demonstration but do not replace the controlled comparison.

#### Gold impact set

Before execution, an architectural reviewer records the expected affected
entities, obligations, geometry objects, and validators for every edit. A
second reviewer is preferred. Disagreements are retained rather than resolved
after seeing ArchFlow output.

#### Primary metrics

- **Repair success:** all preregistered mandatory validators pass.
- **Recompute ratio:** recomputed downstream nodes / eligible downstream nodes.
- **Commitment retention:** valid unaffected commitments retained / unaffected
  commitments present at baseline.
- **Impact precision:** correctly affected nodes / nodes reopened by strategy.
- **Impact recall:** correctly affected nodes / gold affected nodes.
- **Unintended change ratio:** changed nodes outside the gold impact set /
  baseline nodes outside that set.
- **Failure attribution:** first reported failure matches the gold affected
  obligation, dependency, or validator.
- **Cost:** wall-clock, provider calls, tokens, and deterministic operations,
  reported separately.

Small samples require paired descriptive results and confidence intervals where
meaningful; they do not justify population-level or model-ranking claims.

## 7. Paper structure and page budget

Plan for the usual CAADRIA full-paper envelope of no more than ten total pages,
but verify the 2027 template before final layout.

| Section | Target pages | Required content |
| --- | ---: | --- |
| Abstract + Introduction | 1.0 | problem, RQ2, contribution, honest result summary |
| Related Work | 1.2 | grammars, TMS/provenance, constrained generation, editable floor-plan systems |
| Conceptual Model | 1.2 | dual-track state/history, obligation, dependency, exact-base transition |
| System | 1.4 | proposal/validate/commit, semantic-geometry bridge, repair executor |
| Experimental Design | 1.2 | cases, edits, strategies, preregistration, metrics |
| Results | 1.5 | P062 boundary plus paired repair results and failures |
| Discussion + Limitations | 0.8 | generalisation, validator dependence, sandbox/CAD boundary |
| Conclusion + References | 1.7 | answer RQs, no promotional extrapolation, references |

The paper should foreground the research question, not a list of modules or
technologies. `MDP + LLM + dependency graph` is implementation vocabulary, not
the contribution.

## 8. Required figures and tables

1. **Figure 1 — terminal output versus process-first generation.** Geometry-only
   generation is contrasted with accepted state, obligations, dependency
   closure, and evidence history.
2. **Figure 2 — exact-base ArchFlow transition.** Proposal, deterministic gate,
   commit/reject, working state, canonical state, and evidence history.
3. **Figure 3 — semantic-to-geometry bridge.** Component identity, family,
   geometry object, realization, validation, and revision.
4. **Figure 4 — repair experiment.** One delta under target-only, whole-chain,
   and dependency-scoped strategies.
5. **Figure 5 — three cases and three edit classes.** Matched views with no
   implication that visual quality is the evaluation target.
6. **Table 1 — related-work distinction.** Terminal constraint handling,
   operational derivation, dependency-aware repair, and evidence ownership.
7. **Table 2 — empirical outcomes.** All 27 cells, including failures and
   unknowns.
8. **Figure/Table 6 — locality results.** Validity, recomputation, retention,
   precision/recall, and failure attribution.

Every generated figure receives a source manifest. Decorative diagrams cannot
replace result evidence.

## 9. Reviewer attack surface

- **“Obligation is only a renamed constraint.”** Show stable obligation
  identity, consumer, validator, evidence, resolution, and downstream effect.
- **“Dependency closure is only provenance.”** Demonstrate executable reopening
  and retention, not merely trace display.
- **“The baseline is weak.”** Apply the same delta to all strategies and keep
  provider, baseline, validators, and budget fixed.
- **“The validators define their own success.”** Publish validator contracts,
  add an independent gold impact set, and report false-positive/false-negative
  closure behaviour.
- **“Three buildings prove nothing general.”** Claim mechanism transfer across
  three non-isomorphic cases, not architecture-wide performance.
- **“Voxel/sandbox output is not CAD.”** State that discrete or neutral geometry
  is the controlled experimental substrate and separate it from future exact
  B-rep realization.
- **“Failures were removed during development.”** Preserve the P062 failure
  taxonomy and disclose which studies were protocol repair rather than final
  assignments.

## 10. Delivery schedule

### 27 August – 3 September: claim and repository freeze

- preserve the current dirty worktree as attributable, reproducible changes;
- freeze the paper question and claim ledger;
- confirm CAADRIA abstract status and the 2027 full-paper template when issued;
- decide whether `replayable` remains an executable claim.

### 4 – 10 September: finish or bound P062

- complete the frozen assignments without hidden retry; or
- explicitly close P062 as partial with all missing cells visible;
- generate no paper result table until the table-ready gate passes.

### 11 – 22 September: implement the narrow repair experiment

- register P063 only after the P062 gate;
- implement only the repair executor, measurement hooks, and independent impact
  annotations needed by RQ2;
- avoid unrelated CAD-kernel, UI, provider, or autonomous-agent expansion.

### 23 September – 3 October: run and freeze evidence

- preregister the 27 repair episodes;
- run each frozen assignment once under its stated policy;
- retain all failures and unknowns;
- reproduce indexes and tables from a clean checkout.

### 4 – 13 October: analysis and full draft

- generate evidence-bound figures and tables;
- draft Methods and Results from records, not memory;
- write Introduction and Discussion only after the results are fixed.

### 14 – 20 October: adversarial review

- theory/novelty review;
- architecture and experiment review;
- reproducibility and anonymity review;
- manual citation and AI-use audit.

### 21 – 25 October: submission freeze

- fit the official template;
- render and inspect the PDF;
- remove author-identifying information for double-blind review;
- verify every claim, figure, reference, and source manifest;
- submit before the 26 October AoE deadline.

## 11. Claim-freeze gates

- **10 September:** no new headline contribution after this date.
- **22 September:** dependency-scoped repair must execute end to end, or the
  title falls back to the auditable-protocol version.
- **3 October:** empirical evidence freezes. Later reruns require an explicit
  correction record and cannot replace unfavorable outcomes silently.
- **13 October:** Results text freezes except for verified corrections.
- **20 October:** references, anonymity, AI-use statement, and figure provenance
  must be complete.

## 12. Immediate next gate

Do not create or claim P063 yet. The immediate engineering objective remains:

> finish the frozen P062 matrix, or close it honestly as partial, while making
> the current dirty-worktree implementation reproducible.

Only after that boundary should the registry introduce P063 with a write scope
limited to the paper package, experiment adapter/metrics required by RQ2, exact
project evidence, tests, and generated mapping files.
