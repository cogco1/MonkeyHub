# Authority-oriented package migration

This is a human-readable migration ledger for ADR-002.  It is not runtime
configuration, project state, a work-card acceptance record, or canonical
completion evidence.

## Completed ownership slices

| Former owner | Canonical owner | Compatibility rule |
| --- | --- | --- |
| geometry-local digest helpers used by new contracts | `archflow/contracts/canonical.py` | old domain helpers remain until their callers migrate |
| capability-owned evidence claim fragments | `archflow/evidence/claims.py` and `applicability.py` | new schemas only; no implicit upgrade of old records |
| capability evidence sufficiency | `archflow/evidence/sufficiency.py` | old capability path re-exports the same objects |
| capability visual evidence | `archflow/evidence/visual.py` | old capability path re-exports the same objects |
| capability stage evidence pack | `archflow/evidence/stage_pack.py` | pack remains an index with no closure authority |
| capability relation coverage | `archflow/evidence/relation_discovery.py` | discovery cannot define required relations |
| adapter-owned web snapshot schema | `archflow/evidence/sources.py` | adapter re-exports the schema and only performs I/O/extraction |
| capability precedent adoption | `archflow/research/adoption.py` | old capability path is a facade |
| capability branch research | `archflow/research/branch.py` | old capability path is a facade |
| capability research query | `archflow/research/query.py` | old capability path is a facade |
| mixed branch/unscoped basis index | `archflow/research/index.py` plus `research/compat/unscoped_v1.py` | branch path is canonical; unscoped path is read-only |
| capability architectural completeness | `archflow/validation/stage_completeness.py` | old capability path is a facade |
| capability spatial validation | `archflow/validation/spatial.py` | old capability path is a facade |
| capability component coverage | `archflow/validation/component_lineage.py` | old capability path is a facade |
| validation-owned program compiler | `archflow/compilers/voxel_program.py` | old validation path is a facade |
| runtime-owned commitment and brief compilers | `archflow/compilers/commitments.py` and `brief.py` | old runtime paths are facades |
| runtime-owned program, site, and resource compilers | `archflow/compilers/program.py`, `site.py`, and `resources.py` | old runtime paths are facades |
| runtime-owned neutral geometry compiler | `archflow/compilers/geometry.py` | old runtime path is a thin identity-preserving facade |
| state-owned convergence evaluator | `archflow/control/convergence.py` | old state path is a facade |
| capability material ledger | `archflow/materials/ledger.py` | old capability path is a facade |
| project-owned production checkpoint/transition use cases | `archflow/runtime/persistence/` | old project paths are facades; P036 remains writer |
| adapter-owned model and retrieval value contracts | `archflow/ports/model.py` and `retrieval.py` | adapters retain only concrete CLI/HTTP execution and thin re-exports |

New production-control contracts are owned by:

- `archflow/validation/contracts.py`: exact typed check envelopes;
- `archflow/control/requirements.py`: branch- and scope-bound requirement
  profiles with explicit denominators;
- `archflow/control/stage_closure.py`: composite closure compiled only from
  typed check receipts.
- `archflow/control/profile.py`: persistent authorization binding for the
  exact profile record, branch, stage, subject digest, and subject-inventory
  record/digest;
- `archflow/control/stage_subjects.py` and
  `archflow/runtime/stage_subject_inventory.py`: persistence-neutral
  `StageSubjectInventory@1` plus its exact `SpatialOptionProposal@2` /
  `ComponentIndex@1` join compiler; every semantic component covers every role
  in its selected baseline with an evidence- and authority-bound `REQUIRED` or
  `NOT_APPLICABLE` disposition;
- `archflow/control/baseline.py`: framework-owned minimum physical-check roles
  for pre-geometry, spatial, developed, and coordinated stage exits; evidence
  sufficiency remains the separate P079 control gate; spatial roles are
  recomputed from normalized validator inputs rather than accepted receipts;
- `archflow/validation/assembly.py`: exact support, containment, collision,
  opening-clearance, vertical-chain, and load-path relationships, plus a
  source-digest-bound complete subject manifest with per-role obligations and
  resolved relation candidates;
