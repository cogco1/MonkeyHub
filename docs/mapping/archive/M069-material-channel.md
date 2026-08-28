# M069 — Material channel

- Origin: Modify (reconstruction-plan hard prerequisite: proposals
  carry palette refs but nothing downstream consumes them; material
  "correctness" must mean auditable assignment, not voxel texture)
- Status: Ready
- Depends on: P072, P073, P077

## Goal

Component-level material intent as typed, provenance-required records,
projected into the realizations that already carry semantics: the CAD
emission (layer color and user text) and the IFC export (property set
plus IfcMaterial association). A component without an assignment is a
typed `unassigned` entry — the material ledger is a coverage account,
never a texture claim.

## Design

- `MaterialIntent`: material id, label, non-empty source refs (adopted
  facts or evidence), optional display color — otherwise derived
  deterministically from the material id. The framework ships no
  material vocabulary.
- `MaterialLedger`: component-to-material assignments over declared
  intents; `ledger_coverage` joins against the program's bindings and
  reports assigned and unassigned components — silence is illegal.
- CAD: when a ledger accompanies translation, component layers take
  the material display color and every object carries
  `archflow:material` user text; the semantic round trip includes it.
- IFC: elements gain the material in their property set and a real
  `IfcMaterial` via `IfcRelAssociatesMaterial`.
- Demo: the symmetric monument ledger assigns only what can be cited
  (Roman concrete from the multi-source adoption; granite columns from
  the retained precedent evidence); every other component stays typed
  unassigned.

## Stop conditions

- Stop if the framework would own a material name or default.
- Stop if an unassigned component could pass silently.

## Tests

Ledger validation and provenance requirement, coverage join, CAD layer
and user-text projection, IFC material association; architecture scope
and discovery.


## Completion

- Completed: 2026-08-28
- Evidence: material.py: MaterialIntent (mandatory non-empty source_refs, deterministic display color), MaterialLedger (assignments only over declared intents), ledger_coverage joining bindings with typed unassigned and orphan lists. CAD projection: material layer colors + archflow:material user text, in the semantic round-trip expectation; IFC projection: pset entry + IfcMaterial via IfcRelAssociatesMaterial (re-read verified). Live monument ledger: granite (colonnade, citing the retained octastyle adoption quote 'large granite Corinthian columns') and roman-concrete (rotunda+dome, citing the research-006 multi-source adoption); 7 components honestly typed unassigned, coverage 30 percent; material CAD script sha retained; material IFC export VERIFIED into workspace exports. tests/test_material.py (7) green; suite 621; archcheck PASS.
