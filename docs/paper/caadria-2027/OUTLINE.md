# CAADRIA 2027 manuscript outline

**Status:** drafting skeleton, subordinate to [PAPER_CONTRACT.md](PAPER_CONTRACT.md)  
**Format assumptions (verify against the official 2027 template when issued):**
CAADRIA full paper, max 10 pages including references, double-blind,
abstract <= 200 words, up to 5 keywords, numbered sections.
Conference theme: *Adaptive Horizons: Designing for Uncertainty and
Transformation.*

Page targets copy contract §7. Nothing here may upgrade a claim beyond the
contract §4 boundary table.

---

## Title

**Committed into Being: Dependency-Scoped Repair in an Obligation-Bearing
Architectural Generation Protocol**

Fallback (if the 22 September executable-repair gate fails):
**Committed into Being: An Obligation-Bearing Protocol for Auditable
Architectural Generation**

`replayable` must not appear in title or contribution statement.

## Abstract (~180 words, in the 1.0-page Introduction budget)

Skeleton, one sentence each:

1. **Problem.** Generative AI inverts the economics of architectural design
   process: decisions diverge with every regeneration instead of converging
   through commitments, while the dependencies that should fan out from each
   decision remain implicit and unqueryable.
2. **Approach.** ArchFlow compiles each accepted design move into a bounded,
   operationally Markovian state carrying typed commitments, obligations, and
   dependency edges with explicit propagation effects, separate from an
   append-only evidence history.
3. **Mechanism.** A local design change triggers a computed invalidation
   closure; repair recomputes only that closure while unaffected accepted
   state is retained under exact-base transitions.
4. **Experiment.** Preregistered comparison over 3 non-isomorphic building
   cases x 3 edit classes x 3 repair strategies (target-only, whole-chain,
   dependency-scoped), against an independently annotated gold impact set.
5. **Result.** `[NUMBERS FROM FROZEN P063 EVIDENCE — retention, recompute
   ratio, validity, attribution; state failures plainly]`
6. **Boundary.** Claims are limited to mechanism transfer across the three
   cases in a neutral-geometry sandbox; no autonomy, CAD-validity, or
   aesthetic claim.

## Keywords

design commitments; dependency-aware repair; generative AI; design process
formalisation; design state provenance

---

## 1. Introduction (1.0 page incl. abstract) — Figure 1

Purpose: state the inversion problem, pose RQ2, list bounded contributions.

- Hook (2 short paragraphs): in conventional practice **decisions converge**
  (commitments accumulate, reopening gets costly) while **dependencies
  diverge** (one move fans out across structure, services, egress, daylight).
  Current generative tools invert both: cheap regeneration destroys
  commitment economics; implicit reasoning destroys dependency queryability.
- One paragraph: therefore generation must be *process-first* — the unit of
  progress is an accepted, dependency-aware state transition, not a terminal
  artifact. Tie to conference theme: adaptation under uncertainty requires
  knowing exactly what a change reopens and what survives it.
- **RQ2 verbatim from contract §3**: for the same accepted local delta, does
  dependency-scoped repair retain more unaffected commitments and recompute
  less state than whole-chain rebuild, without reducing mandatory-validator
  pass rate? (H1 validity, H2 locality, H3 retention, H4 attribution — name
  them here, define in §5.)
- Contributions (exactly the VERIFIED rows of contract §4, phrased as system
  + conceptual contributions; the empirical contribution phrased only after
  results freeze):
  1. an obligation-bearing protocol with exact-base proposal / validation /
     commit / rejection;
  2. typed dependency and invalidation closure as an executable, not
     archival, structure;
  3. dual-track separation of future-facing Markovian state and append-only
     evidence history;
  4. a semantic-component-to-geometry lifecycle under one identity spine;
  5. `[if Stage B completes]` measured repair locality vs. rebuild baselines.
- **Figure 1** here: terminal-output generation vs. process-first generation
  (accepted state, obligations, dependency closure, evidence history).

Forbidden in §1: autonomy claims, "Markovian by nature" phrasing (state the
constructed-approximation position), any number not in frozen evidence.

