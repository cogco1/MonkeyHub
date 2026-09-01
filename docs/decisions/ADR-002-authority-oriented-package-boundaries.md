# ADR-002 — Authority-oriented package boundaries

- Status: Accepted for incremental migration
- Date: 2026-08-29
- Decision owners: ArchFlow framework governance

## Context

ArchFlow's protocol already distinguishes canonical state, branch-local design
state, evidence, geometry, validation, and promotion.  Its Python layout grew
incrementally from P001 through the reconstruction work, however, and now
mixes several authority levels inside the same packages:

- `capabilities/` contains expert surfaces, evidence contracts, validators,
  provider orchestration, and archive loading;
- `runtime/` contains orchestration as well as pure compilers and reducers;
- `project/` contains the P036 persistence owner together with higher-level
  production use cases;
- some adapters define durable domain schemas instead of only implementing an
  external port;
- project runners have reused test modules as runtime dependencies.

That layout makes an invalid dependency easy to write and difficult to see.
The directory tree must enforce the same authority boundaries as the runtime
contracts.  This is not a change to the P036 project envelope and is not an
authorization to move active project data.

## Decision

Stable framework responsibilities are owned as follows:

| Package | Sole responsibility |
| --- | --- |
| `contracts/` | canonical encoding, digests, schema primitives |
| `project/` | project identity, refs, layout, ports, repository, P036 writer |
| `state/` | immutable architectural, semantic, and geometry state |
| `evidence/` | sources, claims, applicability, visual evidence, sufficiency |
| `research/` | queries, adoption, branch selection, derived RAG indexes |
| `assets/` | asset provenance, licensing, units, axes, adoption |
| `materials/` | material intent, ledger, binding validation |
| `capabilities/` | capability registry and read-only expert surfaces |
| `compilers/` | pure deterministic state and geometry transformations |
| `validation/` | independent checks and typed check receipts |
| `control/` | requirement profiles, composite closure, convergence, phase decisions |
| `ports/` | protocols for model, retrieval, CAD, and external-world access |
| `adapters/` | concrete implementations of ports |
| `runtime/` | use-case orchestration, invocation, recovery, and P036-backed persistence |

Compatibility code stays next to its owning domain, for example
`research/compat/` or `project/compat/`.  There is no unbounded global legacy
package.  An old immutable record may be decoded and normalized in memory; new
writers emit only the current schema.

The dependency direction is:

```text
runtime
  -> control
  -> validation / compilers / capabilities
  -> state / evidence / research / contracts

adapters -> implements ports
all durable project writes -> project.repository / P036
```

Lower layers may not import a higher layer merely to reuse a concrete class.
Shared contracts move down to their lowest truthful owner.  In particular, a
validator cannot import a runtime compiler type, and a durable evidence schema
cannot be owned by an HTTP adapter.

## Stage closure

Stage acceptance is requirement-first:

```text
exact retained SpatialOptionProposal + ComponentIndex
  -> StageSubjectInventory
authorized source
  -> adopted claim
  -> exact claim applicability
  -> StageRequirementProfile
StageSubjectInventory + StageRequirementProfile
  -> independent CheckReceiptEnvelope records
  -> CompositeStageClosureReceipt
  -> convergence and phase gate
  -> runtime asks P036 to persist
```

Universal mathematical and geometric checks do not need fabricated RAG
evidence.  Claim-bound checks must cite the exact claim, applicability,
adoption, source, and authority records that permit the tested use.  A check
receipt binds the exact branch, scope, subject digest, subject refs, and
coverage denominator.  A caller-supplied `passed` boolean or summary flag
cannot close a stage.

The controller also compiles a framework baseline from the actual authorized
profile at phase exit.  The baseline rises from spatial to developed to
coordinated assurance and makes the following generic roles non-optional at
the appropriate boundary: component lineage, spatial envelope, assembly
relationships, opening clearance, load path, material binding, and CAD
readback.  A typology may add stricter checks but cannot delete these roles.
Where a role truly does not apply, the profile must carry a typed,
authority-bound applicability requirement; omission and a caller-authored
summary are not equivalent to not-applicable.

The denominator is not supplied by whichever checks happened to run.
`StageSubjectInventory@1` is mechanically compiled from the exact retained
`SpatialOptionProposal@2` and `ComponentIndex@1`, which must expose an identical
semantic-component set.  Each component must carry a disposition for every
role in the selected framework baseline.  Both `REQUIRED` and
`NOT_APPLICABLE` retain evidence and authority; `REQUIRED` also names exact
targets, while `NOT_APPLICABLE` names none.  The compiler joins exact identities
and verifies complete dispositions; it does not infer applicability from a
component name or grant design authority.

A typed assembly coverage manifest binds its subject universe to that exact
stage denominator, gives every listed subject role a required relationship or
an authority-backed not-applicable disposition, and accounts for discovered
relation candidates.  Spatial baseline sources retain normalized validator
inputs, not a caller-owned result receipt, so the baseline compiler can rerun
the check.  Durable phase exit additionally requires P036 readback of the
exact predecessor and the persisted proof bundle; a pure controller return is
still only an in-memory candidate until that boundary succeeds.

