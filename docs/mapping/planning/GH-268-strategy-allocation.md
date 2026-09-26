# GH-268

Issue: https://github.com/cogco1/MonkeyHub/issues/268
Base: `1e38914c` (batch I, after batch H2).

A self-contained research lab shows state-conditioned strategy rollouts: four strategies start fresh sessions from one immutable state snapshot of a finite controlled environment, an external evaluator scores outcomes apart from tokens, and baseline allocators assign extra rollouts.

Batch I (2026-09-25/26), in the owner's order; plans are kept outside the repo.

## Lane `strategy-allocation`

- Landed (first slice): `labs/strategy_allocation/` is a package a collaborator can use from its README alone. Four synthetic cases (provided source, protected dependency, open direction, local conflict) have public deterministic dynamics; their goals, step judgments and reference optimum are hidden in `reference.py`, which only the evaluator imports. Strategies A–D share one prompt contract and output schema, and each rollout is one fresh `codex exec --ephemeral` process fed from an immutable, digest-pinned snapshot. The evaluator replays trajectories and takes no token input; records keep resources beside the outcome as JSON lines and CSV. `AllocationState → NextAllocation` wraps the unchanged `SequentialAllocator` (equal, round robin). `benchmark verify` replays a run from its snapshots and `rescore` re-judges retained trajectories. The smoke run made 16 calls, all ok in distinct threads. It showed that reference@0's open-direction goal contradicted its task; reference@1 corrects it and the run was re-judged without new calls. 60 offline tests; CI does not run lab tests.
- Open: the full V0 run (128 calls, command and estimate in the lab README) with a pinned model; the first adaptive (OCBA-style) rule and its matched-budget comparison with equal allocation; a snapshot taken from a real project's ContextPack@1; the collaborator's decisions listed in the README (allocation outcome, provider errors in analysis, a model-free B, harder cases).
