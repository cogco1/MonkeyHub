# Frozen development-set results

Run: 2026-09-23T06:08:19.364791+00:00 to 2026-09-23T06:08:42.334649+00:00.

All four real CPU methods used the same final corpus/query snapshot. No method was unavailable. The 41 queries and qrels are agent-authored and agent-reviewed development data, not human labels or a blind evaluation. Source omissions were corrected using these questions before the unified run. Earlier pilot scores are not included.

Corpus SHA-256: `8419ab1d95c6032791ae65307500cbf0484ad91588cb62ca0b47b2c7b4498d55`. Query SHA-256: `5fe7daa4a4c64f94dd0db7a92cad64bd06ba1fb67082709e60c7f717e7bd3ce0`.

## Raw ranking and filtered ranking

Top 3; 35 queries with proxy relevance labels. Six known-missing queries have null ranking-quality metrics. Raw still enforces project isolation. Scope errors count wrong requested edition/jurisdiction in ranking slots, not source truth.

| Method | Raw recall | Raw nDCG | Raw wrong-scope slots | Filtered recall | Filtered nDCG | Filtered mean ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| lexical | 0.763 | 0.681 | 38/119 (31.9%) | 0.831 | 0.829 | 0.20 |
| dense | 0.828 | 0.804 | 41/119 (34.5%) | 0.894 | 0.925 | 5.20 |
| hybrid | 0.772 | 0.714 | 42/119 (35.3%) | 0.880 | 0.899 | 5.73 |
| hybrid_reranker | 0.828 | 0.809 | 36/119 (30.3%) | 0.862 | 0.908 | 82.53 |

All filtered policies returned zero wrong-scope fragments. All four policies had zero cross-project context leakage on this fixture. Exact numeric-principal hits after filtering were 7/9 lexical, 7/9 dense, 8/9 hybrid and 8/9 reranker; these locate a paragraph, not a correct numerical answer.

## Per-corpus reranking comparison

Filtered top-3 rankings, before companion expansion:

| Corpus | Queries / label-bearing | Hybrid recall / nDCG | Reranker recall / nDCG |
| --- | ---: | ---: | ---: |
| regulation | 12 / 10 | 0.780 / 0.839 | 0.717 / 0.832 |
| precedent | 13 / 12 | 0.875 / 0.872 | 0.903 / 0.924 |
| project | 8 / 6 | 1.000 / 1.000 | 1.000 / 1.000 |
| technical | 8 / 7 | 0.929 / 0.945 | 0.881 / 0.911 |

Reranking modestly improves the precedent proxy ranking here but reduces regulatory and technical recall. No significance or population inference is justified. Do not choose a universal reranker from this set. Provisional next candidates for a **new** human-reviewed comparison: hybrid for regulation, dense for precedent/technical, and metadata+lexical for tiny project notes. This is a research direction, not a production default.

## Emitted evidence, conditions and budget refusal

The following uses the actual emitted JSON context. Complete means all proxy-labeled fragments are present, not full legal/architectural sufficiency. Companion recall averages 20 targeted queries. Refusal emits zero evidence bytes and must not be treated as successful compression.

| Method | Filtered companion recall | Expanded companion recall | Expanded complete / 35 | Expanded mean bytes | Expanded mean ms | Missing-companion refusals |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| lexical | 0.662 | 0.650 | 24/35 | 2231 | 0.20 | 8 |
| dense | 0.787 | 0.800 | 28/35 | 2303 | 5.15 | 7 |
| hybrid | 0.738 | 0.750 | 27/35 | 2399 | 5.56 | 7 |
| hybrid_reranker | 0.713 | 0.700 | 26/35 | 2220 | 82.91 | 8 |

At 12 KiB there were no byte-budget refusals; the absent Table 405.2 / section 304.3.2 caused 7–8 missing-companion refusals per method. Since the whole top-3 bundle is closed and then refused, an irrelevant incomplete neighbor can also prevent delivery. The strict top-3 closure is deliberately conservative and not an optimized smallest-sufficient-set selector. Expansion gains/losses belong to this post-processing policy, not to dense embeddings or the cross-encoder alone.

At 1 KiB, lexical/reranker had 26 budget refusals and dense/hybrid 27, in addition to the missing-companion and five no-candidate refusals. Only two small project responses fit; companion recall was zero. Refusals retain exact reopen refs and do not emit shortened clauses.

Metadata absence caused justified no-candidate refusal for five of six known-missing queries. The remaining historical-intent query has matching metadata and related prose; all methods return **unverified**, not a successful semantic abstention. This is an explicit unresolved retrieval limitation. Budget refusal on that query in the 1 KiB condition is not evidence that semantic absence was detected.

Exact duplicate text bytes were zero after filtering; this is only a byte-exact duplicate metric. Per-query irrelevant bytes, principal hits, ranking scores, context ids, missing refs and statuses remain in [results.json](results.json). The corpus digest plus item ids resolve every delivered/refused source via [corpus.json](corpus.json).

## Every top-one proxy false positive