## 2. Related Work (1.2 pages) — Table 1

Four strands, each closed with the specific gap ArchFlow addresses:

- **2.1 Grammars and procedural generation** (shape/graph grammars, CGA,
  discursive grammars): derivation exists but is typically forward-only;
  revision means re-derivation.
- **2.2 Truth maintenance, provenance, and design rationale** (TMS/ATMS,
  IBIS/DRL, PROV): dependency and justification structures exist but are
  retrospective records, rarely executable against a live design state.
- **2.3 Constrained and editable neural generation** (constraint-aware
  layout nets, Graph2Plan-family, diffusion floor plans, LLM agents with
  tool use): constraints act at sampling time; accepted decisions do not
  persist as first-class commitments across edits.
- **2.4 Versioned/parametric CAD state** (parametric history, BIM
  worksharing, feature dependency graphs): dependency exists inside one
  geometry kernel, but not spanning brief, program, semantics, obligations,
  and validation authority.
- **Table 1**: rows = the four strands + ArchFlow; columns = terminal
  constraint handling / operational derivation / dependency-aware repair /
  evidence ownership. One line of prose per row; no strawmen — credit what
  each strand does own.

## 3. Conceptual Model (1.2 pages) — Figure 2

Purpose: the dual-track state model and the operationally Markovian
construction, independent of implementation.

- **3.1 Three objects, three authorities** — the `C_v` / `D_v,k` / `H<=t`
  table: canonical single-writer baseline; branch-local working state
  sufficient for the next move; append-only history necessary for
  justification. "State is sufficient for action; trace is necessary for
  justification."
- **3.2 The compiled move** — the pipeline: natural-language move ->
  `DecisionOperator` bound to exact `D_v,k` -> delta -> precondition/lock
  checks -> direct effects -> typed dependency/invalidation closure ->
  readiness compilation -> `D_v,k+1`. "The decision is the action, not the
  state."
- **3.3 Convergence machinery** — typed commitments with lifecycle and
  authority; maturity stage gates; explicit branch selection
  (fork/park/reject/select, no automatic winner); single-writer promotion.
- **3.4 Divergence machinery** — dependency edges with explicit propagation
  effects (`invalidates`, `requires_revalidation`, `blocks`,
  `supports_only`); bounded fixed-point closure; the compiler never guesses
  an unnamed dependency; obligations + validators as the fail-closed net for
  undeclared impacts.
- State explicitly: architectural design is **not** naturally Markovian; the
  protocol constructs a bounded operational approximation and keeps the
  residual measurable (links forward to impact recall in §5).
- **Figure 2** here: exact-base transition — proposal, deterministic gate,
  commit/reject, working state, canonical state, evidence history.

## 4. System (1.4 pages) — Figure 3

Purpose: enough implementation to make §5 credible; no module tour.

- **4.1 Proposal–validate–commit loop**: exact-base rejection (state digest
  with branch epoch), sufficient-digest equivalence, fail-closed gates,
  single-writer canonical promotion.
- **4.2 Semantic-to-geometry bridge**: one `DesignComponent` identity spine;
  design decision -> semantic component -> realization specification ->
  family (parametric / authored asset / hybrid) -> geometry program ->
  realization receipt binding component <-> geometry object <-> platform
  object. Screenshots are read-only evidence; execution receipts carry no
  acceptance authority.
- **4.3 Repair executor** (the P063 mechanism): given an accepted delta,
  the three strategies as implemented — target-only patch (no closure),
  whole-chain rebuild, dependency-scoped repair (recompute exactly the typed
  closure). What "retained" means operationally (digest-identical records).
- **4.4 Evidence substrate** (short): P036 single-writer project repository,
  content digests, reloadable records — one paragraph, only what the
  experiment relies on.
- **Figure 3** here: semantic-to-geometry bridge with identity retention
  under revision.

Forbidden in §4: describing quarantined legacy routes, provider internals,
or any capability not exercised by §5.

## 5. Experimental Design (1.2 pages) — Figures 4, 5

Purpose: preregistration-grade method statement; a reader could re-run it.

