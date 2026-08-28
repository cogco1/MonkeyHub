# P061 — Parametric assembly and external mesh family protocol

- Origin: Planning
- Status: Ready
- Depends on: P060, P047, P049, P051, P055, P057

## Goal

Unify reusable parametric assemblies and content-addressed external mesh
families behind one exact-project semantic component protocol without
pretending that an imported mesh has editable parametric geometry or adding a
second component tree.

## Write scope

- `archflow/state/component_family.py`
- `archflow/state/geometry_program.py`
- `archflow/state/__init__.py`
- `archflow/state/README.md`
- `archflow/runtime/family_compiler.py`
- `archflow/runtime/geometry_compiler.py`
- `archflow/runtime/semantic_geometry_lifecycle.py`
- `archflow/realization/sandbox.py`
- `tests/test_component_family_protocol.py`
- `tests/integration/test_component_family_realization.py`
- `probes/p061-component-family-protocol/`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- One typed family instance binds an existing `DesignComponent` id, exact
  project/run/base, family revision, local frame, anchors/sockets, native unit,
  scale, interfaces, dependencies, and provenance without creating another
  semantic ownership tree.
- Parametric assemblies reference only the existing generic geometry
  operation and hosted-assembly vocabulary. Project records own parameter
  names, values, ranges, and adaptation decisions; the framework owns no
  column, window, ornament, or building-type template.
- External mesh families require stable content identity, media type, native
  unit, declared sockets, and provenance. Placement and bounded scale are
  explicit; internal mesh geometry is immutable inside ArchFlow and cannot be
  misrepresented as parametric.
- Parametric, mesh, and hybrid family instances publish the same semantic
  binding, anchor, interface, dependency, and realization receipt surface.
  Missing assets, sockets, units, interface targets, or content digests fail
  before realization with typed local evidence.
- Exact-predecessor family revise, replace, and retire operations participate
  in the existing P055 semantic-geometry lifecycle. Dependency invalidation is
  local, preserved siblings retain identity and object digests, and no family
  operation writes project HEAD or bypasses candidate review.
- Two non-isomorphic project fixtures and a fresh P036 probe prove parametric
  and mesh realization, reload, local replacement, and honest loss/authority
  boundaries without a platform exporter.

## Tests

- Exact schema, digest, provenance, unit, scale, anchor, socket, interface, and
  semantic-owner validation.
- Parametric, mesh, and hybrid realization plus missing-asset/socket and
  immutable-mesh negative cases.
- Exact-predecessor revise/replace/retire, local dependency invalidation,
  preserved-sibling identity, and stale-result rejection.
- Two non-isomorphic projects, P036 probe reload, instance-literal scan,
  architecture firewall, V3 boundary, scope, diff, and full discovery.

## Stop conditions

- Stop before adding a framework-owned architectural family, default family
  parameter, component hierarchy, building answer, or platform-specific block
  or CAD object.
- Stop if an external mesh is treated as internally parametric, if its content
  identity can change behind a stable family revision, or if scale is inferred
  without an explicit native unit.
- Stop if family records become another component tree, persistence writer,
  geometry authority, candidate reviewer, or canonical promotion path.
- Stop before implementing Minecraft, Rhino, Revit, schematic, or other
  platform exporters; P061 ends at the neutral sandbox receipt.


## Completion

- Completed: 2026-08-17
- Evidence: Implemented ComponentFamilySet@1 as a project-authored annotation over the sole SpatialOptionProposal.components tree, with parametric assembly, immutable external mesh, and hybrid modes; exact component/geometry/parameter/asset/unit/socket/anchor/interface/dependency/provenance compilation; ComponentFamilyRealizationReceipt@1 binds the exact compiled family objects to one sandbox scene and receipt; exact P055 revise/replace/retire lifecycle rejects stale receipts and unacknowledged local dependency impact while preserving independent sibling identity. Fourteen focused tests pass; full discovery passed 567 tests with 3 explicit opt-in skips; ARCHITECTURE PASS 120 files; V3 boundary PASS 120 production files and 2 frozen oracles; P061 scope, diff, compileall, P036 reload and retained-probe integrity pass. Probe realization claims are project://p061-component-family-protocol/runs/parametric-001/records/component-family-realization-claim-boundary-9e190f8d69a0182dc2001386653eacc7895468bc700d417dc1b3904a16c4a164.json and project://p061-component-family-protocol/runs/mesh-001/records/component-family-realization-claim-boundary-05b37b1841a73833f01a47958580c8e41adb38fe9323595c661f549484c78537.json; exact replacement claim is project://p061-component-family-protocol/runs/lifecycle-001/records/component-family-replacement-claim-boundary-1aa681a7ff53237a0438ee5733e11515400a06ecd7546e7ab68f19bb9149d1de.json. No architectural family catalogue, platform exporter, geometry-generation authority, persistence authority, review authority, or canonical-write authority was added.
