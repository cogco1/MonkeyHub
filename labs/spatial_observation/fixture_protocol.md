# Public synthetic fixture and independent expectations

This is an experimental fixture for issues #195 and #170, not a project design or
production tool. All dimensions below are deliberately authored public synthetic
inputs. No private building, provider output, invented measurement, learned
feature or human design assessment is involved. This protocol and its analytic
expectations must not be passed as observation data to the evaluated model.

## Source and coordinates

`Fixture.create(explicit_external_root, variant="base", scale=1.0)` initializes a
P036 project at the supplied disposable path. Existing `StateRecord` element
producers, geometry compiler and `execute_occt_export(preview=False)` produce its
exact STEP and actual cold-read receipt. `read_elevation_source` verifies that
retained receipt, SHA, units, axes, complete object names and STEP on every query.
No hand-written STEP or second canonical geometry is used. HEAD stays at its
initial version; this is an experimental run, not an acceptance or issue.

The state producers use Hub Y-up meters, with profile pairs in Hub XZ. The exact
STEP and all point queries use CAD Z-up meters: **CAD (x,y,z) = Hub (x,z,y)**.
Thus authored profile pairs are also CAD XY, while height and elevation are CAD
Z. Front looks in CAD +Y with screen right +X and up +Z; back looks -Y; left +X;
right -X; top -Z with right +X and up +Y. `scale=2` multiplies every authored
length by two, so volume expectations multiply by eight. Tolerance is .001 m
in the program; line deflection is .0001 m, following current `model_view`.

`source.state_digest` is the existing **StateRecord.state_digest** binding digest,
the same existing developed-state binding retained by the compiled program in
the execution receipt. `snapshot.record_digest` separately exposes the existing
StateRecord content digest, including all authored rows and relations. `source`
also names the revision and exact retained STEP SHA; all three must match the query source.
STEP export headers may vary across fresh executions: use each run's actual SHA,
not an assumed reproducible STEP digest. The fixture record content is stable.

## Predeclared geometry and held-out variation

Every final solid is named `obj-<entity-id>`. These are extrusions of the stated
CAD XY footprint, from the stated Z elevation, with the stated height. The
structural/closure roles are declarations, not geometric inferences. `mystery`
has no declared role, as do several small unclassified experiment objects.

| ID | Base XY footprint | Z elevation / height | Held-out change |
|---|---|---|---|
| screen | x 0..4, y 2..2.3 | 0 / 3 | max x 4.5 |
| twin-a | x 1..2, y 4..5 | 1 / 1 | unchanged |
| twin-b | x 5..6, y 4..5 | 1 / 1 | x 6..7 |
| u-shell | (8,0),(12,0),(12,4),(11,4),(11,1),(9,1),(9,4),(8,4) | 0 / 2 | unchanged |
| insert | x 9.5..10.5, y 2..3 | 0 / 1 | x 9.25..10.25 |
| clash-a | x 0..1, y 8..9 | 0 / 1 | unchanged |
| clash-b | x .75..1.75, y 8..9 | 0 / 1 | x .5..1.5 |
| lintel | x 0..4, y 2..2.3 | 3 / .2 | max x 4.5 |
| seal | x 0..4, y 2..2.3 | 3.2 / .1 | max x 4.5 |
| fixing | x 3.7..3.8, y 2..2.3 | 3.3 / .2 | unchanged |
| drain | x 4..4.2, y 2..2.3 | 0 / .2 | unchanged |
| mystery | x 6..7, y 8..9 | 0 / 1 | unchanged |

These small named pieces exercise queries; their labels do not assert a
buildable building assembly. Both revisions are authored independently from the
table, not produced by an actual edit propagation action. The held-out variant
tests a changed snapshot and source freshness, not successful automatic design
revision. The fixture's declaration is code-authored; external human approval of
the task set must be reported separately if the issue requires it.

### Independent ontology-drift stress probe

`variant="role-drift"` is a separate public stress probe requested for #170's
ontology-drift acceptance item. It is outside the main five held-out-query
statistics and does not replace or retune the base, held-out or scale variants.
Every geometry parameter and dependency remains identical to base. Only
`mystery.fields.role` changes from null to the existing registered
`role.structural_support`; its label intentionally stays `unclassified object`.
This is a changed semantic declaration with a stale descriptive label, not a
new ontology term or a geometric deduction of structural fitness.

The variant goes through the same real StateRecord/P036/compile/export/retained
STEP path and acquires new content and state-binding digests. Exact state query
must return the explicit new role even when the label suggests no classification;
an old base source must be rejected. All 12 cold-read solid measurements and all
authored geometry parameters must remain equal to base. The independent stress
record should retain old/new bindings, selection and lookup results, mismatched
label/role and unchanged geometry, and should not be folded into the original
unknown-role success rate. No learned selector weights or scoring gold are changed.