There are 101 method/policy occurrences. They are grouped below by query and returned fragment so repeated policy runs remain readable; every original occurrence and score is retained in results.json. Scores have no calibrated confidence meaning. “Mismatch” means disagreement with this development label, not proof that the passage is useless.

| Query | Top fragment | Inspection |
| --- | --- | --- |
| reg-walk | old-walk | Wrong edition/jurisdiction; deterministic metadata filter removes it. |
| reg-pinch | ada-door-exception-1 | 32-inch wording retrieves the door latch-stop exception instead of walking-surface limits. |
| reg-door | aba-door | Wrong edition/jurisdiction; deterministic metadata filter removes it. |
| reg-deep | aba-door | Wrong edition/jurisdiction; deterministic metadata filter removes it. |
| reg-ramp | old-ramp | Wrong edition/jurisdiction; deterministic metadata filter removes it. |
| reg-jurisdiction-missing | ada-turn-exception | No matching requested scope/version exists; raw-ranking match disappears under metadata filtering. |
| reg-version-missing | old-ramp | No matching requested scope/version exists; raw-ranking match disappears under metadata filtering. |
| pre-original | study-manual | Manual-tracing status replaces the requested method consequence. |
| pre-unknown | study-changed-context | Changed use overlaps the topic; label audit should decide whether it is auxiliary evidence for the requested gap. |
| pre-prior | study-hypotheses | Hypotheses are related and may be useful auxiliary evidence, but the labeled transfer rule is elsewhere; human qrel review remains needed. |
| pre-counterexample | study-method | Generic method advice replaces the actual changed-context counterexample. |
| project-width | alpha-r1-brief | Wrong edition/jurisdiction; deterministic metadata filter removes it. |
| project-entrance | alpha-r1-decision | Wrong edition/jurisdiction; deterministic metadata filter removes it. |
| project-superseded | alpha-r1-decision | Wrong edition/jurisdiction; deterministic metadata filter removes it. |
| project-dimension | alpha-r1-brief | Wrong edition/jurisdiction; deterministic metadata filter removes it. |
| project-old | alpha-r2-decision | Wrong edition/jurisdiction; deterministic metadata filter removes it. |
| project-revision-missing | alpha-r2-decision | No matching requested scope/version exists; raw-ranking match disappears under metadata filtering. |
| tech-glob | py3.11-glob | Wrong edition/jurisdiction; deterministic metadata filter removes it. |
| tech-isdir | py3.11-is-dir | Wrong edition/jurisdiction; deterministic metadata filter removes it. |
| tech-absent-version | py3.12-relative-condition | No matching requested scope/version exists; raw-ranking match disappears under metadata filtering. |
| tech-glob-return | py3.11-glob | Wrong edition/jurisdiction; deterministic metadata filter removes it. |
| pre-missing-intent | study-geometry | Related prose cannot establish historical author intent; semantic missing evidence remains unresolved. |
| reg-walk | ada-definition | Walking-surface wording retrieves the ramp definition; it does not answer route width. |
| reg-walk | aba-walk | Wrong edition/jurisdiction; deterministic metadata filter removes it. |
| reg-deep | old-door | Wrong edition/jurisdiction; deterministic metadata filter removes it. |
| reg-jurisdiction-missing | aba-door | No matching requested scope/version exists; raw-ranking match disappears under metadata filtering. |
| reg-version-missing | aba-ramp | No matching requested scope/version exists; raw-ranking match disappears under metadata filtering. |
| tech-absent-version | py3.11-glob | No matching requested scope/version exists; raw-ranking match disappears under metadata filtering. |
| pre-missing-intent | study-method | Related prose cannot establish historical author intent; semantic missing evidence remains unresolved. |
| reg-jurisdiction-missing | old-walk | No matching requested scope/version exists; raw-ranking match disappears under metadata filtering. |
| pre-missing-intent | study-intervention | Related prose cannot establish historical author intent; semantic missing evidence remains unresolved. |
| tech-error | py3.11-relative | Wrong edition/jurisdiction; deterministic metadata filter removes it. |

## Timing, cost and boundaries

Environment: Python 3.12.10, Windows-11-10.0.26200-SP0, Intel64 Family 6 Model 183 Stepping 1, GenuineIntel; CPU, 4 Torch threads. Dense load/index took 6.996s and reranker load 0.029s with downloaded model files already cached.

Each query/policy was timed once in a fixed method order. Concurrent local development, OCCT and provider experiments were not controlled or measured. Do not turn these samples into a controlled latency claim. External inference requests/cost are zero. Hardware amortization, electricity and download/network costs were not measured.

Extraction and generation remain separate: the corpus is partial source extraction, not a scored extraction system, and no downstream answer was generated. Correctness, condition retention, unsupported strengthening and contradiction handling through generation remain null. The fixed-evidence downstream comparison, held-out human annotation, a diverse historical-precedent set and chunking alternatives remain open in #122.

Verification: focused regression tests, real four-method CPU run, genuine missing-dependency fallback smoke, and repository archcheck. No application, project store, installation or formal acceptance was changed.
