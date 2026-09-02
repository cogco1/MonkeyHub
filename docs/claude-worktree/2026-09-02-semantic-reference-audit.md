# Geometric robustness / semantic reference audit (2026-09-02)

Hypothesis under test: most tolerance / near-miss / floating / misalignment
problems come from components that share a design datum but compute their
coordinates separately; if they evaluate from one authoritative reference,
their coordinates coincide before geometry exists (construction by
relation), and post-hoc healing becomes unnecessary except for kernel-level
problems. No code changed; every claim is from the working tree at
`535ad9c` and the run records.

## Verdict

The hypothesis holds for this repository, and it is already half-proved by
its own history: the datum-bound runs (run-017, P090/M096/P098) produced
80/80 contacts within 1 mm with no healing, while the literal-coordinate
run (run-012/014) needed a 25 mm "embedding counts as contact" ruling and a
stage-5 audit. Roughly three quarters of the divergences found are Type I
(separately computed coordinates for one semantic position); the rest are
Type II (a gap or an embed that must be *declared*, not tolerated) and a
small Type III residue that belongs to Rhino. The current data structure
allows relations to precede geometry at the datum level only (LEVEL /
PLANE / SERIES bindings on `base_level`-class parameters); grids, hosts,
supports and alignments are not yet references the compiler resolves. The
missing piece is a reference resolver in front of the producers — not a
seventh table.

## Current failure taxonomy

**Type I — preventable reference errors (found):**

| Site | Source A | Source B | What happened |
| --- | --- | --- | --- |
| Villa run-012, column top vs abacus | `ionic_column_profiles(..., SERVICE_TOP)` rings up to `COLUMN_HEIGHT` | abacus bottom `SERVICE_TOP + COLUMN_HEIGHT - 0.02` | a 20 mm *embed* written as a literal so that separately computed tops "touch" |
| Villa run-012, entablature vs pediment vs roof | `ent_bottom + ENTABLATURE_HEIGHT` | `front_eave = ... - 0.015`, `front_ridge = ... - 0.015` | 15 mm compensations to avoid coplanar faces |
| Villa run-012, frames / leaves / glazing vs wall face | `BODY_HALF - 0.08`, `- 0.055`, `- 0.035` | wall face `BODY_HALF` | four independent face offsets for one host face |
| Villa run-012, facade band | `cursor = -BODY_HALF - 0.17`, `BODY_HALF + 0.17`, `+ 0.07`, `+ 0.08`, `- 0.12` | body edge | overhangs written as literals per function |
| Villa run-012, portico | `front_z = BODY_HALF + PORTICO_DEPTH - 0.62` | `pediment_front = BODY_HALF + PORTICO_DEPTH + 0.10` | two "front" planes for one facade |
| Stage-5 audit (run-014) | generator embeds 10–25 mm as "contact" | validator | the ruling "≤25 mm counts as overlap; true clash >60 mm" is a tolerance invented to absorb Type I |
| run-017 west abaci | historical west script rounded plan extents to mm | run-016 extents | 0.5 mm divergence for one position rounded in one script and not the other |
| Rocca `derive_main_block` (today) | wall height from pixel-measured cornice 32.41 | project level main-cornice 32.0 (labels) | two sources for one level → `walls_grade_to_cornice` failed until heights were taken as level differences |
| Rocca `derive_south_portico` (today) | export reused by "status == succeeded" | receipt program digest | provenance divergence of the same kind: one identity, two lookups |

**Type II — semantic ambiguity (found):**

| Site | Relation that should decide |
| --- | --- |
| Entablature courses set into the entablature block (run-017: 12 declared engagements per side) | `ENGAGEMENT(course, entablature, depth ∈ [a, b])` — an embed to keep, not a clash, not a contact |
| Front entablature vs returns (2 per side), frame joinery (10 per side) | `MEETS` at a corner vs `ENGAGEMENT` at a lap: the same 0–20 mm overlap is right or wrong depending on the relation |
| Rocca entablature ends into the antae (~1 piede) | declared bearing engagement, written today as a record |
| Rocca hall wall through the roof massing | massing solid vs shell: an `INTERSECTS`-tolerated massing, not a clash |
| Villa stair vs terrain (`interlock_basis: explicit materialization allowance, not geometric tolerance`) | the script already names it: an allowance is a relation parameter |
| Wall tool overshoot (cut margin) | `HOSTS_VOID` needs *through*-cut semantics: overshoot is a producer rule, not a coordinate |

