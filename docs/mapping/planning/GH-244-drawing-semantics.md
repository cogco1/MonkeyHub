# GH-244

Issue: https://github.com/cogco1/MonkeyHub/issues/244
Base: `132a618b` (batch G claim, GH-244 released).

Drawings carry their semantics: rebuilds replace their previous revision (#291), a deterministic cleanup report, component and material attributes with material-keyed hatch and poché in paper mm, who and why on each revision, and an axonometric model view with a cache for visual review.

Batch H1 (2026-09-25), in the owner's order; plans are kept outside the repo.

## drawing-application

- 291-S1: a rebuild registers as the whole-document replacement of its previous revision; a fork or a changed page aspect registers none. The cause (representation, source, upload) is derived, and Render ignores representation replacements.
- 244-S1/S2, application side: plan status and vector pass the receipt's `cleanup` through; `graphics.hatch.byMaterial` and `graphics.beyond.fade` are read, written and defaulted in paper units, absent unless named.
- 05-S1, application side: `reason` on the request and the boundary's attribution go to the revision's receipt; documents read `previousRevisionRef`, `attribution` and `reason` back from it.
- 303-S1, drawings side: `model-view` offers `axon` and reuses projections in process memory.
- Joins drawing-projection at `freeze_cut_plan(attribution=, reason=)` and the receipt's `cleanup`, `attribution` and `reason`. The plan and native test fixtures stand in for those receipt fields until then (`stand_in_for_the_projection_receipt`, which switches itself off); remove it when the lanes join.
- 05-S3b (the project recipe in `generate_plan`) waits for GH-223/recipe-decision.