- **5.1 Stage A — production consistency (P062)**: 3 cases (clinic,
  workshop, courtyard) x 3 conditions (full / generation-context ablation /
  validation ablation); one frozen provider profile; lifecycle states
  including failed/timed-out/unknown; result index reconstructable from
  records; table-ready gate.
- **5.2 Stage B — repair locality (P063)**: unit = one repair episode from
  the same accepted baseline and same frozen delta; 3 cases x 3 edit classes
  (program-relation / component replacement / geometric constraint) x 3
  strategies = 27 paired episodes; delta frozen once and reused across
  strategies so model sampling is not the variable.
- **5.3 Gold impact set**: annotated before execution, second annotator
  preferred, disagreements retained.
- **5.4 Metrics** (define denominators exactly, from contract §6): repair
  success; recompute ratio; commitment retention; impact precision/recall;
  unintended change ratio; failure attribution; cost reported separately.
- Statistics statement: paired descriptive results + intervals where
  meaningful; no population or model-ranking claims.
- **Figure 4**: one delta under three strategies. **Figure 5**: the three
  cases x three edit classes (matched views; caption states visual quality
  is not the evaluation target).

## 6. Results (1.5 pages) — Table 2, Figure/Table 6

Written only from frozen records; drafted after 3 October per contract §10.

- **6.1 Stage A boundary**: completion status of the 3x3 matrix including
  failures, rejections, unknowns; semantic-geometry consistency;
  architectural-usability outcomes; repair/retry counts; reload equivalence;
  wall-clock and model use. If P062 closed partial: the partial sample and
  missing cells shown, not summarised away.
- **6.2 Stage B primary comparison**: **Table 2** with all 27 cells
  (including failures/unknowns); **Figure/Table 6** for validity,
  recomputation, retention, precision/recall, attribution.
- **6.3 Failure taxonomy**: protocol-repair studies disclosed as such;
  where declared dependencies under- or over-predicted the gold impact set
  (feeds RQ3, descriptive only).

## 7. Discussion and Limitations (0.8 pages)

- What the result does and does not show: mechanism transfer across three
  non-isomorphic cases, not architecture-wide performance.
- Validator dependence: validators published, gold set independent;
  false-positive/negative closure behaviour reported.
- Declared-dependency incompleteness as the honest residual; the
  obligation/validator net; future loop from validation failure to new
  declared edges.
- Sandbox/CAD boundary: neutral discrete geometry is the controlled
  substrate; exact B-rep realization is future work, not implied.
- Reviewer attack surface (contract §9) answered implicitly here — each
  bullet in §9 must map to a sentence in §2, §5, or §7.

## 8. Conclusion (0.3 pages)

- Answer RQ1/RQ2 in two sentences with the measured numbers; RQ3 in one
  descriptive sentence.
- Close on the inversion thesis: the model's divergence belongs in
  dependency hypotheses; convergence belongs to the protocol. One sentence
  to the conference theme. No promotional extrapolation.

## References (~1.4 pages)

- Budget roughly 25–30 entries; every §2 strand needs 4–6 anchors.
- Manual verification of every entry (contract §10 AI-use audit); no
  generated citations.

---

## Writing order and gates

| When | What | Depends on |
| --- | --- | --- |
| now | §2, §3 full drafts; Figures 1–3; Table 1; 27-episode preregistration text | nothing empirical |
| after P062 gate (10 Sep) | §5 final; §6.1 draft | table-ready or explicit-partial index |
| after Stage B freeze (3 Oct) | §6.2/6.3; Table 2; Fig/Table 6 | frozen episode records |
| after results fixed (13 Oct) | §1, §7, §8, abstract numbers | frozen §6 |
| 14–20 Oct | adversarial review passes; anonymisation; figure manifests | full draft |

Double-blind notes: no author names, no repository URL, no `ARCHFLOW` if it
identifies the group in prior publications (decide once at anonymisation);
figure manifests kept outside the submitted PDF.

Every figure and table carries a source manifest (record refs + digests) in
this workspace, per README authority rules.
