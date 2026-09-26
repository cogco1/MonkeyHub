# GH-122 retrieval development baseline

This lab compares lexical, dense, hybrid and hybrid+reranker over **one frozen
development set**: 54 fragments, 41 agent-authored queries and proxy relevance
judgments. It locates evidence; it cannot accept a fact, change a design, issue a
project, validate a regulation or recover an architect's intent. See
[RESULTS.md](RESULTS.md) for the measured outcome and remaining failures.

## What is actually being compared

| Baseline | Fixed configuration |
| --- | --- |
| lexical | BM25, positive Robertson/Lucene IDF, k1=1.5, b=.75; English word/numeric-section tokenizer; unique query terms |
| dense | `sentence-transformers/all-MiniLM-L6-v2`, normalized vectors, cosine score, CPU |
| hybrid | reciprocal-rank fusion of lexical+dense, k=60, over the eligible corpus |
| hybrid_reranker | hybrid's top 10, `cross-encoder/ms-marco-MiniLM-L6-v2`, final top 3 |

Model revisions are fixed in [retrieval.py](retrieval.py) and retained in
[results.json](results.json). They are small English models, not a claim to
state-of-the-art, multilingual or architectural reasoning performance. Overlong
model input is refused instead of silently truncated. No model training,
embedding service, vector database or new agent runtime is involved.

Every baseline runs the same four policies over the same query/corpus snapshot:

1. `isolated`: corpus/project isolation, no requested version/jurisdiction filter,
   isolated top-3 fragments. Deliberately unsafe comparison, never a consumer path.
2. `filtered`: requested version/jurisdiction filters **before** ranking, isolated
   top-3 fragments. BM25 statistics are recomputed over that eligible set.
3. `companions`: the same filtered ranking plus recursive declared companion links,
   under a 12,288-byte evidence-content budget.
4. `companions_1024`: the same operation with 1,024 bytes, to expose refusal costs.

All policies enforce project equality before scoring, including the unsafe
version ablation. An explicit project is mandatory for project-corpus requests.
Expansion cannot import another project, edition or jurisdiction. Qualification
fragments link back to the principal they qualify, so an exception-first hit
cannot skip its missing table. Cycles are visited once. Missing companions or
budget overflow reject the entire context; exact available fragment/source plus
corpus, project, jurisdiction and version identity remains in `reopen`. A missing
companion is also tied to the fragment that required it and that fragment's
declared link basis, rather than being reported as an unexplained absent id.
Ranking scores are not probabilities or truth confidence.

`unverified` means only that the selected declared bundle fits. It does **not**
mean the source extraction is complete or that the query can be answered. The
semantic missing-evidence example demonstrates this limit. No similarity
threshold is presented as a general abstention detector.

## Corpus and labels

| Corpus | Frozen input and boundary |
| --- | --- |
| regulation | Official 2010 ADA excerpts, 1991 ADA near-matches and federal ABA near-matches. Source section, normalized character offsets, response/excerpt SHA-256 and URL are retained. Jurisdiction labels mean the instrument's scope, not a determination of legal applicability. Table 405.2 and section 304.3.2 are deliberately absent and produce explicit missing companions. |
| precedent | Twelve source excerpts from the existing [Study method note](../../docs/research/study-evidence-method.md), pinned to `27e7f2f`, including competing explanations, conditions, altered use and measured counterprediction. These concern **one synthetic precedent**, not twelve independent historical buildings or a new historical-source corpus. |
| project | Six clearly synthetic alpha/beta project/revision notes, authored for this experiment. They are not actual user decisions, accepted StateRecords or P036 records. The geometry theme follows the existing courtyard experiment, but these text fixtures do not reproduce its project lifecycle. |
| technical | Exact Python 3.11/3.12 pathlib paragraphs with adjacent qualifications, broken-symlink behavior and the `relative_to` symlink warning. “Current” means the query's requested 3.12 target, not latest Python. |

The assigned base had no GH-122 retrieval implementation or query file. Study
questions adapt its existing fixture question/research note; other queries were
written from these selected sources. Every query and label says **agent-authored**.
The same questions helped repair omissions during development, and an independent
agent checked the implementation and selected source spans. This is neither a
human-marked dataset, independent blind evaluation nor a held-out test set. No
ranking parameter or model was tuned after observing the scores. The final run
reran all four methods after the source/label corrections; pilot scores are not
mixed into it.

The local frozen excerpts reopen without network via item id in `corpus.json`.
Study refs additionally reopen at the exact Git commit/line span. External HTML
URLs are mutable: response SHA and normalized offsets bind the inspected
response, but that complete response is not archived here. A future URL with a
different response hash must not be presented as the exact old document. No PDF
page number is invented for HTML. Source extraction coverage is partial and its
accuracy is not measured by a ranking score.

Regulatory companion links follow explicit section references/adjacent
exceptions. Study/project links are labeled **agent-proposed**, with no claim to
historical causation or human endorsement. Query judgments never enter ranking,
filtering or expansion.

## Metrics and interpretation

`recall`, `precision`, binary `ndcg` and `mrr` use the original **top 3 ranking**,
not expanded context. Precision uses denominator 3 even for fewer candidates.
`principal_hit` locates one labeled principal; `numeric_principal_hit` restricts
that same exact-fragment hit measure to nine numeric queries. Neither checks a
generated numerical answer.

