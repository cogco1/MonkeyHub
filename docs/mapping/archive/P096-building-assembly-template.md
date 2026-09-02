# P096 — Building assembly template: the typology layer

- Origin: Planning
- Status: Ready after P095
- Depends on: P091, P093, P095, M096

## Goal

Add the missing layer between generic building semantics (relation
predicates, obligations) and component templates: a
`BuildingAssemblyTemplate@1` library record that stores component
roles, parent structure, cardinality rules, functions, relation
direction with the datum each relation carries, phase of appearance,
required datums and checks, and component-template references — and
never an absolute coordinate. A project consumes a template through an
`AssemblyTemplateBinding@1` record (roles to project components,
datum roles to project datums, project-derived cardinalities with
evidence), so reuse is re-derivation, not copying a model. A
programmatic harvester builds the first candidate,
`centralized-palladian-villa`, from the villa's retained design state
and run-017 seat datums; it enters the library as a candidate under the
existing two-vote rule with an OPEN second-case obligation, not as
promoted truth.

## Acceptance

- The template schema refuses absolute coordinates, unknown parent or
  relation roles, unknown predicates, untyped datums, and a
  project-derived cardinality without a parameter basis.
- Roles form a tree; relations name the datum role they carry; every
  required datum names its publishing role.
- The binding fails typed when a role is neither bound nor declined,
  when a cardinality is not met, or when a datum role is unbound.
- The villa harvest yields a candidate with one case vote, zero
  numeric coordinates, roles for the whole retained tree, relations from
  the run-017 seat datums and the villa relation ledger, and an OPEN
  second-case obligation in the library.
- Full unittest suite and the architecture firewall pass.

## Write scope

- `archflow/state/assembly_template.py`
- `archflow/capabilities/assembly_library.py`
- `tests/`
- `docs/mapping/`
- `governance/work_registry.json`

## Tests

- Schema invariants and coordinate refusal.
- Binding completeness and cardinality.
- Harvest determinism from a retained design state.
- Architecture firewall.

## Stop conditions

- Stop before a typology name or constant enters kernel code; typology
  lives only in library records.
- Stop before a template is promoted with a single case vote.
- Stop before a binding mutates a template.


## Completion

- Completed: 2026-09-01
- Evidence: BuildingAssemblyTemplate@1 (state/assembly_template.py): roles with parent, function, fixed or project-derived cardinality (COUNT band with basis), first phase, indexing, component-template family and required datum roles; relations over ArchitecturalRelationKind naming the datum role each carries; typed required datums with publishing role; required checks; case votes; open boundaries; the schema refuses floats and numeric vectors outside module-ratio bands, unknown parents, unknown roles, undeclared datum roles, and project-derived counts without a COUNT parameter. AssemblyTemplateBinding@1 + bind_assembly_template fail typed on unbound-undeclined roles, unmet counts, and unbound datum roles whose publisher is bound. assembly_library: roles_from_design_state, relations_from_datum_bindings (SUPPORT relations a project's datum bindings already prove), harvest, candidate with OPEN second-case obligation, promotion under two votes or waiver, binding record. Villa harvest (run-017): candidate centralized-palladian-villa d08f9545 with 42 roles (41 retained + indexed portico-side), 37 relations (11 derived from run-017 bindings, 26 declared with evidence), 18 datum roles, 31 checks, 3 COUNT parameters, one vote, 5 open boundaries, zero floats; library candidate 8b3f22ba, second-case obligation b2ed230d OPEN, villa binding d62762d3. 8 tests; full suite 1691 with the two known Windows flakes only (both pass alone; hook test passes on a committed tree); authority-literal ratchet held; ARCHITECTURE PASS (297 files).

## Live evidence (recorded 2026-09-01, after completion)

Second case bound the same night: **Rocca Pisana** (Scamozzi, Lonigo
1574–76), project `rocca-pisana`, run `binding-001`. Extracted web
statements (it.wikipedia, wga.hu) recorded as `web-evidence-extract`;
a 24-component schematic tree; `assembly-template-binding-3d1fc186…`
binds 23 roles, declines 19 with reasons (lantern, attic, mezzanine,
spiral stairs, under-stair passages, …), sets portico-count 1 with the
south index and columns-per-portico 6, and names the datum ids the
project commits to publish. Library `assembly-template-comparison-
9964dcb9…` records stable / villa-only / rocca-only (basement, dome,
serliana window type). Edition 2 (`assembly-template-0f117a22…`,
promotion receipt ae18c0c1…) makes every villa-only role optional
(0..1 count bands) and portico-count a 1..4 band; the second-case
obligation is closed (`assembly-second-case-closure-b4305f6c…`).
Vote kind stated on every record: organisation-level binding; Rocca's
geometry is not derived. Binding rule amended: a project-derived
indexed role may bind a subset of the template indexing.
