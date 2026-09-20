# GH-170 revision evidence comparison

This continues PR #198 at `72f424800a4eadf71604648d2d93f8163f22b10e`.
It does not rerun #195's representation comparison or replace production
ContextPack. `SelectionIndex.select_revision` is an opt-in lab interface consumed
by `revision_benchmark.prompt_blocks` and the existing restricted Claude consumer.

## Mechanism and limits

The original pilot allowed retrieval seeds to crowd out drainage evidence.
The revision interface reserves evidence before filling optional ranking slots:

1. Check the complete supplied source binding, including STEP/content identities.
2. Follow only `invalidates` and `requires_revalidation` downstream effects,
   matching `StateRecord.closure`. The lab snapshot is still an entity-only
   projection, not a general adapter for parameter nodes or production ContextPack.
3. For `change`, traverse upstream from **all** affected objects and supplied
   condition targets. Never descend again from a common upstream datum to siblings.
4. Preserve supplied conditions verbatim. The selector does not solve them.
5. If required evidence exceeds the budget, effects are unavailable, or the
   request is open-ended (`inspect`, `similar`, no target), return full context.
   A named ID absent from this source is an error, not an entity to invent.
6. Required images must include valid PNG bytes bound to the current revision,
   state and retained STEP. Missing/stale images stop before model invocation;
   a larger text context cannot establish a visual check.

The four methods share this guard. Raw v1 rankings and their omissions are
retained separately. Full uses all 34 entities. Graph/rule, BM25 and pinned
MiniLM-plus-graph fill up to eight entities after required evidence. Required
evidence may itself exceed eight and trigger full fallback. Extra text relevance
is not a proof of sufficient architecture knowledge. No undeclared dependency,
physical clearance, compliance, edit acceptance or full building impact is inferred.

Borrowed mechanisms are deliberately narrow. [ConceptGraphs](https://arxiv.org/html/2309.16650v1)
uses compact object nodes and relation-based reasoning; its [scene-graph code](https://github.com/concept-graphs/concept-graphs/blob/93277a02bd89171f8121e84203121cf7af9ebb5d/conceptgraph/scenegraph/build_scenegraph_cfslam.py)
selects candidate relations from overlap/MST before model inference. That pruning
cannot recover a distant declared drainage dependency. We reuse object-level
retrieval, retaining CAD-declared direction and effect instead of inferred links.
The [MiniLM model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
describes text similarity vectors, not exact geometry or causal evidence. The
same pinned ONNX weights and actual 128-token truncation from the prior pilot
are retained and reported. No new embedding service or training is introduced.

## Truth and split frozen before held-out measurement

An independent read-only reviewer inspected the original fixture, real
`StateRecord.dependency_edges/closure`, and proposed the split below. This is
reviewed authored synthetic relevance, not an unseen building or an external
architect's acceptance. The implementation author knows these explicit labels;
the selectors and model prompts never receive them. No held-out tuning is allowed.

The new fixture extends the original public geometry with
`roof-post → roof-beam → canopy → gutter → outlet` and 16 separate facade panels.
The outlet is remote in XY; all 33 solids reference ground. The held-out revision
changes canopy thickness, beam elevation and outlet position, in addition to the
old held-out changes. Both are retained using the existing Fixture/P036/OCCT path.

`C = {ground, screen, lintel, seal, fixing, drain, twin-b}`.
`R = {ground, roof-post, roof-beam, canopy, gutter, outlet}`.
All required members are critical in this experiment. Explicitly listed gold
sets are not computed using the selector. Gold edges are the source dependency
edges whose endpoints both occur in that authored set; edges retain their
original direction, kind and effect. Datum refs are `entity:<child>`, while
explicit relation refs are `relation:<a>-to-<b>`.

| Split | Request | Required answer evidence / expected behavior |
| --- | --- | --- |
| D1 | Widen screen | C; preserve remote drain and second upstream branch |
| D2 | Resize mystery | ground, mystery; role remains null |
| D3 | Change canopy, budget 4 | R; full fallback |
| D4 | Increase screen height but preserve height=3 | C; preserve condition, consumer reports conflict |
| H1 | Raise roof-post | R; remote outlet retained |
| H2 | Deepen gutter | R; all upstream supports retained |
| H3 | Move twin-b | C; drain's other upstream branch retained |
| H4 | Resize fixing | C; fixing remains unknown-role |
| H5 | Find label=unclassified object | Full fallback; answer mystery only |
| H6 | Compare twin-a/twin-b drainage impact | Full fallback; answer twins and drain; no invented A edge |
| H7 | Thicken canopy from .25 to .45, keep .25 | R; conflict must be identified |
| H8 | Raise shared ground | All 34 entities; full fallback |
| H9 | Canopy/beam revision with front image | R + current PNG + proposed exact pair |
| H10 | Insert/shell revision with top image | ground, insert, shell + current PNG + proposed exact pair |

Development checks fixed the eight-entity budget and conservative guard without
fitting a relevance threshold. Four-entity D3 explicitly tests budget overflow.
H9/H10 test actual multimodal delivery and proposed exact-check references only;
they have no independently frozen pixel-level answer, so **visual reasoning
accuracy is not measured**. The model must not claim an exact measurement or an
executed revision. None of these evidence plans performs the proposed CAD edit.

## Measurement and reproduction

```console
python -m labs.spatial_observation.revision_benchmark --phase development --output <new-external-development-root> --encoder <pinned-model-cache>
python -m labs.spatial_observation.revision_benchmark --phase heldout --output <new-external-heldout-root> --encoder <pinned-model-cache> --claude <existing-cli> --repetitions 2
```

The held-out run makes 80 real calls: ten tasks × four methods × two repetitions.
Calls are sequential with deterministic shuffled order (seed 170), no hidden
retries, fixed model alias/effort/output limit/system policy and actual image
bytes. CLI transport follows the [official headless guide](https://code.claude.com/docs/en/headless).
The requested alias is mutable; actual returned model identities are retained.
Provider prompts contain the same task facts and actual selected context, with no
method labels, gold, omitted-ID diagnostics or ranking scores.

The strict evidence-plan score requires all applicable items: exact relevant
entity and dependency reference sets, support in the supplied context, all
conditions, unknown roles, required image references, proposed exact pairs and
correct conflict decision. Syntax and transport failures remain failures. This
is a narrow completion condition, not proof of architectural revision quality.

All gate refusals (stale index/result, missing/stale PNG) are separately timed,
with zero provider calls. A full rebuild is measured before the edited-source
queries. No incremental index implementation or free update is claimed. Index,
encoder load, model download/preparation, fixture creation, rendering, retrieval,
serialization and complete CLI waits are separate. Query time includes the raw
attempt and fallback; full fallback input/cost stays in the primary aggregate.

All returned models and disjoint ordinary/cache read/cache write tokens count.
Missing usage remains unknown. CLI API-equivalent USD is not an actual account
charge; electricity, hardware cost and subscription quota consumption are unknown.
Report raw coverage, guarded coverage and consumer correctness alongside cost.
If graph/rule is as accurate and cheaper, do not attribute the guard's gain to
embeddings. Old #198 failures and results remain unchanged.