**Type III — genuine kernel problems (found):**

| Site | Nature |
| --- | --- |
| Rhino merged the 1 cm tool overshoot with the wall face (document tolerance) | boolean face merging — the trigger was our margin choice, the behaviour is the kernel's |
| rhino3dm loose bounding boxes on trimmed Breps (18 mm on prisms; 10 % on aperture boxes) | instrument error, solved by RhinoCommon tight boxes |
| Loft caps, boolean topology after seven cutters, `expected_object_bounds` failing closed for non-box bases | kernel topology; handled by explicit fail-closed rules |

Where the mass sits: of the 26 compensating literals in the villa's
`add_*` functions, 21 are Type I (one position computed in two places),
5 are Type II (an intended overhang, allowance or embed that should be a
relation parameter), 0 are Type III. The stage-5 audit's whole finding was
Type I disguised as a tolerance.

## Reference audit

Coordinate sources by layer:

| Layer | 1 literal XYZ | 2 project constant | 3 derived expression | 4 shared datum | 5 grid ref | 6 host-relative | 7 support-relative | 8 geometry-derived |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| villa run-012 monolith | 309 literal lines | 29 constants | inline arithmetic per function (`BODY_HALF + PORTICO_DEPTH - 0.62`) | none | none (angles by `rotated(…, side*90)`, 13 sites) | none (openings placed by literal `cx, cy`) | none (stair riser from `SERVICE_TOP / STAIR_RISERS` is derived, not a support ref) | none |
| run-017 seat scripts | none for levels | LEVEL_TOL 1.5 mm | course layout rule (soffit offsets, top reveal) | yes: 12–14 interface datums per side, `base_level` bindings (M096) | none | none | none | **yes**: every datum *value* read from run-016 boxes (`ref.z` ×11, `r3` ×14) |
| runner producers (P089) | profiles at Y=0 | none | heights as level differences (`top_level − base_level`) | yes: base_level per element, column-top published | published only (nothing binds to a grid) | openings along the wall line (`along`, `sill`, `head` offsets) — host-relative ✓ | none (entablature binds the column-top datum: a support-relative *level*, not a support ref) | none |
| compiler | — | — | — | resolves LEVEL→NUMBER, PLANE→VECTOR3(origin/normal), SERIES→NUMBER[index]; rejects a restated literal (`RESTATED_DATUM_PARAMETER`) | PLANE datums resolvable but no producer emits plane bindings | — | — | — |
| Rhino back end | lifted points rounded to 9 decimals | — | — | — | — | — | — | readback compares named bounds at 3 mm |

Interfaces, with their two sources today and whether one reference removes the divergence:

| Relation | Source A (today) | Source B (today) | Expected semantic relation | Divergence risk | Shared reference eliminates it? |
| --- | --- | --- | --- | --- | --- |
| column top — capital (abacus) bottom | column rings to `COLUMN_HEIGHT` (012) / column-top datum (017) | abacus `−0.02` literal (012) / `base_level` → column-top datum (017) | SUPPORTS at datum column-top | high in 012 (20 mm embed), zero in 017 | yes — already proven in 017 |
| capital — entablature | abacus + 0.18 | `ent_bottom = SERVICE_TOP + COLUMN_HEIGHT` | SUPPORTS at abacus-top | medium (two expressions of one height) | yes (017: entablature binds abacus-top) |
| entablature — pediment / roof | `ent_bottom + ENTABLATURE_HEIGHT` | `− 0.015` eave/ridge fudge | SUPPORTS / MEETS at entablature-top | 15 mm | yes (017: pediment and roofs bind entablature-top; project level main-cornice in P098) |
| wall band — landing / floor | wall course 009 bottom literal | landing top literal | MEETS at piano-nobile | rounding | yes (P098: both bind `level-piano-nobile`) |
| wall — opening (host) | wall boxes cut around opening literals (012) | frame literals `cx, cy` | HOSTS_VOID at a host offset | high (28 boxes hand-fitted) | yes (P092: opening = along/sill/head on the wall; aperture = wall ∩ tool) |
| opening — frame / glazing | frame `BODY_HALF − 0.08`, glazing `− 0.035` | wall face | FILLS_VOID with type projection | medium | yes (opening solver: all faces from the void + type parameters) |
| stair top — landing | `STAIR_RISER = SERVICE_TOP / risers` | landing top literal | MEETS at piano-nobile | low (same constant) — but the solver takes a *number* (`StairInterface.datum: float`), not a level ref | partially: the value is shared, the reference is not |
| stair foot — terrain | terrain box top | stair bottom `0.0` | SUPPORTED_BY terrain | medium (allowance literal) | yes, with a terrain-grade datum and an explicit allowance parameter |
| portico — main block | `front_z = BODY_HALF + PORTICO_DEPTH − 0.62` | wall face `BODY_HALF` | MEETS / INTERSECTS at the facade plane | high | yes with a facade-plane (grid) reference — not yet resolvable by any producer |
| drum — dome, hall — drum | `ATTIC_TOP − 0.12`, `radius − 0.20` | dome sections | SUPPORTS at drum-top | medium | yes (Rocca today: dome binds drum-top level, 0 deviation) |
| four sides | `rotated(profile, side*90)` recomputed per side (13 sites) | — | one type, four placements | low numerically, high semantically (four copies) | yes: placement by facade grid + outward direction |
| column axes — grid | `centers = (index − 2.5) * span` per side | grid record (P098) published but unused | ALIGNS_WITH axis k | zero today only because both use the same formula | yes once column-array resolves `GridRef` |

