# Bringing the research back in: from retained RAG records to the atlas board

2026-09-03. Kaiwen: the earlier research (RAG) results must not be lost; the codex skill
`build-lineplus-research-atlas` has a board format; do not build the board, say how the research
connects. Facts first, then the approach.

## What exists

**Retained, not lost.** The villa project already holds the research as shared containers, written by
the archived lanes and still readable through P036:

| run | records |
|---|---|
| research-001 | research-source-ledger, architectural-evidence-claims, uncertainty-register, reconstruction-parameter-seed |
| research-002 | stair-source-ledger, stair-parameter-proxy-ledger |
| research-003 | controlled-source-ledger (6 sources: canonical_url, roles, retained_object_sha256), visual-evidence-inventory (VisualEvidenceInventoryReceipt@1: 2 source images, 7 ROIs, 7 observations, 7 component hypotheses, coverage), source-document-binding |
| research-004 | stage-5-material-evidence-pack x5, stage-5-material-evidence-source x20, glazing evidence |
| research-005 | underpass source ledger, visual claim set, facade-orientation reconciliation |

82 content-addressed objects (photos, documents) sit in `objects/sha256/`. The producers
(`visual_inventory.py`, `stage_control_sources.py`) are in `archive/`; the records are data and stay.

**Unreferenced.** The WIP record cites three `evidence:` refs at record level and none on any of its 96
entities. `evidence_refs` are free strings: the record validates entity references and
`relationship_refs` (batch 1) but not evidence, so a dangling evidence ref is silent.

**Invisible.** The Studio's Evidence drawer has three tabs (honesty, receipts, events): what the server
said, verbatim. Nothing shows research.

**The skill's format.** An evidence entry is `id, mechanism, case, fact, spatial_result, url` plus
`status` (confirmed / reported / calculated / inferred), `visibility`, `source_type`,
`quote_or_locator`, `image_source`. Case cards, A3 SVG sheets and the HTML case library are rendered
from `research.source.json` / `cases.source.json` by `scripts/build_svg_pack.py`. The retained
ledgers carry a subset of these fields under other names, and the visual inventory carries what the
skill lacks: ROIs with pixel regions, observations with `evidence_aspects` (existence, morphology,
relative_position, topology) and the component each supports.

## Approach: one ledger, two imports, one export, one tab

1. **The ledger is the record's vocabulary for evidence** (kernel, one owner:
   `archflow/state/evidence.py`). A `research-evidence-ledger` record (EvidenceLedger@1), one entry
   per evidence id: id, kind (source / image / roi / observation / hypothesis / material), url or
   retained object ref, status (the skill's four words), aspects (the inventory's four), the
   component or role it supports (resolved through the semantic registry, ADR-006, so
   `portico-columns` lands on `role.structural_support` + the element), and `since` (snapshot date).
   `StateRecord` validates every `evidence_refs` entry, record- and entity-level, against the ledger
   named in `basis_refs`, exactly as `relationship_refs` are validated against declared relations.
   Elements without evidence are not refused; they are reported (`unsupported`) the way unchecked
   relations are.

2. **Import A: the retained research** (`archflow/capabilities/evidence_import.py`). One reader over
   the five research runs' record kinds above, producing the ledger into a new research run
   (Shared, S1). Villa gets its ledger from what it already has; nothing is re-searched.

3. **Import B: the atlas skill's JSON** (same module, second input, same output). `research.source.json`
   `evidence[]` maps field for field; `image_source` is ingested into `objects/` through the
   repository's `ingest` and referenced by sha. So a future study made with the skill enters the
   project as evidence, not as a folder beside it.

4. **Export: the ledger back out as the skill's input** (`tools/export_research_atlas.py`). Writes
   `research.source.json` + `cases.source.json` and copies the referenced objects, into
   `exports/research-atlas/<date>/` (the layout's row for non-authoritative share packages). The
   codex skill renders the A3 sheets and the case library from there; an `research-atlas-export`
   record names the export and the ledger digest it came from. The board stays the skill's job.

5. **Studio: a fourth tab, Research.** Ledger entries grouped by element; ROI crops served from
   `objects/` by a read-only artifact route (the API already serves `.3dm` artifacts); picking an
   element shows its evidence and an unsupported element says so; the rendered A3 sheets, when an
   export exists, are linked from the same tab. The tab reads the ledger record, never the
   archived lanes.

## Where it sits in the ladder

Evidence is the shared container of the `research_brief` phase (RIBA 0/1, LOD 500 for a monument,
since the as-built is the evidence). The runner's stage-0 closure can then require
`evidence-coverage` (every element with `roles` of structural support or weather enclosure cites at
least one confirmed or reported entry), which is the one research check the spine can measure.

## Order

After Wave B (the record-kind table registers the ledger kind; the containers module lists research
runs) and after the Studio's pick/highlight fix. One worker for 1 and 2, one for 4, one for 5; 3 is a
day's addition to 2 once a skill study exists to import.
