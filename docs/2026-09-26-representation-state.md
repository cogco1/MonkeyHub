# Representation and production state: evidence and answer (2026-09-26)

**Issue:** [#223](https://github.com/cogco1/MonkeyHub/issues/223), lane `GH-223/representation-status`.
**Base:** `f221d029` (batch F, batch DE and the batch G claim).
**Scope:** closes the experimental scope reopened on 2026-09-23: three representations of one exact design source; selective design, representation, artifact and review invalidation; cold reopen; explicit handling of a missing or replaced host or source. The evidence is tests over fixtures. The change adds one read-only projection and no record kind. The Blender physical-render variant and the #217 eye anchor stay with #218.
**Owner decisions (2026-09-25):** D-223-1 accepted: no second global State, and the project-level dependency is a read-only projection. D-223-2 accepted: decisions carry the project recipe (`StudioScopedDecision@1` with a recipe binding). Lane `GH-223/recipe-decision` builds it; this change does not.

**Path abbreviations**

| Short | Path |
| --- | --- |
| `API` | `apps/archflow-studio/api/archflow_studio_api` |
| `TESTS` | `apps/archflow-studio/api/tests` |
| `RD` | `TESTS/test_representation_dependencies.py` |

## 0. Answer

- **No `RepresentationState@1`, no `ProductionState@1`, and no Representation or Production Stage.** Each representation already has a retained owner. Each owner binds it to exact sources and restores it on reopen. People's production verdicts also have retained records already.
- **The project-level dependency is a projection.** `representation_status` in `API/application/representation_dependencies.py` (owner `studio.binding`) takes one exact page and returns its status (`current`, `outdated`, `frozen` or `unavailable`), the exact refs the page was made from, and the newest page that replaces it. It asks the page's owner on every read and stores nothing.
- **Four readers, one answer.** The Worktree Graph and Publish now read the projection. Drawing's `plan_status` and Render's `_freshness` stay as the read-set readers it asks. Before this change the graph judged a drawing by its whole model state. A change outside the plan's crop then showed the drawing stale in the graph, while the Drawing tool, Render and Publish showed it current.
- **The only new retained object** is the project recipe, carried by a decision rather than a new state (D-223-2).
- **R1/R2/R3 already has a test** at the drawing and AI-render level (§5). Studio has no Blender physical variant; that stays with #218.

## 1. Model

```text
Project
├─ Design State / Stage            StateRecord@1, DesignStage@1, the Working Head (studio.binding)
├─ Representation                  each owner keeps its own content
│   ├─ drawing recipe              StudioSourceDocument@1 viewRecipe kind=cut-plan
│   │                              + DrawingProjectionReceipt@1 (studio.artifacts)
│   ├─ render job + document       StudioRenderJob@2 + viewRecipe kind=ai-render (studio.render)
│   ├─ captured view               viewRecipe kind=model-view: camera, screen size (studio.render)
│   ├─ Blender presentation        BlenderPresentation → BlenderProjectionReceipt@1 (core runner only)
│   ├─ board scene                 studio-board-scene (studio.board)
│   └─ publication                 PublicationDocument@1 (studio.publication)
├─ Human dispositions              CandidateAdmission@1, DeliberationEpisode@1, Publish freeze,
│                                  page review ink, StudioScopedDecision@1
├─ Artifacts                       PNG, PDF, SVG, STEP, 3DM and .blend bytes: content-addressed,
│                                  receipt-bound, never authority
└─ derived representation status   representation_status(): current | outdated | frozen | unavailable
                                   (read-only, nothing stored)
```

## 2. Where each fact lives

For each fact the table gives its owner and record, whether a cold reopen restores it, whether a change to it can make something downstream outdated, and the test that shows this.

| Fact | Owner and record | Reopens | Can outdate downstream | Test |
| --- | --- | --- | --- | --- |
| Design content: entities, parameters, relations, materials | `StateRecord@1`, through a run's runner receipt | yes | yes, but only what reads the changed part | `RD::test_three_exact_design_representations_reopen_and_only_follow_read_geometry` |
| The Working Head, which every live representation compares with | `design/working.json` `current`, else the main line's accepted head (`studio.binding`) | yes | yes | `TESTS/test_rendering.py::RenderModelFreshnessTests::test_saved_render_goes_stale_when_the_working_head_moves_and_current_when_it_returns` |
| Cut-plan view: cut height, depth, crop, scale, line weights, hatch spacing, hidden objects, dimensions, `follow` | the `viewRecipe` of `StudioSourceDocument@1`, plus `DrawingProjectionReceipt@1` (`studio.artifacts`) | yes | makes a new revision only; never the design, never a sibling | `RD::test_three_exact_…`; `TESTS/test_drawing_plans.py::CutPlanTests::test_real_plan_representation_changes_reopen_with_source_and_old_revisions` |
| Drawing revision chain | `previousRevisionRef` in the receipt | yes | no status reads it yet (291-S1 registers page replacements) | `TESTS/test_drawing_plans.py::CutPlanTests::test_real_plan_representation_changes_reopen_with_source_and_old_revisions` |
| Entourage on a plan (people, trees) and its anchor | `viewRecipe.dressing[]`, whose `anchorObjectId` names an exact object | yes | a moved anchor makes the plan outdated; a deleted anchor leaves it partially broken and never rebinds it | `TESTS/test_drawing_plans.py::CutPlanTests::test_dressing_anchor_follows_exact_object_and_survives_deleted_anchor_without_rebinding`; `RD::test_missing_drawing_host_makes_descendant_unavailable_without_rebinding` |
| Captured camera view | `viewRecipe` kind `model-view` (camera, screen size) and the identity chunk in its PNG | yes | outdated when its model state stops being the Working Head's design | `TESTS/test_rendering.py::RenderViewTests::test_camera_source_survives_restart_and_is_the_actual_adapter_input`; `RD::test_one_status_vocabulary_for_drawing_render_and_upload_pages` |
| AI render request: direction, source, references, output options, provider, model | `StudioRenderJob@2`, and the result's `viewRecipe` kind `ai-render` | yes, and never replayed | the result is outdated when an input is; an independent sibling stays current | `RD::test_replaced_reference_propagates_through_render_but_not_independent_variant`; `TESTS/test_rendering.py::test_reopen_restores_completed_attempt_without_dispatch` |
| Render attempt status | `StudioRenderJob@2` sequence rows | yes; a running attempt reopens as `unknown` | no | `TESTS/test_rendering.py::test_reopen_running_is_unknown_and_read_or_retry_never_replays` |
| Blender presentation: camera, preset, resolution, samples | `BlenderPresentation`, retained in `BlenderProjectionReceipt@1` by the core runner | yes (the receipt) | no Studio consumer; a `.blend` edited in Blender is refused, never read back as design | `tests/test_blender_projection.py::ProjectionTests::test_full_rebuild_cold_read_and_artifact_provenance` and `::test_blender_geometry_edit_is_rejected_without_changing_source` (both need `ARCHFLOW_BLENDER_EXECUTABLE`) |
| Page replacement | `replaces_pages` of `StudioSourceDocument@1` | yes | yes: the replaced page and everything made from it | `TESTS/test_rendering.py::test_replaced_source_is_outdated_without_rebinding`; `TESTS/test_publications.py::PublicationTests::test_replacement_marks_old_source_stale_and_freezing_preserves_original_export` |
| Review ink and comments | `studio-document-annotations`, per exact page | yes | no; ink stays evidence about its exact page, and a new revision starts without it | `RD::test_three_exact_…`; `TESTS/test_document_annotations.py::DocumentAnnotationTests::test_two_pages_image_and_file_versions_keep_separate_ink_through_erase_undo_reopen` |
| Board scene | `studio-board-scene` (`studio.board`) | yes | no; Board swaps replaced pages in place | `TESTS/test_boards.py::BoardTests::test_geometry_text_frames_and_exact_pages_survive_cold_restart` |
| Publication pages and freeze | `PublicationDocument@1` | yes | no; a frozen source keeps its exact bytes | `TESTS/test_publication_dependencies.py::test_drawing_changes_stale_live_page_and_leave_frozen_output_and_layout_intact` |
| Candidate verdicts | `CandidateAdmission@1`, `DeliberationEpisode@1` | yes | no | `TESTS/test_candidate_admission.py::CandidateAdmissionTests::test_a_rejected_result_is_retained_and_listed_only_on_request`; `TESTS/test_episodes.py::RejectionIsAJudgement::test_a_rejected_proposal_is_kept_with_its_reason` |
| Artifact bytes: .png, .pdf, .svg, .step, .3dm, .blend | content-addressed objects and their receipts | yes | a missing or damaged file makes its page unavailable (in Publish, missing) and rebinds nothing | `RD::test_missing_transitive_input_is_unavailable_without_destroying_render_result`; `TESTS/test_publications.py::PublicationTests::test_missing_retained_source_allows_text_repair_but_blocks_export_until_restored` |
| Representation status | not stored; `representation_status` derives it | re-derived on each read | it is the downstream answer | `RD::test_one_status_vocabulary_for_drawing_render_and_upload_pages`; `RD::test_the_worktree_graph_and_the_drawing_agree_on_read_set_changes` |

The same table answers the Issue's audit questions A.1 to A.5:
- A.1, reliably retained: every fact above.
- A.2, only in UI state or script parameters: nothing, once it is used. Drawing saves an appearance edit as a new revision once the edits pause, and Render retains a request when it is submitted. Material and lighting wishes are retained only as an AI render's untyped `direction` text. Blender's lighting is a fixed code preset (`two-area-v1`), recorded in its receipt but not editable project data.
- A.3, lost on reopen: only a running render attempt. It reopens as `unknown` and is never replayed.
- A.4, mixed into the Design State: nothing. The representation tests check that `HEAD` and the design branches stay put.
- A.5, a projection that can be rebuilt and needs no persistence: representation status itself.

## 3. The projection (223-S1)

`representation_status(binding, page, *, frozen=False, reads=None)` judges one exact page (`runId`, `assetSha256`, `revisionRef`, `pageIndex`) in this order:

1. It finds the newest registered replacement by following each explicit `replaces_pages` link. A loop raises `ReplacementCycle`.
2. It reads the page's own registration and bytes through the artifact owner (`document_bytes`). If that fails, the page is `unavailable` with `page_available` false.
3. A consumer's own keep (`frozen=True`, Publish's freeze) is `frozen`. The page is read but never compared.
4. A replaced page is `outdated`, whatever it depicts.
5. Otherwise the page's owner answers:

