# GH-303

Issue: https://github.com/cogco1/MonkeyHub/issues/303
Base: `1e38914c` (batch I, after batch H2).

The drawing consumer of visual observation is measured: a --drawing benchmark compares compiler output, the project recipe, one visual review, and a typed repair with one re-check, on real cut plans.

Batch I (2026-09-25/26), in the owner's order; plans are kept outside the repo.

## Lane `drawing-benchmark`

- Landed (303-S3): `tools/benchmark_visual_observation.py --drawing` runs the owner's order on cut plans through the runtime's own routes: arm A the compiler's output, B the project recipe, C one `first_bundle` look at B's page, D a typed repair from C's actionable findings and one `after_repair` look. Per arm it reports the remaining manual corrections (one per failing exact check, per recipe key drawn otherwise, per open actionable finding), the looks' cost, wall clock, and D's acceptance and open findings with and without the second look. `docs/2026-09-26-drawing-visual-benchmark.md` has the definitions, the smoke run (synthetic fixture, three plans, four looks) and the full-run command.
- Open: the full run on real cut plans and the owner's judgement of the perceptual criteria (D-303-3); for #244, cut edges two touching solids both draw survive the cleanup, and corner joins need a material rule; the entourage repair should use the exact collision list (303-S2 policy or a Drawing rule); section perspectives are not covered.
