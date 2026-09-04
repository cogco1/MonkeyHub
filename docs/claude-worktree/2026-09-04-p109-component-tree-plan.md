# P109 — the component tree is the source of truth for editing; Villa Rotonda moves onto it

**Status:** design + execution ledger, written 2026-09-04 by the Studio lane (Fable) on Kaiwen's
direct instruction: audit, then implement; stop only where a fact cannot be determined and the choice
changes the result. Audits: `.superpowers/sdd/audit-studio-architecture-2026-09-04.md`,
`.superpowers/sdd/audit-villa-lineage-2026-09-04.md`.

## 0. Root cause, in one paragraph

The Studio reads `input/runner/state-record.json`: 96 entities, 41 `Component@1`, 4 `Element@1`,
0 parameters, 14 relations. Editing is defined as "a numeric param of an Element@1 row"; the intent
grammar can only move a scalar that already exists, and the agent's record sheet lists the four
elements with their numeric fields. So every sentence about a component without an element
(`portico-columns`, `portico-roofs`, 37 of 41 components) has nowhere to land except the one element
that has a `height` — `portico-roof-abutment-west` — and the agent picks it because the sheet offers
nothing else. The model the architect sees (~1120 objects, 24 columns, 8 portico roofs, 4 abutments)
was produced by the old whole-building scripts (run-012 stage 1–4 `.archflow.py`, 1 056–5 968 lines;
run-016 datum migration) whose objects carry `archflow:component` and `archflow:producer_op` but no
`Element@1` identity; run-017 rebuilt the west band on the new spine (a wall element with solved
openings, four portico seats authoring geometry programs directly from run-015/016 extents) without
authoring the rest as element rows. Clarifications keep no state: a question is a 422 with a sentence,
and the next sentence starts from the old selection. Old chain: script → program → 3DM, identity by
object name. New chain: `StateRecord` → `Element@1` rows → `element_producers` → geometry program →
CAD export → inspection; identity is the element id, objects are `obj-<producer op>`.

## 1. What already exists (EXTEND, never a parallel)

| need | owner today | what is there |
|---|---|---|
| component tree | `archflow.state.state_record` (`design_components_of`, `Component@1.parent_id`) | tree, semantic kind (registry-checked), intent, maturity |
| realizations / elements | `Element@1` rows: `component_id`, `producer`, `references`, `params`, `basis_refs` | producers: column-array, capitals, beam, pediment, wall, prism, ring, loft, dome-cap, declined |
| object refs / operation refs | export user strings `archflow:component`, `archflow:producer_op`, `archflow:object_ref`, name `obj-<op>`; `seat-3dm-inspection` records | pick resolver maps `obj-<elementId>[-…]` to the element of the claimed component |
| geometry binding | `references` (levels, grids, `<id>-top` datums), `compilers.geometry` resolves datums | `element_producers` publishes `<id>-top` |
| editable capabilities | `params` scalars (`numeric_fields` in the projection) + `Parameter` (value/unit/lock) | no per-capability source/confidence/status/validator |
| dependencies | `StateRecord.dependency_edges()` = relations (`support_contact`, `clearance_interval`, `aperture_exists`) + entity references (`host`, `level`, `datum`, …); `closure()` | one propagation rule (INVALIDATES / REQUIRES_REVALIDATION) |
| validation | `capabilities.relation_checks`, `studio.validation` five clauses | |
| clarification | `BlockedNeedsHuman{question, acceptedForms}` | no pending state |
| agent | `studio.intent` compilers on `ports.model`; closed JSON `{status, targetComponentId, elementId, utterance, why, question}` | no closed action vocabulary; server re-types the sentence only |

No new kernel schema is needed for identity or dependencies: an `Element@1` row is the realization, its
`params` are the capabilities, relations and references are the edges. Three things are genuinely
missing and get the minimum typed form:

1. **Capability facts beyond the scalar** — source, confidence, status, validator refs. Kept *beside*
   the record as a derived catalog (the record stays the truth of the value; the catalog says where the
   value came from and whether it is editable). Record kind `component-catalog` (new; reason: no kind
   describes object ↔ element ↔ capability coverage; `catalog-confrontation` is an archived lane's
   kind read by nobody on the spine).
2. **Pending intent** — short-lived, in-process (like `ProposalStore`), carried on the 422 body and
   resumed by a continuation token. Not a record: it is not history.
3. **Declared control proposal** — a proposal whose successor record *adds* an `Element@1` row (or a
   `Parameter`) with provenance; runs as a candidate like any proposal; never written to the authored
   record by the studio.

