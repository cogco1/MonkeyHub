# P099 — Type to instance propagation

- Origin: Planning
- Status: Complete (2026-09-02)
- Depends on: P063, P091, P092

## Goal

Reuse is one type imported once, N instances placed, geometry derived
per instance. Today an instance only records which template it
selected; a new template edition does not reach its instances. Make the
instance's binding to a template edition a dependency edge, so an
edition change reopens exactly the instances' closure (P063 repair,
lifted to the type level), and let repeated instances of one type
compile to ARRAY / ASSET_INSTANCE operations and native CAD block
instancing instead of per-instance coordinates.

## Acceptance

- An instance record names template id and edition; promoting edition
  N+1 marks every instance of edition N `requires_revalidation` through
  the invalidation closure; unaffected instances are retained by digest.
- A window type placed eight times compiles to one definition and eight
  placements; the CAD translation carries one block definition.
- Full unittest suite and the architecture firewall pass.

## Write scope

- `archflow/capabilities/component_library.py`
- `archflow/compilers/geometry.py`
- `archflow/state/component_template.py`
- `tests/`
- `docs/mapping/`
- `governance/work_registry.json`

## Stop conditions

- Stop before an edition change mutates an accepted program silently.
- Stop before a type carries a project coordinate.

## Evidence (2026-09-02)

Kernel. `ComponentInstance@1` (`archflow/state/component_template.py`)
names template id, edition, template record, project, run and the
operation / object refs that realize it — never a coordinate;
`instance_edition_edge` turns the binding into a `DependencyEdge` with
effect REQUIRES_REVALIDATION from `template:<id>-edition-<n>` to
`instance:<id>`. `propagate_template_edition`
(`archflow/capabilities/component_library.py`) walks the P063-style
closure (INVALIDATES / REQUIRES_REVALIDATION edges, bounded) from every
older edition of the promoted template: instances of those editions and
everything downstream of them (the program they are realized in, its
stage receipt) are `reopened`; other templates and the same edition are
`retained` with their digests; a backward edition fails typed; nothing
rewrites an accepted program (`TemplateEditionPropagation@1` is a
record, not an edit). Placement: `OpeningRequest(count, step)` places a
type N times along the wall; the opening solver authors the members
once and emits one ARRAY per member (frames, pane, leaves), so the
assembly names the arrayed objects and the CAD translation carries one
block definition per repeated member with N instances. The wall keeps
one tool and one aperture per placement (booleans consume solids, not
block instances; the void is cheap, the members are what repeat).
Tests: `tests/test_type_instance_propagation.py` (9): record and edge,
closure through programs and receipts, supporting edges not crossed,
same / backward editions, recorded propagation, eight placements → one
definition, per-placement validation (the ninth placement leaves the
wall; placement 1 of an attic array behind the roof is refused), real
compiler acceptance with family bounds and five block definitions of
multiplicity eight.

Villa run-017 west band (`run_type_instances.py`, receipt
`typed-instances-receipt-1e49f9ab…`). The two ground windows and the
two principal windows are each authored once and placed twice (ARRAY,
step 15.100 m); the family bounds of every arrayed member equal the
union of its two run-016 pieces (max deviation 0.0 m); the envelope
seat accepted in one round with zero issues; the program carries 10
block definitions of multiplicity 2; the Rhino export read back with
10 instance definitions and 20 block instances. Seven window
placements and the wall became `component-instance` records bound to
the harvested candidates (`palladian-window-frame` edition 1,
`palladian-wall-with-openings` edition 1). A candidate edition 2 of the
window template (frame-depth band widened, unpromoted) was propagated
with realized-in-program edges: all 7 window instances and the program
record are reopened; the wall instance is retained by digest; the
accepted program is untouched (`template-edition-propagation` record).

Left open: irregular placements (no common step) stay one instance per
placement — a placement list op would lift that; propagation is not yet
wired into the design controller's stage revalidation (it produces the
duty, the controller does not consume it); promotion of the window
edition needs the second case (Rocca).



## Completion

- Completed: 2026-09-02
- Evidence: tests/test_type_instance_propagation.py (9); villa run-017 typed-instances-receipt-1e49f9ab: two window pairs placed by ARRAY (10 block definitions x2, Rhino read back), 7 window instances + wall recorded, edition-2 propagation reopens the 7 windows and the program, wall retained