Five explicit `dependency` relations have `propagation="revalidate"`:
screen → lintel → seal → fixing → drain, and twin-b → drain. Their direction
means upstream change requires downstream revalidation; it does not encode a
parameter formula or promise to move geometry. The existing StateRecord also
derives ground → every element from each base reference. The snapshot therefore
contains 12 geometric entities plus the `ground` datum, with no dangling edges.
There is deliberately no explicit shell/insert relation: a graph selector cannot
invent that spatial connection, while an exact pair query can test a suspicion.

## Independent expectations (evaluation only)

| Task | Base | Held-out | Derivation |
|---|---|---|---|
| twin-a / twin-b minimum solid distance | 3 m | 4 m | left edge B minus right edge A |
| u-shell / insert minimum solid distance | .5 m | .25 m | nearest cavity wall x=9 or x=11 |
| u-shell / insert common volume | 0 m³ | 0 m³ | insert lies in open cavity, positive wall clearance |
| clash-a / clash-b common volume | .25 m³ | .5 m³ | x overlap × 1 m × 1 m |
| twin-a front | no visible edges; hidden edges present | same | screen spans its X/Z projection, lies closer in Y |
| twin-b front | visible edges present | same | its X range is outside screen |
| twin-a back | visible edges present | same | viewed from +Y, twin-a is before screen |
| screen downstream closure | screen, lintel, seal, fixing, drain | same | declared four-edge chain; includes changed source |
| twin-b downstream closure | twin-b, drain | same | its one declared relation |
| twin-a downstream closure | twin-a | same | no declared downstream relation |
| mystery role | unknown | unknown | role absent; no inference from box shape |
| CAD point (10,2.5,.5) in insert | inside | inside | all coordinates strictly within the box |
| same point in u-shell | outside | outside | strictly within cavity, not the shell material |
| reuse base source against held-out fixture | refuse | refuse | revision/content/STEP mismatch |

The shell's bbox encloses the insert's bbox in plan and overlaps in height.
Consequently bbox intersection and bbox zero distance would be wrong proxies for
the nonzero **solid** clearance. The correction task may state a false prior of
.25 m³ overlap and ask the model to verify it: success requires correcting to zero
with relevant source evidence; the prior is a challenge, not reference truth.

## Observation permissions and exact-query interface

`snapshot()` returns a fresh plain dictionary containing source, units, coordinate
convention, `entities[{id, metadata, params}]`, relations and dependencies. It
contains authored shape parameters and declarations, no measured answers. It is
the fixed representation used for #170 selection and the A representation for
#195. Unknown role remains null. Object identity in metadata is explicit.

`render_views()` returns one entry per requested direction: `{png: bytes, width,
height, recipe, source, render_seconds}`. It directly imports the current Studio
`_elevation_view` recipe and applies the current `model_view` scale calculation,
then invokes the existing drawing owner. PNGs are unlabelled grayscale visible
lines on white, maximum edge 1024 pixels, not RGB. All 12 solids participate in
each HLR solve. Tests compare every PNG byte-for-byte against the actual current
application `model_view`, with only source lookup supplied by this lab's retained
project. B0 receives the PNG and allowed common source/view metadata; D0 receives
identical PNGs and access to the following source-bound tool.

`exact_query(action, args, source=fixture.source)` always checks source equality
and cold-reads verified STEP. It returns `{source, action, result, query_seconds}`.

| Action | Args | Result facts |
|---|---|---|
| state | ids optional | authored snapshot, filtered entities |
| dependencies | ids | existing StateRecord closure, edges, declared-only caveat |
| pair | first, second | existing OCCT status, distance_m, common_volume_m3 |
| shape | id | existing exact shape measurement, including explicitly named bbox |
| point | id, point [CAD x,y,z] | OCCT inside/outside/boundary classification |
| visibility | view, ids optional | object IDs with counts of real visible/hidden HLR polylines |

Visibility counts are not pixel occlusion percentages or exact CAD edge counts:
HLR may split edges, and polyline discretization has a tolerance. Zero visible
plus nonzero hidden indicates no visible extracted line in that projection;
visible does not imply that the complete object is unoccluded. No action returns
task IDs, expected answers, gold labels or comparison scores. Query accounting
includes retained-source cold-read; rendering and export timings are separately
available. These intervals are local measured latency, not provider costs.

## Checks and limitations

Run `python -m pytest labs/spatial_observation/test_fixture.py -q` with the existing
cadquery-ocp/Pillow runtime. Checks cover analytic distances and volumes in three
variants, exact bbox counterexample, complete-scene HLR identity, current B0 PNG
parity, source rejection, unchanged HEAD and coordinate/role/dependency behavior.
These checks validate the synthetic geometry and adapter, not model superiority
or architectural adequacy. No depth/normal/tensor model input is claimed. A
hidden line query is a permitted D0 fact; B0 cannot recover arbitrary entity IDs
from unlabelled pixels, and that information loss should stay visible in scoring.
