# P091 — Component template records and library

- Origin: Planning
- Status: Ready after P090
- Depends on: P050, P090

## Goal

Define the component-template record kind and its promotion path so that
converged project components become citable, parameterized inputs for
future projects. The anatomy follows the run-014 underpass recovery
triple — representation / applicability / load path — generalized to
mathematics reference, applicability domain, and interface obligations.
Parameters are module-bound ratio bands with per-value evidence refs,
organized as a treatise page: plate witness, numbers, usage notes,
sources, edition lineage. Templates are project records first; a
component-import flow (basis-import precedent) promotes accepted
templates to a library store; P050's `available_template_records`
supplies them at authoring time with receipted selection. Reuse is
re-derivation, never copying: geometry rebakes per project and evidence
re-binds in the receiving project.

## Acceptance

- The template record schema validates the treatise five elements plus
  interface obligations (MEETS/SUPPORT duties, INTERSECTS exclusions,
  CLEARANCE intervals) expressed against P090 datums.
- The stair family is the first harvest: a stair template record derived
  from the existing stair-solver path is selected through P050 with
  receipts and realized on a test project.
- component-import moves a template across projects with provenance
  intact and local evidence re-binding; selection outside the supplied
  records stays a typed failure.
- The two-vote rule — a ratio enters the library only after surviving
  two non-isomorphic cases — is enforced as a typed guard or a recorded
  waiver.

## Write scope

- `archflow/state/`
- `archflow/capabilities/`
- `archflow/project/`
- `tools/`
- `tests/`
- `docs/mapping/`
- `governance/work_registry.json`

## Tests

- Template schema validation including negative paths (bare numbers
  without evidence refs fail).
- Stair-template harvest, selection receipt, and realization round trip.
- Cross-project import with evidence re-binding.
- Architecture firewall.

## Stop conditions

- Stop if any template default lands in framework code — templates carry
  citations or typed open boundaries, never bare numbers.
- Stop before granting the library store any acceptance or
  canonical-write authority.


## Completion

- Completed: 2026-09-01
- Evidence: ComponentTemplate@1 treatise-page contract (module + ratio-band/count/expression parameters each carrying basis refs - bare numbers fail typed; MEETS/SUPPORTS/HOSTS_VOID/FILLS_VOID/INTERSECTS_FORBIDDEN/CLEARANCE/ENGAGEMENT obligations against P090 datum roles; plates, applicability, editions, case votes). Library flows: harvest/promote/import with two-vote guard (waiver path recorded) and mandatory evidence rebinding on import (incomplete rebinding fails typed, provenance travels unchanged). P050 catalog selection receipted end-to-end with a scripted provider; out-of-catalog selection cannot be accepted. First live harvest: palladian-exterior-stair from villa reconstruction-016 promoted into the new component-library P036 project under recorded waiver project://villa-rotonda-reconstruction/runs/reconstruction-016/records/component-two-vote-waiver-29f54769....json; template digest 57f1489e identical in source and library. 13 new tests; suite 1524 passed; ARCHITECTURE PASS (293 files)

## Acceptance delta (recorded 2026-09-01, post-completion self-review)

- "realized on a test project": at completion the receipted-selection
  test asserted the receipt only. `test_catalog_selection_is_receipted`
  now also calls `realize_geometry` on the accepted program.
- "interface obligations … expressed against P090 datums": obligation
  `datum_role` fields were carried but never resolved. `verify_template_datums`
  (state/component_template.py) now reports an unbound role or a role bound
  to an unpublished datum as a violation; `mathematics_ref` is resolved at
  harvest by M090.