| Page | Owner and reader | `current` | `outdated` | `frozen` | `unavailable` |
| --- | --- | --- | --- | --- | --- |
| cut plan | Drawing, `drawing_plans.plan_status` | its read set matches the Working Head's drawable source | geometry, anchors or dimensions it reads changed, or the Working Head has nothing drawable | recipe `follow: frozen`, and its chosen version still resolves | an anchor or host is lost, or nothing can be verified |
| AI render | Render, `rendering._freshness` over the retained request | every source and reference is current | an input was replaced or is outdated | never | an input cannot be resolved |
| captured view or model-bound page | `working_draft.model_is_current` | its exact state is the Working Head's design | the head now holds other design content | never | there is no head, or the model cannot be resolved |
| page bound to nothing | none; only replacements count | until replaced | never | never | never |

`upstream` lists the refs the owner retained for the page, under the owner's own field names: an AI render's `source` and `reference` pages; a drawing's or captured view's `modelSource` and `sourceStageRef`; an imported drawing's `sourceAsset`. That list is the Issue's dependency-index row ("who depends on whom"), derived on each read.

Each consumer keeps its own words, and no wire literal changes:

| Projection | Worktree Graph (`transport/runtime.py`) | Publish (`transport/publications.py`) |
| --- | --- | --- |
| `current` | `current` | `current` |
| `outdated` | `stale` | `stale`; a replacement also names the newest page |
| `frozen` | `frozen` | `frozen` for Publish's own freeze; `current` for a drawing kept on its version |
| `unavailable` | `unavailable` | `missing` when the page's own bytes cannot be read, else `stale` |

