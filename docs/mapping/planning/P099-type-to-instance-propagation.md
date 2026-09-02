# P099 — Type to instance propagation

- Origin: Planning
- Status: Ready after P092
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