- `archflow/materials/binding.py`: exact semantic/component/material/object
  binding validation;
- `archflow/validation/cad_readback.py`: exact object/operation, layer,
  units, axis, envelope, and read-only preview contracts.

`advance_design_phase` now requires the independent phase gate, closed stage
convergence, the actual authorized `StageRequirementProfile`, its persistent
binding, a satisfied exact-current composite stage closure, and the
framework-compiled minimum-role coverage for the current phase.  None of
these substitutes for another.  In particular, an authorized profile that
omits a support/load-path or opening-clearance role cannot advance merely
because every check it chose to include passed.

`ProjectControllerArchiveAdapter` now separates historical branch epochs
before validating current checkpoint content, retains old checkpoint records
as read-only input, and requires an exact P036 stage-exit bundle on the first
checkpoint of a forward epoch.  That bundle binds the predecessor, authorized
profile, stage-subject inventory, exact component proposal and index, closure,
check receipts, and replayable framework baseline proof.  The adapter reads
them back, verifies the immutable P036 record SHA separately from each typed
semantic digest, mechanically recompiles the inventory, and recomputes
admission before retaining the new checkpoint.  The stage subject's
predecessor deliverable must cite that exact proposal record as an artifact or
evidence reference.  Current checkpoint records also retain the exact stage-
exit anchor and previous-checkpoint lineage throughout an epoch, and claim-
bound admission reads back claim, applicability, adoption, source, and
authority records.  A controller result without this archive proof is not a
durable phase exit.

This closes the later-stage synchronized-shrink attack: a runner cannot replace
the proposal, index, inventory, manifests, and requirements with smaller but
mutually consistent copies while claiming the same predecessor.  It does not
prove that a Stage 0 proposal originally declared every component required by
the typology, brief, RAG conclusions, or human design authority.  That genesis
admission remains separate.

## Runner boundary repairs

Production tools no longer import `tests` or insert the test directory into
`sys.path`.  Shared deterministic mechanics live under `tools/projects/`, and
tests import those mechanics instead of owning them.  The Pantheon support is
an explicit predecessor for the live and symmetric monument derivations; it is
not a generic building default.

The architecture checker now rejects:

- framework imports from tools, tests, docs, or probes;
- tool imports from tests;
- named project literals inside framework modules;
- the pre-existing duplicate-writer, unowned-write, probe-executable, and
  authority-leak conditions.

## Compatibility policy

A facade may only import and re-export the canonical objects.  It may contain
no copied implementation, fallback, writer, schema fork, or behavior switch.
Each migration freezes representative schema, payload, digest, and object
identity tests before production imports move.

Facades are removed only when supported callers reach zero.  Immutable records
retain their historical schema and are never rewritten in place.

## Remaining ownership work

The following are intentionally not declared complete by the current migration
slice:

- finish separating pure family, event, and reducer compilers from runtime
  lifecycle/orchestration code; the current family compiler is intentionally
  not moved while those responsibilities remain mixed;
- reorganize project-specific reconstruction entrypoints beneath
  `tools/projects/<project_id>/` while retaining thin old command shims;
- migrate Parthenon P087, its Stage 0-3 predecessor route, Pantheon formal
  mode, and other custom runners from caller-owned gates/direct record writes
  to the common controller/P036 stage-admission composition; retained HOLD
  runs remain explicitly outside that proof until then.  P087's retained
  legacy chain cannot be upgraded with a fabricated bridge: it needs an
  explicit human-authorized migration over exact retained
  `reconstruction-004`, or a new controller-native Stage 0;
- add the Stage 0 typology/brief/RAG/human-authority genesis-admission gate that
  proves the initial proposal's component universe is complete enough for the
  intended building; `StageSubjectInventory@1` prevents later silent shrinkage
  but cannot invent a component omitted at genesis;
- add a composition-level architecture/adoption gate so a future runner cannot
  claim a formal stage exit or COMPLETE evidence pack while bypassing common
  admission;
- mirror the new package boundaries in the physical test directory after
  import compatibility is stable.

These remaining items cannot be treated as completed merely because a target
directory exists.  Each requires production-import migration, fixed-contract
tests, architecture checks, and a zero-duplicate-implementation audit.