The four readers, before and after:

| Reader | Before | After |
| --- | --- | --- |
| Render, `rendering._freshness` | its own dispatch | unchanged; the projection asks it about AI-render pages |
| Drawing, `drawing_plans.plan_status` | its own read set | unchanged; the projection asks it about cut plans |
| Publish, `publications.source_statuses` | its own dispatch over cut plans and AI renders; model-bound pages never checked | reads the projection and maps its words |
| Worktree Graph, `runtime._representations` | judged drawings by the whole-state `model_is_current` | drawing rows read the projection; render rows keep the render owner's `sourceState` |

Two behaviors change, both toward one answer per page:
- **The graph's drawing rows follow the plan's read set.** A graph drawing row now matches the Drawing tool. A new Stage that changes nothing the plan reads leaves it `current` (`RD::test_the_worktree_graph_and_the_drawing_agree_on_read_set_changes`; `TESTS/test_worktrees.py::WorktreeGraphTests::test_representations_start_empty_and_a_render_goes_stale_with_the_head`). A drawing whose anchor or host is gone shows `unavailable`, not `stale`.
- **Publish checks model-bound pages.** Publish now compares a captured view or model-bound page with the Working Head. Render already did this for the same page as an input (`RD::test_one_status_vocabulary_for_drawing_render_and_upload_pages`).