## 2. Tasks (ledger — tick as landed, name the commit)

### A. Component tree and capability projection (studio.binding + studio.shell + web)
- [ ] A1 `application/catalog.py`: `Catalog` derived per projection from the StateRecord (elements,
      params, relations, references) and the reference run's `seat-3dm-inspection` records (objects,
      `archflow:*` strings): per component → descendant elements, capabilities (key, value, type,
      unit=null for element params, bounds when declared, source=`authored|derived|reindexed`,
      confidence, status=`editable|derived|locked|missing`, validator refs), object coverage
      (objects bound to an element / visible-but-unbound), dependency closure; per object →
      binding status (`bound`, `MODEL_VISIBLE_CATALOG_MISSING`, `AMBIGUOUS`).
- [ ] A2 DTOs: `ComponentNodeDto` gains `children`, `elementIds`, `capabilityCount`, `states`,
      `objectCount`, `unboundObjectCount`; `GET /api/state` carries `catalog` (components, elements
      with capabilities, unbound objects); `POST /api/pick/resolve` answers `MODEL_VISIBLE_CATALOG_MISSING`
      for a bound-less object; registry entries first.
- [ ] A3 Web: a nested component tree panel (fold/unfold, counts, states); choosing a component with
      one editable descendant lands on it; several → candidates with value and side; none → "missing
      binding", no numeric editing. Retire the flat `SelectionPicker` list (one mechanism in, one out).

### B. Two-stage intent resolver (studio.intent)
- [ ] B1 `application/resolver.py`: `resolve_target(request, projection, catalog)` — priority explicit
      elementId → object binding (pick/gesture hits) → component tree (single editable descendant /
      candidates / none) → semantic alias (registry aliases + project naming conventions) → camera or
      gesture direction (only with a camera). No string-similarity guessing across components.
- [ ] B2 Action vocabulary: closed typed result `Command | Clarify | DeclareControl | Unsupported`
      (`reasonCode`). The agent's JSON schema becomes this vocabulary; the server re-validates every
      id and key against the catalog before anything is typed. Qualitative amounts without a number →
      `Clarify(missingSlots=[amount])`, never "10 %".
- [ ] B3 `MISSING_ELEMENT_DECLARATION` / `UNSUPPORTED_ADD_FIELD` are terminal: the answer names the
      authored control that has to exist and offers `declare_missing_control`.

### C. Clarification with state (studio.intent + studio.shell + web)
- [ ] C1 `PendingIntent` + `PendingIntentStore`: stateDigest, artifactDigest, originalUtterance,
      target (componentId/elementId), requestedProperty, missingSlots, rejectedCandidates, reasonCode,
      continuationToken; on the 422 body as `pending`; `IntentRequestDto.continuationToken` resumes;
      a reply merges slots ("not the roof, the columns" replaces the target and drops the rejected one).
- [ ] C2 Web: the question card carries the pending intent; selection updates atomically from it; the
      next sentence is sent with the continuation token; terminal codes render "needs an authored
      control" with the declare action instead of asking again.

### D. Villa re-index without geometry change (tools + project root only)
- [ ] D1 Lineage: the exact current candidate 3DM(s), state record, programs, inspections and
      predecessors (audit-villa-lineage).
- [ ] D2 `tools/reindex_project.py` (generic): from retained inspections + programs + the authored
      record, derive stable component identity, element identity, object→element map, producer ops,
      capability draft, dependency draft, each with provenance and confidence; AMBIGUOUS/MISSING where
      not unique; writes a draft successor `state-record` and a `component-catalog` coverage report
      into a new run of the external project through P036 (write point registered in
      `governance/architecture_policy.json`).
- [ ] D3 Coverage: main block, exterior walls, floors, central hall, corner rooms, roof bearing course,
      main roof, drum/dome/oculus/lantern, four porticos, columns/capitals/entablature,
      pediments/portico roofs, landings/stairs/passages, windows/frames/glazing, doors, internal stairs.

### E. Villa dependencies
- [ ] E1 Relations authored (not invented): landing datum → shaft, shaft → capital, capital →
      entablature, entablature → pediment/portico roof, portico roof → abutment, stair datums →
      section, opening → frame → glazing, wall → hosted opening, roof bearing course → roof; closures
      verified through `StateRecord.closure`.

### F. Recompile on the spine
- [ ] F1 Element rows through `element_producers` + run-017 seat conventions; old scripts become
      read-only baseline/provenance; family by family; a successor candidate from the tree; no in-place
      3DM change; no issue/promotion.