Validation failures that were really reference failures: the stage-5 audit
(embeds as contact), the west-abaci 0.5 mm, the Rocca cornice mismatch, the
attic-window-through-roof class (fixed by the exclusion handover, i.e. a
relation participating in generation). None of the validation failures in
the records was a kernel failure except the loose-bbox instrument.

## Proposed semantic reference model

Reference kinds, and where each lives:

| Reference | First-class data type? | Only a relation? | Resolved by | Resolution stage |
| --- | --- | --- | --- | --- |
| `LevelRef(level_id)` | yes — exists (`ProjectLevel` → LEVEL datum) | — | compiler (`base_level`) | GeometryProgram lowering (today) |
| `OffsetFrom(ref, d)` | yes — exists as `base_offset` for levels; generalize to any ref | — | producer emits `base_offset`, compiler lifts | lowering |
| `GridRef(axis)` / `GridIntersection(a, b)` | yes — grid entities exist (PLANE datums); the *reference* type is missing | — | producer (plan coordinates from two axes) | AssemblySpec (producer input) |
| `AxisRef` (a line: facade line, symmetry axis) | yes — same as a grid axis with a role | — | producer | AssemblySpec |
| `HostRef(element)` + `OffsetAlongHost` | yes — exists implicitly (`HostedVoid`: along/sill/head on the wall) | — | host's producer publishes the void; the hosted producer consumes it | AssemblySpec |
| `SupportRef(element)` | as a *datum published by the support* (column-top today) | the SUPPORTS relation names the datum role | producer of the supported element binds the datum | lowering |
| `AlignmentRef` | no — a relation with a datum role (plane or level) | yes | producer binds the plane datum | lowering |
| `ConnectionRef` | no — a relation (INTERFACE / ADJACENT) whose check is MEETS | yes | validator | verification |

Rules:

1. An element stores references and offsets; a coordinate appears only as
   the output of resolution. `Wall W17: from GridIntersection(A,1) to
   GridIntersection(F,1), face exterior, bottom LevelRef(L1), top
   LevelRef(L2)`.
2. Resolution happens in two places, deliberately: **plan** references
   (grid, host along, facade line) resolve in the producer into profile
   points at Y = 0; **elevation** references (levels, support datums,
   offsets) stay symbolic in the operation (`base_level`, `base_offset`) and
   resolve in the compiler from the datum record. This is what makes the
   compiled program a function of the datums: change a level, recompile,
   no producer rerun.
3. A canonical reference resolver is needed for the plan side: one module
   `resolve_reference(ref, state) → (x, z)` used by every producer, so grid
   intersections, host offsets and facade planes have one implementation.
   The elevation side already has it (`resolve_interface_datums`).
4. Extend the compiler's datum kinds by one: `PLANE` bindings to profile
   points are already resolvable (origin/normal); what is missing is a
   binding target for "this profile edge lies on plane P" — leave that to
   the producer via the resolver (rule 2) rather than adding geometry to the
   compiler.

## Relation semantics

Should one relation carry GENERATE, INVALIDATE and VERIFY? The code says
yes for the *relation*, no for its *payloads*: keep one relation record
with three bindings, and keep obligations separate.

