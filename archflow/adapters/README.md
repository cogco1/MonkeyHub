# adapters

Adapters: where the kernel meets Rhino. The CAD program text (`cad_program`), its execution and export identity (`cad_execution`), the incremental patch path (`cad_patch`) and the 3dm readback (`three_dm_inspector`). Rhino is never the source of truth (ADR-002).

Modules and owners: `docs/SYSTEM_MAP.md` (rendered from `governance/module_registry.json`; one owner per capability). This README says what the package is for; it does not repeat the map.

Current owners: adapters.cad_execution, adapters.cad_patch, adapters.cad_program, adapters.three_dm_inspector.