P036 keeps storage identity and semantic identity separate.  The
`ProjectRecordRef.sha256` covers the immutable record bytes, while each typed
proposal, index, inventory, binding, and receipt has its own schema-defined
semantic digest.  Admission verifies both, recompiles the inventory from the
read-back proposal and index, and requires the stage subject's predecessor
deliverable to cite the exact proposal record as its artifact or evidence.
Re-authoring a mutually consistent but smaller proposal, index, inventory,
manifest, and requirement set therefore cannot detach the exit from its
predecessor denominator.

For a claim-bound durable exit, P036 readback covers the complete five-part
basis chain—claim, applicability, adoption, source, and authority—not merely
the final source and authority records.  Later checkpoints in the same epoch
must inherit the exact stage-exit anchor and previous-checkpoint lineage; a
structurally valid high-stage checkpoint with no anchor fails closed.

The inventory prevents a later stage from silently shrinking the exact
retained semantic proposal.  It does not prove that the initial Stage 0
proposal contained every component required by the typology, brief, RAG
conclusions, or a human design decision.  Stage 0 genesis completeness is a
separate authority-bound admission problem; the inventory cannot invent a
missing roof, load-bearing system, opening, or circulation element.  That
limitation must remain visible and fail formal adoption, not be hidden by
calling a mechanically complete inventory a complete building design.

This does not make every check a RAG check.  Physical invariants such as
object identity, non-collision, exact readback, coverage equality, and graph
reachability are universal.  A researched proposition enters a check only
through the exact claim/applicability/adoption/source chain.  This separation
prevents both ungrounded geometry and fabricated citations for facts that are
mathematical rather than historical.

Evidence packs, progress snapshots, dashboards, and previews are projections.
They may index or display authoritative receipts but cannot become acceptance
or canonical-write authorities.

### Adoption boundary

The preceding rules describe the mandatory `DesignController` admission
contract.  They do not retroactively make every project script compliant.
Current custom Parthenon and Pantheon HOLD runners still use their retained
project-local gates and direct P036 record writes, so they can bypass this
controller path.  They must not be described as having generic baseline or
durable stage-exit proof until their composition roots are migrated.  Existing
Stage 0-3 histories cannot be made compliant by inventing controller
checkpoints after the fact.  P087 requires either an explicit human-authorized
migration contract over exact retained `reconstruction-004` or a controller-
native Stage 0; a fabricated bridge is not admissible.

## Repository-root ownership

- `archflow/` contains reusable installed mechanisms only.
- `tools/projects/<project_id>/` contains thin local composition roots and
  project-specific profiles; a reusable product capability cannot exist only
  there.
- `tests/unit/` mirrors framework packages.
- `tests/projects/` tests project profiles and runners.
- `tests/integration/` proves cross-package flows in temporary P036 roots.
- `tests/architecture/` enforces dependency and persistence firewalls.
- `probes/` remains promoted data and evidence, never executable code.
- `docs/` remains human-readable governance, never runtime configuration or
  current-state authority.
- active project records remain under their configured external P036 project
  root.

Project-specific dimensions, component IDs, operation IDs, source selections,
historical interpretations, and human decisions never move into `archflow/`.
Generic assembly, relation, spatial, readback, material-binding, and evidence-
applicability mechanisms do.

## Architecture gates

The existing architecture checker is extended rather than duplicated.  It
must reject:

- `archflow/**` importing `tools`, `tests`, `docs`, or `probes`;
- `tools/**` importing `tests`;
- project identifiers and project-only material or work-card literals inside
  `archflow/**`;
- executable code inside probes;
- non-P036 durable project writers;
- docs or relocation anchors acting as runtime current-state authority.

Generic architectural vocabulary such as column, wall, door, roof, marble,
support, and circulation is not a project literal and remains legal.

## Migration policy

Migration is replacement-first and incremental:

1. establish architecture gates and remove production-to-test imports;
2. introduce canonical claim, applicability, and check-receipt contracts;
3. move one implementation at a time and leave a thin old-path re-export;
4. migrate production imports to the new owner;
5. verify replay, schema, digest, and target tests;
6. remove the facade only after the import graph has zero supported callers.

Large mechanical moves are forbidden while unrelated dirty work overlaps the
target files.  Canonical `HEAD`, external project state, and retained immutable
records do not change as a consequence of package migration.

## Consequences

- Import direction becomes mechanically enforceable rather than conventional.
- Once its composition root adopts the common admission route, a new building
  type contributes project data and profiles instead of a new framework branch
  or validator suite.
- RAG evidence can be audited at each parameter, relation, component, and
  check rather than merely attached to a stage pack.
- Existing import paths remain usable during bounded migrations.
- Physical reorganization takes several verified slices; declaring this ADR
  does not by itself prove that every legacy caller has migrated.

## Rejected alternatives

- **Big-bang directory move:** obscures behavioral regressions and conflicts
  with active dirty project work.
- **One global `legacy/` directory:** removes ownership context and tends to
  become a permanent fallback path.
- **Project-specific checks in the framework:** turns one reconstruction into
  defaults for future buildings.
- **Evidence-pack acceptance:** an index cannot prove that its claims were
  applicable to, consumed by, or measured against the realized artifact.
