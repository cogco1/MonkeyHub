# Virtual geometry observation pilot — GH-170

This experiment lets an agent inspect an **existing virtual model**: choose a
local neighborhood, retain unknown objects and declared dependencies, ask the
existing OCCT backend to measure a suspected gap/collision, and revisit a changed
candidate. It supplies observations for an interleaved Proposer–Critic experiment
(GH-173); it is not an autonomous architectural judge.

`observation.py` consumes named `StepEntry` geometry and its exact project/run/
record binding. `Scene.select` offers explicit-id and structured lexical seeds,
optional bounds proximity, and dependency closure. Lexical seeds require all
supplied terms, rather than treating a shared id prefix as a match by itself.
A truncated selection lists
its omitted objects and unresolved references. No annotation is required: an
unclassified shape remains visible. Caller roles and descriptions are hypotheses,
separate from measured geometry. Bounds overlap is a selection hint, never proof
of collision, bearing, room membership or a traversable route.

`exact_pairs` delegates true minimum distance and common volume to
`adapters.cad_execution`'s existing OCCT implementation. It refuses another
revision's binding. The caller must obtain that binding from the actual retained
candidate; this in-memory module cannot notice an external edit on its own.
`changes` reports changed bounds and annotations. Equal
bounds cannot establish unchanged geometry: callers can supply existing compiler
object identities; otherwise the geometric change remains unknown.

`segment_hits` uses OCCT's finite line/face intersections for cross-storey sight
lines, retaining openings rather than using box occlusion. It reports occupied
endpoints separately: an empty intersection list does not establish a valid
viewpoint. Glass is ignored only when the caller explicitly names it. Failures
remain unavailable; this is geometric visibility, not illumination or comfort.

The caller owns source loading, views/sections and output destinations. This
module writes nothing, retains no new record, calls no model, accepts no design,
and is not imported by production code. Active building data and pilot results
stay in their external project workspace. This first slice deliberately exposes
selection omissions; it does not claim that recorded dependencies are complete.

Run with the repository CAD dependency environment:

```console
python -m unittest labs.spatial_observation.test_observation
python tools/archcheck.py
```

## Research choice and criticism

- [RieMind](https://arxiv.org/abs/2603.15386) supports explicit geometry/tool
  grounding under ground-truth perception, but evaluates scene QA, not building
  design, comfort or causal understanding. Our authored model already supplies
  precise geometry; perception from photographs is not the missing first step.
- [Open-World 3D Scene Graphs](https://arxiv.org/abs/2511.05894) combines spatial
  relations and retrieved context. Its fixed proximity threshold and removal of
  floor/ceiling background are unsuitable defaults for architectural work. Here
  the caller chooses a metric radius and structural objects are not discarded.
- [Agentic Designer](https://arxiv.org/abs/2607.20866) motivates intermediate
  geometric feedback during layout generation. Interior furniture arrangement
  results do not validate a multi-storey architectural critic or GAN training.
- [Multi-agent research ideation](https://arxiv.org/abs/2507.08350) studies role
  and dialogue design. It motivates a bounded independent critic comparison;
  more agents or criticism alone do not establish better architecture.

The actual pilot must compare full context, lexical-only and spatial expansion
on the same questions, inspect missed critical objects, and confirm at least one
suspicion with exact geometry. Embeddings and asynchronous shadow criticism are
later experiments, not prerequisites or claims of this implementation.