`companion_recall` and `complete_evidence` use the actual **emitted context**.
The latter means coverage of all proxy-labeled relevant fragments, not complete
regulatory or architectural evidence. Ranking recall can stay high while both
context measures fall to zero on a justified missing-companion/budget refusal.
Low bytes from refusal are not a compression success. Budgets count UTF-8 JSON
text, source identities and companion metadata together, excluding the refusal
envelope/reopen refs. No text is shortened to pass a budget.

Missing-label queries are excluded from ranking-quality means (null, not zero).
`missing_abstention` is the refusal fraction on those queries; its per-row reason
must be read separately. A byte-budget refusal is not detection of semantic
absence. Scope mismatch counts and their ranking denominators expose stale/wrong
jurisdiction false-positive rates. Project leakage counts measure this finite
fixture only. Duplicate bytes count exact repeated text; semantic redundancy is
unmeasured. Irrelevant bytes are relative to proxy labels.

Latency includes filtering, query embedding, ranking/reranking, expansion and
serialization; dense corpus indexing/model loading is recorded separately.
There is one observation per query/policy, fixed execution order, warm downloaded
models, CPU/4 Torch threads, and uncontrolled concurrent local development,
OCCT and provider load. These are smoke measurements, not controlled latency
estimates. Local inference has zero external API charges; energy, hardware and
network costs remain unknown. Full UTC start/end, package versions and CPU
identifier are recorded. No generation was run: downstream correctness,
condition retention, strengthening and contradiction errors are **null**. Do not
attribute those outcomes to these retrieval measurements.

## Run it without touching the application

From the repository root, use a disposable external virtual environment and
explicit external model cache/output paths, for example in PowerShell:

```powershell
python -m venv "$env:TEMP/gh122-repro"
& "$env:TEMP/gh122-repro/Scripts/python.exe" -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
& "$env:TEMP/gh122-repro/Scripts/python.exe" -m pip install -r labs/retrieval/requirements.txt
& "$env:TEMP/gh122-repro/Scripts/python.exe" -m labs.retrieval.benchmark --cache-dir "$env:TEMP/gh122-models" --output "$env:TEMP/gh122-result.json"
python -m pytest labs/retrieval/test_retrieval.py -q
python tools/archcheck.py
```

`--lexical-only` needs no model libraries. A real missing-model dependency records
the three affected methods as unavailable and still runs lexical; it does not
substitute a mock embedding/reranker. A model runtime failure discards that
method's partial measurements. Model downloads require network access; fixture
reading and the lexical baseline are offline. Nothing launches or modifies the
user's Hub, projects or CAD session.

## Existing owners and reusable identity

The inspected `studio.shell` `RetrievalProvider.retrieve(query, stage, limit)` is
a reserved protocol with no callers/implementation. This lab's replaceable
`EvidenceRanker.rank` is consumed only by its CLI and is **not** connected to the
reserved production port. Production source/ledger reads belong to
`studio.study`/`studio.artifacts`; persistence/ref identity belongs to P036.

An integration can carry existing `StudySource.basis_ref`, document asset SHA,
page/revision, exact `ProjectRecordRef.uri`, ledger ref, evidence id and separate
project/run binding through an EvidenceItem's source mapping **unchanged**.
Their owners must still resolve/verify them. This experiment has no retained
Study or project store and does not mint look-alike canonical URIs. The Git
source refs, external source identities and opaque original evidence refs are
reusable. Fragment/query ids, proxy labels, alpha/beta revisions, scores, models,
links and result summaries belong only to this experiment. They are not new
shared canonical types. ContextPack/Study/Hub wiring remains with PR #245.

## Primary research and implementation checks

Checked 2026-09-23; method choices, not imported performance claims:

- [BEIR, Thakur et al., 2021, v4](https://arxiv.org/abs/2104.08663v4): heterogeneous
  corpora motivate reporting per corpus and comparing lexical with neural
  methods. Its performance conclusions do not establish results for these 41 queries.
- [Cormack, Clarke and Buettcher, RRF, SIGIR 2009](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf):
  rank fusion uses `sum(1 / (60 + rank))`; fusion avoids calibrating heterogeneous scores.
- [Lucene 9.12.0 BM25Similarity](https://github.com/apache/lucene/blob/releases/lucene/9.12.0/lucene/core/src/java/org/apache/lucene/search/similarities/BM25Similarity.java):
  inspected positive IDF. This small Python implementation is not Lucene's analyzer,
  index, default k1, norm encoding or performance implementation.
- [Sentence Transformers retrieve/rerank](https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html)
  and [v3.4.1 CrossEncoder](https://github.com/huggingface/sentence-transformers/blob/v3.4.1/sentence_transformers/cross_encoder/CrossEncoder.py):
  independent embedding candidates followed by joint query/passage scoring; API/cache
  behavior was checked against the installed version and real CPU calls.
- [MiniLM embedding model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
  and [MS MARCO reranker card](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2):
  checked model purpose, license and pinned revisions. Both are Apache-2.0 models.

Source rights: ADA/ABA excerpts are United States federal publications; source
URLs and section identities are embedded with every fragment. Python documentation
is copyright Python Software Foundation and is redistributed under its
[PSF License Agreement](https://docs.python.org/3/license.html); the snippets
normalize whitespace and select paragraphs, without rewriting their statements.
The Study excerpt uses this repository's source/license. No publisher drawings or
full research papers are redistributed.
