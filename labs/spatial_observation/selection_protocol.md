# #170 controlled selection protocol

`selection.py` is a read-only experiment over `Fixture.snapshot()`. It does not
introduce a persistent index, a canonical graph, a writer, or an acceptance path.
The source is the same synthetic StateRecord/retained STEP used by #195. The
snapshot contains 12 solids and their ground datum, CAD Z-up coordinates and
metres, stable entity refs, recorded relations, and StateRecord dependency edges.
Content digest, bound state digest, revision and retained STEP identity remain
attached when supplied. A consumer calls `require_current` before using a result.

The falsifiable hypothesis is that text embeddings plus explicit graph expansion
improve held-out relevant-entity/edge recall over simple selection at a matched
entity budget. This experiment does not claim to train or test a spatial encoder.
The separate #195 representation comparison must not be pooled with these rows.

## Fixed inputs and methods

Each method receives identical `Query(text, target_refs, operation)` inputs and
the same structured source revision. Explicit target refs are retained first.
The selected entities/edges retain their original fields; algorithms never add
similarity edges. The method cannot read `audited_tasks()` or its gold labels.
Returned `context` is the model input. Gold, ranking scores, evaluation metrics,
and closure-audit diagnostics are outside that context. Tell a downstream model
that selected contexts are incomplete and absence is not proof of no dependency.

| Method | Mechanism frozen before held-out inference | Scope/limitation |
| --- | --- | --- |
| full | Every entity and recorded edge | Full-context coverage/cost reference, intentionally not truncated to selective top-k |
| graph | Directed breadth-first downstream closure for impact; one-hop recorded neighbors for inspection; declared producer + parameter-key signature for similarity | Requires named targets; no text entity resolver; signature is deliberately a simple rule, not actual shape equivalence |
| lexical | BM25-style term scoring, k1=1.2, b=0.75, on deterministically serialized entity fields and incident edges | No stemming, learned synonym expansion, or inferred relation; stable-id order breaks ties |
| embedding_graph | Cosine ranking from real MiniLM vectors on the same text; half remaining slots seed retrieval, then recorded neighbor/downstream expansion, then remaining ranked candidates | Retrieval seeds can crowd out required dependencies; full declared closure is audited separately and cannot replace validation |

Selective methods use six entities in the primary comparison. They may return
fewer if their rule finds fewer. Full context contains 13. Compare actual context
bytes and coverage, not an unsupported claim of token-equivalent budgets. Four-
entity and 12-entity runs are separate budget sensitivity checks; a five-entity
closure cannot fit four slots regardless of ranking. Do not use that arithmetic
constraint as evidence against a particular retrieval method.

`audited_tasks()` contains three development queries and five held-out queries
on an edited revision. The gold is explicit authored synthetic relevance, open
to independent review; it is not external human approval or a real-building
relevance study. The queries and hybrid allocation were frozen before the first
held-out inference. No parameter search is performed after observing outcomes.

| Split/task | Independently checkable relevant evidence |
| --- | --- |
| Development screen impact | screen → lintel → seal → fixing → drain, including all four declared edges |
| Development marker B impact | twin-b → drain |
| Development explicit unknown | mystery entity with `role=null` |
| Held-out head member impact | lintel → seal → fixing → drain |
| Held-out marker A impact | twin-a only; its similar-looking peer has a different declared dependency |
| Held-out shell suspicion | u-shell and insert; collision suspicion requires retained-STEP solid query |
| Held-out unknown search | mystery by metadata description, without a supplied id |
| Held-out geometric versus causal | twin-a, twin-b, drain and twin-b's declared drain edge |

The far-end drain is a predeclared critical sentinel on impact cases. Every
other missed dependency is also listed by `missing_declared_dependency_refs`;
an empty sentinel list is not complete impact coverage. The rule baseline can
miss the shell/insert pair because the record has no relationship for it. That
is a useful missing-model relation, not permission to fabricate a dependency.

## Actual encoder and implementation evidence