A render row in the graph stays the render owner's reading of the attempt's retained request, which is the reader the projection uses for AI pages. Asking about the output page instead would run that reader a second time for every attempt, and would count a pre-AI native attempt, which has no retained request, as an unbound upload.

## 4. The Issue's six questions

1. **Is `RepresentationState@1` needed?** No. Drawing, Render, Board and Publish each retain their own representation, bound to exact sources, and each restores it on reopen (§2). What was missing was one reading of their status: `representation_status`, a projection rather than a record.
2. **Is a "Representation Stage" worth having?** No. The only status a consumer uses is derived: `current`, `outdated`, `frozen`, `unavailable`. A person's judgement about a representation already has an owner: Publish's freeze, page review ink, a drawing's `follow: frozen`. Nothing consumes a Stage-like maturity of a representation across sessions.
3. **Is a "Production Stage" too abstract?** For now, yes. Job status is transient and says so: a restart shows `unknown` and never replays. Retained verdicts already exist: `CandidateAdmission@1`, `DeliberationEpisode@1`, the freeze, the review ink and a formal issue (`project.issue`). The Issue's own rule applies: no schema without a real consumer across sessions.
4. **Are scene objects representation-only, or design evidence?** Plan entourage is representation-only. It lives in the drawing recipe (`dressing`) and is anchored to an exact design object (`anchorObjectId`). A rebuild moves it with that object. If the object is deleted, it is reported `missing` and is not rebound. It never enters the State Record. A scene object can become design evidence only through an explicit design proposal in a person's words, never by being drawn. Entourage in an AI render exists only as `direction` text, which nothing retains as evidence.
5. **How are a render material override and a design material kept apart?** Design materials are State Record fields. An AI render's material wish is `direction` text in its retained request. `BlenderPresentation` has no material override and is locked to `overview` / `preview-v1`. No render path writes design state: `studio.render` states this as an invariant, and its tests hold `HEAD` fixed. A typed render recipe waits for a real user (#253).
6. **Do drawing, render and fabrication need a shared abstraction, or a shared contract?** A shared contract. Drawing, Render and Publish share three things: exact source binding (`runId`, `assetSha256`, `revisionRef`, `pageIndex`, `modelSource`, `sourceStageRef`); explicit page replacement (`replaces_pages`); and now one status projection. They share no representation type. Fabrication (`apps/monkeyfab`) runs outside the project Runtime and reads no representation status today.

The Issue's render slice has six acceptance checks:

