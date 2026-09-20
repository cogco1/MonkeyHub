# Study: evidence, competing explanations and a bounded geometry experiment

Checked 2026-09-20 for [#73](https://github.com/cogco1/MonkeyHub/issues/73).
This is a method note and reproducible synthetic example. It does not establish
the intent of a historical designer, validate a universal composition score, or
report a model call that has not happened.

## Decision and existing boundary

Use corrected source polygons to test **a stated geometric consequence under
stated conditions**. Retain the explanation as a revisable claim. Neither matching
a precedent nor surviving one edit establishes why a real building was designed
that way. A person's preference needs their own attribution.

The existing [Study owner](../../apps/archflow-studio/api/archflow_studio_api/application/study.py)
already binds evidence to an exact registered page and retains revisions through
P036. [#111](https://github.com/cogco1/MonkeyHub/pull/111) delivered archive integrity;
[#117](https://github.com/cogco1/MonkeyHub/pull/117) delivered aspect-correct,
exact-revision comparison. Reuse these. The document editor remains the interaction
surface; Study evidence is not ordinary annotation ink or canonical design state.

At the inspected baseline `3a92b414`, `StudyDerivation@1` measures axis-aligned
bounds and compares their relation signatures. Its automatic `0.67` Jaccard
threshold labels a composition family without empirical or human validation.
Neither its score nor the number of relations is a measure of usable space.
Retain old derivation snapshots for compatibility; new research results must name
their actual method and leave family/preference judgment unresolved unless an
attributed judgment exists. The compare endpoint's topology and proportions are
descriptors, not proof of architectural equivalence.

## Original research that informs the experiment

| Primary source and checked extent | Mechanism adopted | Limit or rejected inference |
| --- | --- | --- |
| Stiny and Mitchell, *The Palladian Grammar*, 1978, 5(1), 5–18; [publisher abstract and bibliographic record](https://journals.sagepub.com/doi/10.1068/b050005). Full paper was not available through the checked publisher page. | Make transformation rules and their resulting geometry explicit. | Generating a matching plan does not establish the historical design process. No uninspected detailed rules are imported. |
| Benrós, Duarte and Hanna, *A New Palladian Shape Grammar*, 2012, 10(4), 521–540; [publisher abstract](https://journals.sagepub.com/doi/10.1260/1478-0771.10.4.521). The abstract explicitly tests different rules for the same corpus. | Keep more than one explanation alive; matching the output does not uniquely identify its mechanism. | This experiment does not reproduce their full grammar or claim an independent replication of their corpus result. |
| Falletta, *By-Right, By-Design*, first edition, copyright 2020; [publisher](https://www.routledge.com/By-Right-By-Design-Housing-Development-versus-Housing-Design-in-Los-Angeles/Falletta/p/book/9780815385059) and [27-page publisher-distributed preview](https://api.pageplace.de/preview/DT0400.9781351202503_A38998117/preview-9781351202503_A38998117.pdf), introduction and comparative framework. | Compare outcomes against the different conditions and priorities of design, development and planning. The introduction does not presume that by-design means better. | A repeated shape cannot be treated as a universal necessity. This note does not independently verify any Los Angeles regulation or infer a project's economics from its plan. |

These publications remain copyrighted; only method descriptions and links are
used. Their drawings are not redistributed as fixtures. The synthetic example
below has no historical sources, so its `historicalSources` list is empty.

## Mature implementations checked

| Version, source and license | Actual implementation checked | Adoption decision |
| --- | --- | --- |
| QCAD `v3.33.1.0`, commit `897079c2d11aaa0869f839a6cf5df8a937dbddbe`; [DrawPolyline.js](https://github.com/qcad/qcad/blob/897079c2d11aaa0869f839a6cf5df8a937dbddbe/scripts/Draw/Polyline/DrawPolyline/DrawPolyline.js), [RMoveReferencePointOperation.cpp](https://github.com/qcad/qcad/blob/897079c2d11aaa0869f839a6cf5df8a937dbddbe/src/operations/RMoveReferencePointOperation.cpp), [license](https://github.com/qcad/qcad/blob/897079c2d11aaa0869f839a6cf5df8a937dbddbe/LICENSE.txt). Inspected source files say GPL-3.0-or-later; distribution includes exceptions and separately licensed resources. | `pickCoordinate` separates preview from committed point insertion. Reference-point movement acts through a transaction and directs dependent entity updates into the preview document during preview. | Reuse the same distinction between a reversible visual proposal and a saved correction in the existing DocumentCanvas. No QCAD code, engine or proprietary polyline add-on is copied. |
| OpenCV `4.12.0`; [pointPolygonTest](https://github.com/opencv/opencv/blob/4.12.0/modules/imgproc/src/geometry.cpp), [connectedcomponents.cpp](https://github.com/opencv/opencv/blob/4.12.0/modules/imgproc/src/connectedcomponents.cpp), [license](https://github.com/opencv/opencv/blob/4.12.0/LICENSE). Top-level Apache-2.0; inspected older files retain their permissive Intel notices. | Point classification tests contour edges rather than its rectangle. Component labeling uses explicit connectivity; component area counts member pixels, not rectangle area. | Adopt the distinction between extent, occupancy and connectivity. Do not add OpenCV merely for these polygons, and do not mistake connected ink for a passable architectural route. OpenCV was inspected, not installed or run. |
| Shapely `2.1.2`, already pinned by the Studio API; [DE-9IM predicates](https://shapely.readthedocs.io/en/2.1.2/reference/shapely.relate_pattern.html), [license](https://github.com/shapely/shapely/blob/2.1.2/LICENSE.txt), BSD-3-Clause. | Polygon predicates distinguish interior and boundary intersections. | Use the existing dependency for actual polygon area/intersection/contact/distance. Invalid polygons should return an explicit unsupported result rather than be silently repaired. The independent fixture oracle below does not call Shapely. |

The adoption is a mechanism choice, not a claim that these tools implement Study's
reasoning or that their licenses grant access to arbitrary drawings.

## Audited synthetic example

[study_fixture.py](../../apps/archflow-studio/api/tests/study_fixture.py) returns
PNG bytes, exact polygons, research input and an independent geometric audit. It
writes no files or project state. Callers register bytes through the ordinary
document API and keep test output in a temporary project. The original diagram
and coordinates are dedicated to [CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/);
helper code uses the repository license.

The square page contains an envelope, a concave left mass, a rectangular right
mass and their intervening stepped void. Page-normalized areas are respectively
`0.64`, `0.26`, `0.24`, `0.14`. The three interior polygons partition the envelope.
Left-mass and void bounding boxes overlap by `0.08`, while their actual interiors
overlap by **zero**. This is a deliberate counterexample to bounding-box semantics.
The baseline raster was rendered and visually checked against these polygons.

The two competing explanations are:

1. **Conditional connection:** the clear strip could connect the page's north and
   south edges, provided the ground surface and both ends are passable.
2. **Environmental interval:** the same footprint could be a non-passable light,
   planting or drainage gap. No daylight or drainage performance is inferred.

The evidence gap is substantive: the source gives no section, roof, levels,
surface, endpoint access, metric scale or use record. Geometric connectivity
supports the first explanation's geometric requirement; it does not discriminate
between the uses. No original author, historical intention or personal preference
is invented.

Each intervention edits only the right mass, retaining the original source page
and source void trace. The **unobstructed remainder** of that trace is recomputed;
it is not assumed to retain its area or connectivity. Area units below are page
area. The first two edits can obstruct the source void; the larger one is an
explicit failed layout, not a proposal for construction.

| Change from the exact baseline | Predicted consequence | Independent checked result |
| --- | --- | --- |
| Translate right mass `dx=-0.05` | Narrows the interval; retains continuity. | Void/mass intersection `0.04`; remaining clear area `0.10`; one component joining both ends. |
| Translate right mass `dx=-0.12` | Blocks the neck; loses continuity. | Intersection `0.092`; remaining area `0.048`; two components, neither joining both ends. Mass/mass overlap `0.004` also rejects it under non-overlap conditions. |
| Translate right mass `dx=+0.05` | Loses two-sided contact without losing continuity. | Remaining clear area `0.14`, one component; true polygon distance `0.05`. Right mass leaves the fixed envelope, so that condition rejects the edited layout. |
| Scale right mass `0.8` about its center | Loses contact while retaining continuity. | Mass area `0.1536`; distance `0.03`; remaining clear area `0.14`, one component. |

`fixture_observations()` subdivides the plane at the authored orthogonal vertices,
classifies each cell center by polygon ray parity, sums occupied cell areas and
flood-fills cells sharing a positive-length edge. This is exact for this bounded
orthogonal fixture; it is neither a pixel tolerance nor a production navigation
solver. Corner contact does not count as connectivity. Shapely independently
checks intersections, area, distance and component count for these coordinates.
No physical clearance, fire egress, accessibility or human route claim follows
without scale and the missing conditions.

For comparison, register `base`, `contracted` and `blocked` as three separately
identified **synthetic variants**, not independent historical precedents. Read
their exact ledger references through the existing comparison endpoint. One
comparison tests proportion/contact changes; the obstructed variant challenges
the inferred relation. These cases do not estimate a population, learned range,
style or statistical accuracy.

## Conditional transfer and remaining judgments

The editable pattern is to maintain an unblocked connected interval **when a
connection is required**. Two-sided contact is a separate relation: contraction
loses it while continuity survives, so it cannot automatically become a protected
condition. The prior asks a new project to test its required connection instead
of copying the silhouette or assigning corridor/wall/room labels.

The changed-context input declares the interval a non-passable planting/drainage
strip and removes the through-access requirement. Its prior is **revised**:
retain the geometric separation example, withdraw the circulation recommendation,
and leave the environmental explanation untested. The earlier source and findings
remain unchanged. `preferenceStatus` stays `unresolved`; no user is signed up to
prefer the result.

This fixture is a manual authored baseline. Real machine tracing needs a real
provider, the registered input page and a retained invocation receipt; its
proposal must remain distinguishable from later correction. Provider success,
trace agreement, geometric consequence, explanation quality and human preference
are separate outcomes. The fixture helper and this note alone do not establish
that the Hub UI or an image provider completed that workflow.

## Integrated experiment, 20 September 2026

The implementation was then exercised in a disposable project through the real
registered-document and Study APIs. An independent headless browser used the
existing Hub/Board document editor: drawing and correcting a fifth trace,
confirming evidence, undo/redo, editing the explanations and prior, running four
interventions, comparing three synthetic variants, and saving/reopening the
result. Injected save failure and a real concurrent-revision conflict retained
the local draft. Ordinary document annotations and canonical HEAD stayed
unchanged. This tests the manual workflow; it is separate from the provider run.

The opt-in provider experiment used the original CC0 page and the configured
Codex CLI transport (`codex-cli 0.153.4`, receipt model identifier
`codex-cli-default`; the executable did not report a more specific model).
No model override was supplied. Three real invocations completed:

| Invocation | Input boundary | Observed result | Provider duration |
| --- | --- | --- | --- |
| Trace | Registered page, empty evidence. | Four machine polygons, all retained as proposed. | 16.938 s |
| Initial reasoning | Authored polygon corrections and an open question; no supplied hypotheses, gap, interventions, pattern or prior. Original machine rows were retained as rejected, not relabeled as a human endorsement. | Two competing explanations, an evidence gap, four declared interventions with server-computed outcomes, and a conditional pattern/prior. | 34.108 s |
| Challenge the prior | The first-round measured outcomes and an explicit synthetic context negating the model's own comparable-levels condition. | Revised prior, citing the actual outcomes of `cf1` and `cf4`; personal preference remained unresolved. | 44.893 s |

The initial model predicted that removing the left mass (`cf1`) would increase
clear area from `0.14` to `0.16`. The result remained `0.14`: the declared void
trace was fixed, and removing a neighboring mass did not add untraced area. Its
leftward translation prediction (`cf2`) similarly overestimated the clear area.
Right-mass translation (`cf3`) produced the predicted `0.10` and one component;
rightward left-mass translation (`cf4`) produced `0.06` and two components.
The follow-up response explicitly recognized the `cf1` prediction failure and
used `cf4` only as evidence about simulated planar connectivity. It withdrew
transfer of a passage interpretation to the changed-levels project. This is an
observed correction, not a success score for architectural reasoning or proof
of the original drawing's purpose.

A final repetition added the complete comparison path before the third call.
The first reasoning input still contained only corrected evidence and the open
question. Afterward, the test separately registered the contracted CC0 variant,
saved a comparison of the exact first-round and variant revisions, and supplied
that retained result with the context challenge. Trace, initial reasoning and
comparison-informed reasoning took `12.354`, `48.112` and `42.350` seconds.
The initial alternatives in this run were usable passage versus graphic negative
space. The model rejected transfer after its own shared-topology condition was
explicitly negated. Its explanation cited the measured `cf-left-in` overlap
`0.04` and clear-area change `0.14 → 0.10`, and the comparison's right-mass area
ratio `0.375 → 0.24` and height ratio `1.0 → 0.8`. These values matched the
retained outputs. The comparison uses the existing bounds-based descriptors;
those ratios are not a new polygon-topology validation. References and results
survived model continuation and cold read unchanged. This repetition tests
comparison input and use; it is not an accuracy or improvement comparison between
the two model runs.

The provider response, request, source/revision binding, usage and duration are
retained in the corresponding Study revisions. The reproducible opt-in test is
`StudyLiveModelTests.test_real_codex_trace_evidence_only_reason_then_challenge_prior`
in [test_study_model.py](../../apps/archflow-studio/api/tests/test_study_model.py).
Run from `apps/archflow-studio/api` with the repository and API roots on
`PYTHONPATH`, `ARCHFLOW_STUDY_LIVE=1`, and a working configured Codex executable:

```text
python -m pytest tests/test_study_model.py::StudyLiveModelTests -s -q
```

This makes real provider calls and prints a temporary output path containing the
responses, including negative results. Ordinary tests skip it. A prior run with
authored hypotheses was only a continuation check; the evidence-only run above
is the basis for the initial-reasoning observation. No private building material
was sent. These synthetic variants do not complete validation on independent
architectural precedents, establish a learned composition-family threshold, or
implement applying a prior to an editable building model.