- [ ] F2 Per-component comparison old ↔ new (bbox, counts, datums, interfaces, semantics) via
      `compare_runs`; unexpected geometry differences: zero; incremental rebuild scope on a local change;
      real readback of the new 3DM; Studio loads the new state.

### G. Tests that must pass (api)
- [ ] G1 "把左侧柱廊的柱子提高 0.1m" → west portico columns, never the west abutment; closure includes
      column, capital, entablature, roof interface.
- [ ] G2 "把左侧柱廊略微提高" → candidates + missing slots; no "10 %" by itself.
- [ ] G3 "不是屋顶，是柱子" → pending target becomes columns; roofs not carried forward.
- [ ] G4 `portico-columns` with no catalog → `MODEL_VISIBLE_CATALOG_MISSING`; abutment.height never offered.
- [ ] G5 "补充 portico-columns 字段" → `declare_missing_control` → authored-control proposal with
      provenance; not written to the authored record without confirmation.
- [ ] G6 object ↔ element ↔ operation traceable; G7 tree coverage reconciles with 3DM coverage;
      G8 canonical HEAD, old runs, models and receipts unchanged (digests before/after).

## 3. Project markdown

One `PROJECT.md` at the project root (checked first against existing brief/manifest mechanisms):
identity, object/era/target state, branch and stage, units/frame/up-axis, typology, human names,
aliases and directional conventions, evidence entry points, open items, preferences; bound to a state
digest line. It never holds element ids, values, editable fields, edges, locks, bindings, operations
or authority.

## 4. Open questions for Kaiwen (asked only when the choice changes the result)
- (to be filled after the lineage audit)

## 5. Ledger, 2026-09-04 (second session)

