# P073 — IFC semantic export

- Origin: Planning
- Status: Ready
- Depends on: P072

## Goal

Export a compiled program with its semantics as a deterministic IFC4
file through a single open-source library (IfcOpenShell) — the open
industry standard where semantics are first-class, so the semantic
geometry can be inspected in any BIM tool without ArchFlow present.

## Design

- Spatial hierarchy: project → site → building carrying the program's
  project and run identities; every physical object becomes an element
  named by its `object_id`.
- Element typing is data-driven: a caller-supplied mapping from
  component id to IFC class chooses the element class; unmapped
  components fall back to `IfcBuildingElementProxy`. The framework
  ships no building vocabulary of its own.
- Property sets retain binding ids, component id, commitment refs,
  evidence refs, producer op id, and program digest per element.
- Geometry maps parametrically where IFC expresses it exactly —
  extruded area solids for solids and extrusions, revolved area solids
  for revolves, boolean results for CSG — and lofts become faceted
  breps built from the exact ring vertices, recorded as a typed
  representation note. Arrays and radial arrays become one mapped
  representation reused under N transformed `IfcMappedItem` instances.
- Validation re-reads the exported file and compares element counts,
  names, property sets, and mapped-item multiplicities against the
  program; the receipt retains the file digest and library identity.
- The exporter owns no design authority and never repairs geometry.

## Stop conditions

- Stop if an unsupported construct would be silently approximated
  without a typed note.
- Stop if the export would require framework-owned building vocabulary.

## Tests

Hierarchy and naming, data-driven typing with proxy fallback, property
set round-trip, mapped-item multiplicity, faceted-loft note; exporter
determinism; architecture scope and discovery.


## Completion

- Completed: 2026-08-28
- Evidence: export_program_to_ifc authors deterministic IFC4 (content-derived GUIDs, no timestamps; same program => same file sha256). Monument program exported: 17 elements typed via caller mapping (IfcColumn x5 colonnade rows, IfcCovering x5 coffer rings, IfcWall drum+plinth, IfcRoof dome, IfcSlab portico, 3 proxies), 304 IfcMappedItem instances over 13 single-definition representation maps, drum-wall boolean chain EXACT parametric IfcBooleanResult (circle-profile extrusion operands, door void applied), dome lofts faceted from exact ring vertices with typed notes (ifc.loft_faceted, ifc.boolean_partial_representation for the unevaluated dome inner/oculus voids). Re-read validation VERIFIED (names==program physical set, psets on all elements, mapped multiplicities match); IfcExportReceipt@1 retained in monument-001 with file sha256 and ifcopenshell-0.8.5 identity. tests/test_ifc_export.py (8) green; archcheck PASS.
