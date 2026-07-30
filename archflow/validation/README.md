# Validation

Owns read-only deterministic hard gates and explicit goal-completion obligation
checks for a Candidate Submission.

Hard gates protect minimal invariants. Obligation checks assess whether the
stated goal is discharged. Neither layer performs soft ranking or mutates
canonical state. See
[`docs/DYNAMIC_MAP.md`](../../docs/DYNAMIC_MAP.md).

Current status: P1 deterministic base, artifact, required-claim, and obligation
gates emit a frozen `ValidationReceipt`. Validator exceptions fail closed and
cannot mutate canonical state.

## Voxel usability hard gates

`validate_usability` consumes only `BuildingProgram@1`, a detached
`VoxelObservation@1`, and explicit use-zone evidence. It checks six objective
questions: footprint target, clear height, exterior entrance, traversable
connectivity/width, required-use zones, and grounded support.

Failures contain stable codes, measured values, thresholds, and spatial
evidence where available. Incomplete evidence fails closed only for the
geometry-dependent gate it prevents from being checked. The function has no
MCP, workspace, committer, Architect, or aesthetic-score input channel.

## Voxel use scenarios

`UseScenarioValidator` derives entrance-to-required-zone and inter-zone routes
from the current `BuildingProgram@1` and explicit use-zone evidence. It searches
only observed walkable cells; vertical connections require explicit typed
evidence. Blocked routes, inadequate headroom, isolated levels, unbound zones,
unknown route evidence, and exact-candidate mismatch become hard findings
through the existing `Validator` protocol.

Primary validation also requires `ScenarioObservationBinding@1`. The binding
names the exact candidate-program digest, neutral-geometry-program digest,
sandbox-realization receipt, validation-program digest, observation content,
artifact, and workspace. A missing, stale, or externally sourced binding is a
hard finding. External-world observations may be evaluated as comparison
evidence, but cannot become the primary passing observation.

The validator has no MCP, workspace, committer, evaluator, score, or geometry
edit input. A successful MCP transport therefore cannot bypass a failed use
scenario in the aggregate `ValidationReceipt`.

## Commitment transition monitor

`monitor_commitments` is a read-only compiler from exact-base criterion
observations to a `CommitmentMonitorReceipt`. It keeps normative state separate
from candidate work:

- an achievement commitment may remain pending until its declared completion
  boundary, while a maintenance commitment fails as soon as an active
  condition is violated;
- missing satisfaction or activation evidence creates a typed, blocked
  evidence obligation rather than guessing or editing geometry;
- a candidate cannot replace an active commitment, and a negotiable failure
  can only emit an authority-bound revision proposal;
- only named `invalidates` and `requires_revalidation` edges propagate repair
  obligations, so unrelated branches remain closed;
- preference and hypothesis findings remain advisory and cannot waive a hard
  or revision-required failure.

Temporal monitors store only decision-relevant progress such as whether a
condition has ever activated or an achievement has been observed. Their state
round-trips independently; the event trace that explains how it was reached
remains outside the operational Markov state.