| Relation | GENERATE (constrained generation) | INVALIDATE (propagation) | VERIFY (after materialization) |
| --- | --- | --- | --- |
| SUPPORTS / SUPPORTED_BY | the supported element binds the support's published datum (`column-top`) — proven in run-017 and Rocca | support → load: REVALIDATE (datum value change) or INVALIDATE (support removed) | `support_contact` on bounds; RhinoCommon probe at fine stage |
| HOSTS / HOSTS_VOID | the hosted element is placed along the host and cut by the host's producer (P092) — proven | host moves → hosted stale; host removed → hosted invalid | `aperture_exists`, aperture bounds = void |
| MEETS | both elements bind one datum (level or plane) | REVALIDATE both | bounds coincide within compile tolerance (not a healing tolerance) |
| CONNECTS (INTERFACE / ADJACENT) | shared datum where a datum exists; otherwise no generation role | REVALIDATE | `meets` or `clearance` per relation |
| ALIGNS_WITH | shared plane datum (facade line, axis) → producer resolves both | REVALIDATE | plane distance |
| CLEAR_OF | exclusion bounds from the counterpart's realized geometry refuse the placement (run-017 attic windows; runner handover) — proven | counterpart moves → REVALIDATE | `clearance_interval` |
| SEPARATED_FROM(gap) | the producer offsets by the declared gap from the shared reference | REVALIDATE | gap within interval — never normalized to contact |
| ENGAGEMENT(depth) | producer embeds by the declared depth | REVALIDATE | overlap within interval — never reported as clash (M097) |

The stricter structure: `Relation{kind, participants(role, ref), datum_role
(generation), propagation_rules (invalidation, exists), validator{check_kind,
interval, tolerance} (verification)}`; obligations remain `DesignObligation`
records linked by `source_ref`. So: **the RelationTable is an input to
generation, not an acceptance list** — the datum role on SUPPORTS / HOSTS /
MEETS / ALIGNS_WITH is what the producer's reference resolver consumes;
CLEAR_OF is consumed as exclusion bounds; only CONNECTS without a datum and
Type III checks stay verification-only.

## Three change simulations

**Case 1 — L2: 3900 → 4200.** Authoritative reference: `level-L2`.
State diff: one level entity (or one parameter if derived). Closure: every
element with `base_level`/`top_level`/`sill_level` = L2 or a support datum
derived from it (columns standing on L2, the slab bottom, walls topping at
L2, openings whose sills ride on L2, stairs whose target is L2 → derived
riser → riser-count obligation, facade courses bound to the L2-derived
cornice). Reference resolution: elevation side only — no producer rerun for
elements whose *plan* is unchanged; recompile resolves `base_level` from the
new datum (today's compiler). Producers rerun: stairs (riser count may
change), anything whose plan depends on L2 (none). Geometry regenerated: all
bound operations (revised, same op ids), retired 0, created 0. Validators
rerun: SUPPORTS/MEETS on L2 (contact holds by construction; the check
passes trivially), stair band. Possible numerical error: none from
references; rounding only if a producer rounds (the runner rounds to 9
decimals; the seat scripts rounded datums to mm — remove that).

**Case 2 — terrain elevation changes.** Authoritative reference:
`terrain-grade` level (today a project level) plus, for a sloped site, a
terrain surface datum. State diff: the terrain entity. Closure: everything
SUPPORTED_BY terrain — stairs (source level), podium/basement walls
(base_level grade), landings by lineage, the exterior-stair envelope. A
floating building is impossible by construction: the stair solver takes
lower/upper datums as *inputs* (`StairInterface.datum` — today a number;
must become the resolved terrain datum), and refuses (UNSAT) rather than
producing a floating flight; the podium's base binds grade. Producers
rerun: stair (riser count), any element with a grade base whose *plan*
depends on terrain (none). Validators: SUPPORTED_BY contact at the foot,
the stair band. Numerical error: none at interfaces; Type III only if the
terrain is a NURBS surface and the foot is a boolean with it.