| # | Check | Evidence |
| --- | --- | --- |
| 1 | A design geometry change makes only what depends on it stale. | `RD::test_three_exact_…`: a distant wall leaves three plans and their renders current, and a moved wall makes them outdated. `RD::test_the_worktree_graph_and_the_drawing_agree_on_read_set_changes`. |
| 2 | A camera, line-weight or lighting change creates no design revision. | `RD::test_three_exact_…`: one style revision and three render directions, with `HEAD` and the branches unchanged. |
| 3 | A representation object attached to a design object keeps that anchor. | `TESTS/test_drawing_plans.py::CutPlanTests::test_dressing_anchor_follows_exact_object_and_survives_deleted_anchor_without_rebinding` |
| 4 | A rebuild can say why, along the same source. | The projection's `reason` is the owner's own detail: `RD::test_one_status_vocabulary_…` compares it with the Drawing tool's detail and the Render tool's `sourceState`. `upstream` names the exact inputs. |
| 5 | Source binding still verifies after reopen. | `RD::test_three_exact_…`: a fresh runtime reads the same requests, states and ink with no adapter call. `RD::test_replaced_reference_propagates_through_render_but_not_independent_variant`. |
| 6 | A stale edge, missing host or replaced source is refused or degraded, never silently rebound. | `RD::test_missing_drawing_host_makes_descendant_unavailable_without_rebinding`; `RD::test_missing_transitive_input_is_unavailable_without_destroying_render_result`; `TESTS/test_rendering.py::test_replaced_source_is_outdated_without_rebinding` |

## 5. Reproducing R1/R2/R3

The tests use fixtures only. The room fixture in `TESTS/test_drawing_plans.py::CutPlanTests` builds a real four-wall room with a door, compiles it through OCCT and accepts it as a Stage. An in-process test adapter stands in for image transport. No user project is read.

Run from a checkout with the Studio API dependencies. `cadquery-ocp` is optional; without it the OCCT tests skip. Separate `PYTHONPATH` entries with `;` on Windows and `:` elsewhere.

```text
PYTHONPATH=<repo>;<repo>/apps/archflow-studio/api;<repo>/apps/monkeyhub/api;<repo>/apps/monkeyfab/src
python -m pytest -q apps/archflow-studio/api/tests/test_representation_dependencies.py -k three_exact
python -m pytest -q apps/archflow-studio/api/tests/test_representation_dependencies.py
```

What `-k three_exact` shows:
- **Three representations of one source.** R1, R2 and R3 are three cut-plan recipes of one exact source: `review-plan`; `heavy-cut-plan`, with a 0.7 mm cut line; and `upper-plan`, cut at 2.5 m. Each has its own AI render direction: "Neutral review", "Daylight material study" and "Presentation lighting". All three share one `modelSource` and have three revisions and three distinct images.
- **A style change stays local.** R1 gets a style revision (a 0.9 mm cut line) and a new render. Every sibling stays current, and the new page starts without the old page's review ink.
- **Only a change the views read outdates them.** After an accepted wall far outside every crop, all stay current. After the front wall moves inside the crops, all are outdated, and their documents and bytes are unchanged.
- **Reopen restores everything without re-rendering.** A fresh runtime on the same project reads the same requests, states and ink, with no adapter call. The published `HEAD` and the design branches never move.

## 6. Left open

- **#218 (Panny):** the physical Blender render variant. Studio has no Blender wiring. `BlenderPresentation` is locked to `overview` / `preview-v1`, and its only writer is the core project runner. The #217 eye `attached_to` head belongs to that slice. Studio's equivalent regression is the plan-entourage anchor test in §2.
- **#253:** a predecessor chain for AI renders (`replaces_pages` plus the previous request), once a real user needs it.
- **291-S1:** drawing rebuilds will register page replacements, so Publish can offer the newest revision. The projection keeps its rule that a replacement answers first for the page itself. Whether a pure representation rebuild outdates a render made from it is Render's rule (D-291-1).
- **Cost:** a Worktree Graph read now runs the Drawing tool's status once per drawing, and the route still reads every render attempt's status. A per-request cache shared across both belongs to the drawing and render owners.
- **Duplicate closure walk:** `decision_operator.py` and `state_record.py` both walk dependency closures. That is design-domain cleanup outside this Issue.
