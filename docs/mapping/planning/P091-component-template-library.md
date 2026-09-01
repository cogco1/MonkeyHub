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