The experiment uses `sentence-transformers/all-MiniLM-L6-v2`, a 384-dimensional
English text encoder, with no task-specific training. Its pretrained contrastive
text objective makes it a reasonable small metadata-retrieval control; it has
no geometric tensor protocol and is unsuitable for authoritative distances or
causal closure. The official [model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
documents the training and Apache-2.0 license. The original
[MiniLM paper](https://arxiv.org/abs/2002.10957) explains the distilled transformer
mechanism; this experiment reuses pretrained weights rather than reproducing
that training.

Runtime is FastEmbed 0.7.3 with ONNX Runtime 1.30.0, CPU provider, one thread.
FastEmbed's actual [pooled implementation](https://github.com/qdrant/fastembed/blob/v0.7.3/fastembed/text/pooled_normalized_embedding.py)
applies attention-mask mean pooling and normalization. Both weights and library
are Apache-2.0; see the [library license](https://github.com/qdrant/fastembed/blob/v0.7.3/LICENSE).
No Qdrant server or persistent vector database is used: 13 float32 vectors are
small enough for an exact in-process cosine scan.

The [Qdrant ONNX port](https://huggingface.co/qdrant/all-MiniLM-L6-v2-onnx/tree/5f1b8cd78bc4fb444dd171e59b18f3a3af89a079)
is pinned to `5f1b8cd78bc4fb444dd171e59b18f3a3af89a079`.
`model.onnx` is 90,387,630 bytes and its SHA-256 is
`bbd7b466f6d58e646fdc2bd5fd67b2f5e93c0b687011bd4548c420f7bd46f0c5`;
the loader verifies that actual weight artifact before inference. The port's
`tokenizer_config.json` has `max_length=128` and `model_max_length=512`.
FastEmbed's [preprocessor](https://github.com/qdrant/fastembed/blob/v0.7.3/fastembed/common/preprocessor_utils.py)
uses the smaller limit. Thus this execution really truncates at **128 tokens**,
despite the original model card's default 256 claim. The benchmark reports every
untruncated document length and affected entity. This is an implementation/input
limitation to retain with any negative result, not evidence against all encoders.

## Running and accounting

Install `fastembed==0.7.3` in a disposable environment; keep model downloads in an
explicit external cache. `huggingface_hub.snapshot_download` accepts the repository
and pinned revision above. Pass that downloaded snapshot directory to
`MiniLMEncoder(model_path)`. Nothing sends project data to an embedding service.
The repository's normal environment need not gain these optional dependencies.

```python
from labs.spatial_observation.selection import MiniLMEncoder, run_benchmark

encoder = MiniLMEncoder(model_path)
report = run_benchmark(
    {"base": base_fixture.snapshot(), "heldout": heldout_fixture.snapshot()},
    encoder=encoder,
    budget=6,
)
```

The caller owns the output destination. Persist reviewed experiment evidence in
the existing probe; do not save caches/model weights there. Index, full-rebuild
update, query latency, context bytes and vector bytes are separate. Model load
and weights are one shared `encoder_once` entry, not charged once per index.
Download/preparation time is external and must not be silently counted as zero.
Local inference records zero API calls/charges; electricity and hardware cost
are unmeasured. Downstream model usage and exact queries are separate additions,
not zero-cost work. Diagnostics have separately reported verification time.

`run_benchmark` measures retrieval only. A downstream experiment must keep model,
prompt, tools and settings fixed across `row['result']['context']` inputs and
must not include the row's gold. API-equivalent estimates must remain separate
from actual subscription/account charges.

The benchmark rejects a stale result and stale index, rebuilds against the edited
source, verifies changed screen content is present, and reverses input array
ordering to test invariance. Content changes with the edit even when correct
dependency rankings remain unchanged. Tests additionally check that input and
accepted-source data cannot be changed by mutating a returned context.

The collision hypothesis is checked using `heldout_fixture.exact_query('pair',
{'first': 'u-shell', 'second': 'insert'}, source=heldout_fixture.source)` only when
the selected context supports that pair. This calls the existing retained-STEP
OCCT solid measurement. It is not a bounding-box distance or an embedding claim.

Initial real execution found the held-out pair separated by 0.25 m with zero
common volume, refuting collision. At budget six the graph method retained the
screen's complete five-entity closure, lexical selection missed seal/fixing, and
the hybrid missed drain after retrieval seeds consumed slots. These observations
are retained as negative results; final numbers belong to the reproducible probe
run. Unknown-role discovery worked for both text methods. The simple graph rule
cannot discover an unnamed unknown object. No result here proves general
architectural ontology robustness or accuracy on another building.

## Separate ontology-drift stress case

After freezing the primary 96 retrieval rows, a separate `role-drift` fixture
changes only `mystery`'s recorded role from `null` to the already registered
`role.structural_support`. Its label remains `unclassified object`; all authored
geometry parameters and declared edges remain the same. P036 retains a distinct
source binding and the CAD adapter verifies the retained STEP. This is a
deliberately conflicting label/role example, not a new held-out success or a
claim that the assigned role is architecturally correct.

Every frozen selector receives the same query:
`Query('Inspect dimensions and recorded role of this unclassified object.',
('mystery',))`, with the original six-entity budget. The separate
`ontology_drift.json` records base/drift contexts, source roles, selected order
and ranking-score changes, entity metrics against the same `mystery` target,
stale-result/index rejection, real full-rebuild time, and a source-bound exact
state readback. A descriptive label must not rewrite the role back to `null`.
Explicitly supplying the target tests source fidelity and freshness; it does
not establish autonomous semantic classification or text-only discovery.

The stress case does not change the selector implementation, frozen task set,
primary results, model/tokenizer settings or hybrid allocation. Its results
remain outside the primary held-out aggregates, and no additional downstream
model judgment is inferred from these context checks.
