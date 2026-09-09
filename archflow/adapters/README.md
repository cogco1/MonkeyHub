# adapters

Adapters: shared interfaces to Rhino and OCCT. They cover CAD program text (`cad_program`), execution and export identity (`cad_execution`, with `occt_backend` also projecting named STEP shapes into visible/hidden polylines), incremental patches (`cad_patch`) and 3dm readback (`three_dm_inspector`). Drawing SVG/PNG expression belongs to `monkeydiagram.drawing_svg`. Rhino is never the source of truth (ADR-002).

Modules and owners: `docs/SYSTEM_MAP.md` (rendered from `governance/module_registry.json`; one owner per capability). This README says what the package is for; it does not repeat the map.

Current owners: adapters.cad_execution, adapters.cad_patch, adapters.cad_program, adapters.three_dm_inspector.
