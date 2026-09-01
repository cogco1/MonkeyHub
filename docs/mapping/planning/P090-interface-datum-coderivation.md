# P090 — Interface datum co-derivation

- Origin: Planning
- Status: Ready
- Depends on: P047, P049, P050

## Goal

Make contact between dependent components correct by construction
instead of measured after realization. Host components publish named
interface datums (planes, frames, profiles) as first-class
geometry-program values; dependent boundaries reference the same value
node — identity, not numeric equality — and the publication/consumption
direction follows SUPPORT/HOST relation edges, keeping the derivation
graph acyclic. This lowers the standing "derived, not restated" law from
the decision level to the coordinate level and retires the embed-overlap
convention (`INTERFACE_OVERLAP`, `SITE_EMBED`) whose only purpose was to
make contact measurable a posteriori. Joint taxonomy: bearing/host =
shared datum; clearance = CLEARANCE interval; deliberate engagement =
explicit cited depth parameter; coplanar display flicker is resolved at
display level only and never by geometric offset.

## Acceptance

- Geometry programs express datum publication and reference; the
  compiler validates that references resolve and that the datum graph is
  acyclic along declared relation edges.
- Datum-shared joints verify by derivation-graph check as the primary
  proof; realized Breps pass a regression scan at model tolerance with
  no positive-overlap witnesses.
- A villa successor run migrated to datums re-runs the 2026-09-01 clash
  scan with zero unwaived interpenetration families (baseline: 36), and
  embed constants appear zero times in its authoring.
- A riser-series demonstration shows one rise-value change propagating
  exactly to its dependency closure — retained as dependency-scoped
  repair evidence.

## Write scope

- `archflow/state/geometry_program.py`
- `archflow/compilers/`
- `archflow/capabilities/geometry_proposal.py`
- `archflow/runtime/`
- `archflow/relations/`
- `archflow/realization/`
- `tests/`
- `docs/mapping/`
- `governance/work_registry.json`

(Scope amended 2026-09-01 during execution: the canonical compiler
lives in `archflow/compilers/geometry.py` behind a runtime facade, and
the proposal parser that must accept the new fields lives in
`archflow/capabilities/geometry_proposal.py`.)

## Tests

- Datum resolution, cycle rejection, and dangling-reference fail-closed
  paths.
- Graph-check versus Brep-regression agreement on shared-datum joints.
- Riser-series propagation closure test.
- Architecture firewall.

## Stop conditions

- Stop if any datum default would require a building-type answer inside
  the kernel.
- Stop before weakening write-side exactness anywhere.
- Stop if display-level mitigation would alter recorded geometry.