- A1/A2 landed: 3a32aa9 (catalog derived per projection; `MODEL_VISIBLE_CATALOG_MISSING` on pick and in the catalog). A3 (web tree panel) not started: the web tree is edited by the main session/codex; a window was asked for twice, no answer yet.
- B/C landed as fb01157 (two-stage resolver, pending intent on the 422 body, `POST /api/controls`, PROJECT.md names/compass) and the codex-timeout fix 6faebef; the main session reverted fb01157 in 3a97ae4 and landed its own pending intent in ff82ee6 (`application/clarification.py`: COMPILED / NEEDS_CLARIFICATION / MISSING_EDITABLE_CONTROL / UNSUPPORTED, continuation token, kind-based target resolution, rejectedCandidates, no-progress rule). Not re-applied; the gaps against G1–G5 (camera+compass for left/right, PROJECT.md aliases, a stored declared control) are to be filled on their structure once they answer.
- D1 done: base = reconstruction-015 stage-4 inspection 6546e9e5… (model ab784ab3…, program f958af7b… — the program every run-017 seat cites); porticos = the four run-017 seat runs (design authority: human-derived-stack-ruling-7aac18cd…); the two west facade revisions and the three west wall-elements/typed-instances receipts have no authority and are listed as alternates.
- D2 done: `archflow/capabilities/element_reindex.py` + `tools/reindex_project.py` (2ede1b5), record kind `component-catalog`; runs written into the project: reindex-001, reindex-002 (witnesses excluded). reindex-002: 1462 objects, 1058 bound, 477 drafts, 78 ambiguous, 0 errors, 7 axes drafted, 166 derived support relations; components COVERED 24 / PARTIAL 2 / catalog-missing 7 / empty 8. Coverage Markdown in each run's `workspaces/coverage/`.
- D3: covered by rows — porticos (columns as column-array on axes 1–6/A–F with drafted column lines, capitals on `<columns>-top`, entablature fronts as beams with 1.699 m overhang, courses/landings/roofs as prisms), exterior walls east/north/south as walls with 10 openings each typed as wall-west declares, drum rings/cornices/oculus as rings, floors/cornices/stairs as per-box prisms. Identities without rows (AMBIGUOUS): pediments, roof abutments E/N/S (5-face wedges), main roof sectors, drum and dome shells, lantern, spiral cores. `portico-roof-abutment-west` (authored, a run-016 reading) measured 1.115 m off the run-017 object; `wall-west` residual 0.05 m at the frame projection.
- E1 partial: 166 `support` relations derived from vertical contact (25 mm) — the portico stacks, landings, rings; wall→opening hosting is inside the wall rows' `openings`.
- F1 in progress: `tools/run_project.py --record-ref/--seats-file` (6487cb1); reindex-compile-001 failed on a door void touching the landing top (wall solver counted a shared face as overlap → `CONTACT_TOLERANCE_M` in wall_solver), reindex-compile-002 failed on the west underpass assemblies inside the service passage (handed over as exclusions → moved to the envelope seat in the partition v2), reindex-compile-003 running.
- G: `tests/test_element_reindex.py` (14) covers G6/G7 generically; the API G1–G5 tests were reverted with fb01157; G8 checked by hand (authored record sha e20c2abb…, canonical HEAD state-v000000-934efb53… unchanged after every run).
- Villa `PROJECT.md` written at the project root (identity, units/frame/up axis, compass, names, evidence entry points, open items; bound to record digest e88eba2a…).
- F1/F2 done (2026-09-04, later): reindex-005 (opening interfaces mirrored with their Connection@1; ids off the component namespace) recompiled end to end in reindex-compile-008 on an envelope bound to the candidate (`open_stage_run --record-ref`, 89c9321): both seats accepted and exported, readback verified, stage closure SATISFIED, exit binding written; no promotion. Equivalence (union bbox, 25 mm): 479 elements, 478 equivalent, 1 differs (the authored west abutment, 1.115 m), 0 not produced; 860 objects vs 1 056 bound sources (four walls are four solids instead of 112 scripted slices; 32 wedge floor pieces and the shells/pediments/stairs cores stay AMBIGUOUS without rows). Attempts 001–007 each name one refusal: landing/door contact, exclusion clashes (partition v3), duplicate opening op ids, missing interface, missing Connection, envelope bound to the authored state. Report: V4_RUNTIME/handoff/260904_claude_villa_reindex_report.md.
- Next (studio, on the main session's clarification.py per the relayed ruling A): camera+compass level, catalog as the single directory (MODEL_VISIBLE_CATALOG_MISSING as a sub-reason of COMPONENT_HAS_NO_EDITABLE_ELEMENT), controls persistence on AuthoredControlDraftDto, nested tree panel in web/src on their QuestionCard/MissingControlCard/App.tsx sync; Wave E1/E3/E4 absorbed as requirements.
- Studio, on the main session's clarification.py (b465d0e): camera + PROJECT.md compass read "left" into a side that narrows and never introduces; PROJECT.md aliases name components; the catalog is the resolver's directory and the terminal draft says MODEL_VISIBLE_CATALOG_MISSING with the objects; absolute deltas ("提高 0.1m") read into the grammar deterministically; POST /api/controls keeps a confirmed draft. Web (8391b65): nested ComponentTree replaces SelectionPicker; verified in the browser against the villa with reindex-compile-008 as reference run (building → porticos → portico-capitals: 24 objects, 24 without a row). Left: E1 episode record kind + writer, E3 scope step, E4 derived-control branch; installing the reindex-005 successor as the authored record is Kaiwen's call.

## 6. Full-design-flow wave, 2026-09-04 (Opus implemented, Fable calibrated)

Eight worker results merged to main (36ba695), each rebased, tested and registered by hand:
- W1-A deliberation episodes (E1): record kind DeliberationEpisode@1; POST /api/proposals/{id}/decision, GET /api/episodes; written into the candidate run at accept, flushed from memory at the next run of the same state.
- W1-B producers stair / wedge / shell (with the _loft parameter-order fix 609379c the re-index worker found).
- W1-C web capability panel (what a selected element can be asked; set… and declare-a-control prefill).
- W1-D scope step (SCOPE_UNRESOLVED: element / stack / datum from the catalog closure; asked only when a stack exists, never for a picked element) and derived controls (CONTROL_IS_DERIVED names the source, never compiles).
- W2-B frame editor (GET /api/state/frame, POST /api/state/closure; levels and axes with what changing one would move).
- W3-A re-index drafts stair flights; wedges and shells only from explicit archflow:wedge_*/shell_* export strings (the main session's 526f745/6935fc0 write them; wedge_sense is plane-anchored +x/-x/+z/-z); a failed flight falls back to per-step prisms (3acba47).
- W2-C massing options with metrics (state/massing_metrics.py; options retained as selected-spatial-option in option-NNN runs; state.record gained volume_boxes_of / schematic_pack_of).
- W2-A program sheet (state/program_sheet.py; input/runner/program-sheet.json read/written only by project.inputs; applied as a candidate; saveInput local-mode only).
Left for the next wave: design steps 0/1 (brief → obligations with sources; site envelope), step 6 (design-obligation validators, review agent), step 7 (IFC/Revit), a generative provider on the pack transform, the sheet candidate readable through GET /api/candidates/{id}, the retention-failure-fails-job concern in episodes, and the wedge export strings verified end to end on the villa.

