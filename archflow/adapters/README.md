# adapters

Adapters: where the kernel meets Rhino and OCCT. The CAD program text (`cad_program`), its execution and export identity (`cad_execution`, with the in-process OCCT kernel path in `occt_backend` that also projects named STEP shapes into visible/hidden drawing polylines), the incremental patch path (`cad_patch`), the 3dm readback (`three_dm_inspector`) and the deterministic SVG/PNG serialisation of drawing polylines (`drawing_svg`). Rhino is never the source of truth (ADR-002).

Modules and owners: `docs/SYSTEM_MAP.md` (rendered from `governance/module_registry.json`; one owner per capability). This README says what the package is for; it does not repeat the map.

Current owners: adapters.cad_execution, adapters.cad_patch, adapters.cad_program, adapters.drawing_svg, adapters.three_dm_inspector.
