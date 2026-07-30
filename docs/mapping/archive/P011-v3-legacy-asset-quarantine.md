# P011 — V3 legacy asset quarantine and classification

- Origin: Planning
- Status: Done
- Depends on: P019

## Goal

Turn the frozen V3 repository into a bounded source of capabilities, evidence,
and oracles without allowing its V2 Pack kernel to become a V4 production
dependency.

This card begins a four-card legacy-assimilation cycle:

```text
P011 classify ownership
  -> P012 establish a quarantined CLI boundary
  -> P013 migrate one read-only diagnostic capability
  -> P014 enforce the ownership handover and block dependency backflow
```

## Write scope

- `governance/v3_legacy_manifest.json`
- `docs/migration/`
- `docs/mapping/`
- `tests/test_v3_legacy_manifest.py`

## Classification contract

Every inventoried V3 surface must receive exactly one disposition:

- `native_capability_candidate`
- `read_only_gate_candidate`
- `expert_heuristic_candidate`
- `building_dossier`
- `oracle_only`
- `forbidden_in_production`

The unit of classification is one responsibility, not one Pack directory.
A Pack that mixes facts, algorithms, defaults, geometry, and validation must
be split into separate manifest entries.

## Acceptance

- The live V3 retirement audit is captured with its repository/version
  fingerprint and current production-call counts.
- Each retained item records source, responsibility, current owner, proposed
  V4 contract, permitted side effects, target provider, evidence, and exit
  gate.
- V3 full composers, keyword-to-type routing, instance defaults, and globalized
  building answers are explicitly non-production.
- Gold, Red, probes, and failed cases are retained as evidence rather than
  copied into canonical state.
- One read-only, building-neutral pilot is selected for P013.
- No V3 source file is copied and no V4 production module imports V3.

## Tests

- Manifest schema, unique identity, and required-field validation.
- Every live audit surface maps to a named manifest entry.
- Forbidden production categories cannot name a native V4 provider.
- External paths and fingerprints are data, never import statements.

## Stop conditions

- Stop if classification requires changing V3.
- Stop if a whole Pack must be labelled as one indivisible capability.
- Stop if a building-specific answer is being renamed as a global law.

## Implemented boundary

- `governance/v3_legacy_manifest.json` binds the live retirement audit to its
  commit, dirty-worktree disclosure, audit digest, and responsibility owners.
- All eighteen composer, keyword-route, and legacy-adapter findings have one
  disposition; no whole Pack is treated as an indivisible migration unit.
- Full composers, production routing, adapter compatibility, and silent LLM
  fallback are forbidden in production. Project answers and generated cases
  remain dossiers or oracle evidence.
- `v3.gate.load_path_analysis` is the only selected P013 pilot candidate. It
  still requires the P012 process boundary and receives no V4 authority here.


## Completion

- Completed: 2026-07-28
- Evidence: Captured V3 commit, dirty-worktree disclosure, live retirement-audit digest, and responsibility-level manifest with exact ownership of all 18 composer, keyword-route, and legacy-adapter findings.
- Evidence: Classified composers, production routing, legacy adapters, and silent LLM fallback as forbidden; preserved project answers and generated cases as dossiers/oracles; selected only the building-neutral load-path analysis for a future read-only P013 pilot.
- Evidence: 6 focused tests and 292 full tests passed with 1 external smoke skipped; live V3 audit matched, architecture firewall passed 87 files, compileall and four-path scope check passed.