**Case 3 — wall moves 300 mm.** Authoritative reference: the wall's plan
line (grid or offset). State diff: one placement field. Closure:
`HOSTS_VOID` → openings hosted by the wall (they are placed `along` the wall
line, so they move with it — no agent search), FILLS_VOID → frames/glazing,
MEETS at corners → the two adjacent walls' lengths (if they end on this
wall), ALIGNS_WITH → anything aligned to the wall's plane, CLEAR_OF →
counterparts now within clearance. Reference resolution: plan side — the
wall producer resolves the new line, the openings re-resolve along it, the
adjacent walls re-resolve their end points. Producers rerun: this wall,
its openings (through the wall's own producer), the two adjacent walls.
Geometry regenerated: those elements' operations revised; nothing retired.
Validators: corner MEETS, clearances, aperture bounds. Numerical error:
none at the host; a Type III risk only in the corner booleans if the walls
are joined by boolean union (they are not: walls are separate solids that
meet by shared reference).

In all three cases the cost is the closure's size, and the closure is
found by references, not by search.

## Integration with the current data model

No seventh table. Levels and grids are already reference *targets*
(datum-publishing entities); what is missing is the reference *type* on
elements and a resolver that reads it. Concretely: the entity schemas gain
reference-valued fields (`LevelRef`, `GridRef`, `HostRef`, `SupportRef`,
`OffsetFrom`), relations gain a `datum_role` and a `validator` binding
(P096 already has both at template level), and `Parameters` hold the
derived elevations as facts. A separate "reference layer" would duplicate
the datum records the kernel already resolves.

## Migration plan (each step keeps run-016 equivalence)

1. Reference resolver (plan side): `GridIntersection`, `OffsetAlongHost`,
   facade `AxisRef` → (x, z); wall and column-array producers consume it.
   Invariant: villa west band and Rocca bounds unchanged (references
   resolve to the coordinates the packs carried).
2. Stair bridge takes level references, resolves them from the datum
   record; `StairInterface.datum` becomes derived. Invariant: 23 risers,
   landing on piano-nobile within compile tolerance.
3. Support datums on every producer (column-array publishes column-top —
   done; entablature/roof/dome bind it — done for Rocca) and the
   `REVALIDATE` propagation rule attached to each SUPPORTS relation.
4. Retire the compensating literals: the 26 `± 0.0x` in the villa monolith
   become either a shared reference (21) or a declared ENGAGEMENT /
   SEPARATED_FROM parameter (5) when the villa is re-expressed as entities.
5. Remove datum rounding in authoring (mm rounding of datums in the seat
   scripts); keep rounding only at serialization (9 decimals) and let the
   compiler's tolerance be the compile tolerance, not a healing margin.

## Performance implications

Robustness: contacts hold by construction (already 80/80 in run-017);
healing passes and the 25 mm ruling disappear. Incremental regeneration:
an elevation change is a recompile without producer reruns; a plan change
reruns only the elements whose references changed. Dependency tracking:
references *are* the edges (a reference is a REQUIRES_REVALIDATION
dependency by definition), so the closure needs no separate bookkeeping.
Agent tokens: the agent stops reading `+0.02`-style compensations and
stops hunting for hosted objects (Case 3); it edits one field. Validation:
contact checks become tautologies for shared-reference pairs and can be
skipped by construction proof (record "held by construction: both bind
datum X"), leaving real checks (clearances, spans, kernel results).

## Paper implications

This is the paragraph behind position 4 ("dependencies participate in
generation"): SUPPORTS / HOSTS / MEETS / ALIGNS_WITH are inputs to
producers through datum roles and the reference resolver; CLEAR_OF refuses
placements; only kernel questions remain checks. The evidence already in
hand — run-017's 80/80 contacts, the refused attic windows, Rocca's
entablature landing on the cornice level with zero deviation after the
two-sources bug was removed — is the before/after the paper needs, and the
stage-5 audit is the "before".

## Do-not-do list

- Do not implement "semantic healing": no post-hoc snapping, no
  normalization of near-misses; a near-miss is a Type I bug to remove at
  the reference or a Type II relation to declare.
- Do not raise tolerances to absorb divergence (the 25 mm ruling must not
  return).
- Do not put a global tolerance in the relation vocabulary; put intervals
  on ENGAGEMENT / SEPARATED_FROM / CLEARANCE relations.
- Do not resolve elevation references inside producers (keep them
  symbolic to the compiler) and do not resolve plan references inside the
  compiler (keep the compiler geometry-neutral).
- Do not reimplement kernel operations (booleans, lofts, intersections).
- Do not add a seventh "reference table"; add reference-valued fields and
  a resolver.
- Do not let the stair solver keep taking numbers where references exist.
