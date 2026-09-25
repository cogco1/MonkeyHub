# Visual observation: audit, V0 contract and benchmark (2026-09-25)

**Issue:** [#303](https://github.com/cogco1/MonkeyHub/issues/303), lane `GH-303/visual-observation`.
**Base:** `origin/main` `e183a69a`.
**Scope:** one source-bound, read-only visual observation channel, with Modeling as its first consumer. It is not wired into production chat.
**Method:**
- The author read the backend and Hub paths directly. Two read-only sub-agents located the frontend and Monitor call sites, and the author re-checked every line cited here.
- The benchmark ran against a copy of the installed Hub's test project, through a development project runtime. The author acted as the modeling Agent and recorded each verdict before looking at any image. All real images, raw results and logs are kept outside the repository ("local evidence").

**Path abbreviations**

| Short | Path |
| --- | --- |
| `API` | `apps/archflow-studio/api/archflow_studio_api` |
| `HUBAPI` | `apps/monkeyhub/api/monkeyhub_api` |
| `WS` | `apps/monkeyhub/web/workspaces/src` |
| `MM` | `monkeymonitor` |

## 0. Summary

**What exists**
- The Agent already sees the model. `GET /api/drawings/model-view` is an exact, owner-verified line projection. The Hub turns it into a native MCP image block in the Agent's own context (`HUBAPI/chat.py:2940-2949`).
- No budget or structure governs that look. The Agent picks every observation point, images accumulate in its conversation, and nothing records a "visual review" as distinct cost. Monitor classifies the call as an ordinary tool call (`HUBAPI/chat_trace.py:127-131`).
- The Studio already has a model transport that sends images. `invoke_structured` (`API/application/intent_agent.py:1528-1559`) runs `codex exec` or Anthropic Messages with images, a strict schema, usage and a receipt, and Study uses it (`API/application/study_model.py:110`).

**What V0 adds** (`API/application/visual_observation.py`)
- A request names exact sources.
- Frames come from the projection owners' own answers. A frame that is not exactly one of the named sources is refused before the provider is called.
- The provider's answer is validated against the request's own refs and bound to the frames that were actually sent.
- A Harness allowance decides when a review may happen.
- Each review is a `visual_observation` Monitor span.

**Benchmark** (two tasks × conditions A/B/C, plus one Board page; 5 reviews, plus one text-only cost probe)
- **Deterministic edit.** Vision added nothing. The observer said itself that exact widths cannot be read from a line projection, and its suggested checks were the readback already done.
- **Spatial/formal task.** Readback alone passed every hard criterion and would have reported completion. The perceptual criterion "the four units read as one ensemble" failed on proxy review, so that report would have been a false completion.
  - One review (B) caught the problem. The report changed to a named gap and a question for the architect.
  - A repair and a follow-up (C) confirmed the repair had not fixed it. The loop then stopped at its budget instead of claiming success.
  - Neither B nor C reached the perceptual goal. Its cause lies in conditions the task said to preserve, so the next step belongs to the architect.
- **Cost.** One review of three views took about 22 k input tokens and 49–66 s of provider time. A text-only call through the same transport took 17.3 k input tokens and 15 s, so most of that is the Codex CLI's fixed agent prompt, not the images. Rendering the three frames took a further 19–40 s.

**Recommendation** (§8)
- Keep the channel in the `studio.intent` model seam.
- Wire it next as one runtime route that renders frames itself, called by a Hub Agent tool that holds the per-task budget.
- Use it for spatial and formal tasks only.
- Send a finding whose cause is a preserved condition to the architect instead of repairing it.

## 1. Audit

### 1.1 Image producers and their binding

| Path | Producer | Binding verified before pixels | Stale / forgery risk |
| --- | --- | --- | --- |
| `GET /api/drawings/model-view` (`API/routes/drawings.py:74-90` → `model_view`, `API/application/drawings.py:118-140`) | OCCT hidden-line projection of the retained STEP. Rasterised by Pillow, 150 dpi, ≤1024 px. Front, back, left, right or top only. | `require_model_source` (`API/application/artifacts.py:1074-1090`) checks the exact run, stateDigest and asset (409 `MODEL_SOURCE_MISMATCH` / `MODEL_SOURCE_UNREGISTERED`). `_complete_source` (`API/application/drawings.py:61-81`) requires the paired STEP; `read_elevation_source` re-hashes it. | Exact but not current. An older run still answers with no current/outdated flag (`apps/archflow-studio/api/tests/test_drawings.py:90`). No cache: every call projects again (6–40 s for 1–3 views in §6). No axonometric or perspective view. |
| `POST /api/board/export` (`API/routes/boards.py:31-46` → `export_board_pages`, `API/application/boards.py:170-221`) | `_page_raster` (`API/application/boards.py:144-167`): one registered PDF or image page as PNG/JPEG. | Exact registered bytes, revisionRef (null is not a wildcard) and page index. A digest mismatch is 409 `DOCUMENT_DIGEST_MISMATCH`. | Exact page, but not the Board scene. No server raster of a Board frame exists. No check against a newer replacement page. |
| `POST /api/drawings/sheets`, `/drawings/elevations`, `/drawings/plans` (`API/application/drawings.py`, `drawing_plans.py`) | Retained PDF sheet, PNG/SVG elevation, cut-plan SVG | Exact model source and receipt. Cut plans alone have a Working Head status (`API/application/drawing_plans.py:267`). | Retained revisions; they are read as pages through board export. |
| Render results (`API/application/rendering.py`) | Adapter output kept as a registered document | Freshness is recomputed on read (`_freshness`, `API/application/rendering.py:91-153`) against the Working Head. | The best-bound image source today. |
| `POST /api/render/views` (`API/application/rendering.py:43-63`) | Browser WebGL capture (`WS/workspaces/monkeyarch/viewer/renderView.ts:20-35`) | Only the PNG format and size are checked (`:49-56`). The model source is claimed by the client (`WS/app/App.tsx:720-731`). | Pixels are not verified against the model. |
| `POST /api/captures`, `POST /api/tracing-paper/reviews` (`API/routes/artifacts.py:128`, `:226`) | Viewport PNG from the browser | Run id only, or the recipe only. The first pixels win for a recipe. | Unverified pixels. |
| Intent document visuals (`prepare_document_visuals`, `API/application/gestures.py:456-507`) | Client render of a registered page plus ink | Registration and aspect ratio. Its docstring says "pixels remain the client's render". | Unverified pixels, already sent to the provider. |

**Rule for this channel:** only the first four rows are admissible frame sources. The last three stay excluded until their owners verify pixels.

### 1.2 How images reach the Agent today

- The Hub Agent is a Codex session (ACP by default) with the Hub's MCP tools.
- `studio_request GET /api/drawings/model-view` is allow-listed (`_READ`, `HUBAPI/chat.py:2018`). So is `POST /api/board/export` for one page (`_read_drawing_page`, `:2092-2107`; `_page_image` bounds, `:2071-2089`). The reply becomes a native MCP `image` block (`:2940-2949`).
- The prompt asks the Agent to "distinguish geometry readback from visual inspection" (`:1654-1655`) and to "inspect relevant views when available" (`:2846`). The tool description names the observe route (`:2865-2866`).
- The Agent decides when to look. Each image stays in its conversation, and no structured finding comes out.
- The #294 completion contract relies on exactly this. Its C6 (preserve rules stated in words) and C8 (the evaluator or visual inspection) are only the actor's claim today (`docs/2026-09-25-candidate-admission-audit.md` §4.3).

### 1.3 Deterministic readback available before vision

- `GET /api/candidates/{id}` (`API/routes/candidates.py:168-257`) returns:
  - status, seat completion and relation totals;
  - artifacts with `modelSource`;
  - every retained object's bbox.
- `GET /api/candidates/{id}/compare` (`:260-303`) gives per-object before/after boxes.
- `GET /api/state?run=` gives parameters and dependency edges.
- The Hub's awaited readback bundles these (`_finish`, `HUBAPI/chat.py:2314-2432`).
- `GET /api/candidates/{id}/validation` exists but is not allow-listed for the Agent.
- In the benchmark these reads decided every hard criterion (void size, containment, levels, footprints, window widths). They also answered the one recurring visual suspicion (§6.3) without another vision call.

### 1.4 What cost is recorded today

- **Studio model calls.** They are `model_request` rows with provider-reported tokens (`MonitoredCompiler._record_invocations`, `API/application/monitoring.py:207-251`). The spans come from `StudioMonitor` (`:35-150`), written to `MONKEYMONITOR_DATA_DIR` (`HUBAPI/applications.py:203`).
- **Hub turns.** `HubTurnObserver` (`HUBAPI/chat_trace.py:36-222`) records turn, `provider_round` and tool spans.
  - Codex token usage comes from the native rollout `token_count` events (`MM/codex.py:171-243`), not from `--json`.
  - The Hub ignores `turn.completed` usage (`HUBAPI/chat.py:1940`).
- **Visual review.** Before this lane there was no phase, request kind or image count for it. A model-view read was a generic `tool` call, and a Board page read counted as a `mutation` because it is a POST (`HUBAPI/chat_trace.py:127-131`). The detail vocabulary is closed (`MM/usage.py:18-28`).
- **Prior evidence.**
  - [The #195 pilot](../probes/spatial-observation-v1/README.md) showed that exact facts come from structured state. There the line-PNG arm abstained on 76 of 81 exact-fact questions.
  - The pilot left "visual semantics, composition, material and spatial sense" unmeasured. That is the gap this benchmark addresses.

## 2. Placement decision

The chain the issue asks for maps onto existing owners:

| Issue layer | Owner |
| --- | --- |
| Project State / exact artifact | P036, `state.*` |
| Projection | `studio.artifacts` (`model_view`); `studio.board` (page export); `studio.render` (results). They render. The channel does not. |
| Visual Evidence Frame | `EvidenceFrame`, built from the owner's own answer (`model_view_frame`, `page_frame`) |
| Visual observation channel | `studio.intent`, `application/visual_observation.py`: `observe_frames`, `VisualObservationProvider` |
| Evaluator / Agent policy / Harness | `VisualReviewBudget` (allowance). The Agent judges what a finding means. |
| Typed mutation | Unchanged: proposals → candidate (`studio.intent`, `studio.candidate`) |

**Decision: EXTEND `studio.intent`.** It already owns:
- the Studio model seam (the Codex subprocess and Anthropic Messages);
- the model-call receipt;
- the only existing path that sends bound page images to a provider.

The channel is a second consumer of `invoke_structured`, as Study is. It reuses the same arguments (`exec --json --ephemeral --ignore-user-config -s read-only --output-schema -o --image`, `API/application/intent_agent.py:72-82`, `:1043-1070`), the same `turn.completed` usage parsing (`:1140-1170`) and the same Monitor accounting. No new subprocess wrapper, store or record kind was added.

**Rejected placements**
- **The projection or persistence owners** (`studio.artifacts`, `studio.board`, `studio.render`). The issue forbids vision in them.
- **`studio.validation` and #294 admission.** Vision is evidence, not readiness or authority.
- **`studio.candidate`.** It is Modeling-only, and the channel must also serve Board, Drawing and Render.
- **`hub.shell`.** It would have to trust images crossing HTTP instead of rendering them in-process. It is the right home for the Agent tool that calls the channel (§8), not for the channel.
- **A new module.** AGENTS.md says to extend by default, and there is one real consumer so far. Revisit if the route in §8 grows its own lifecycle.

`ports.model` gained `ModelPhase.VISUAL_OBSERVATION`, so a receipt names the kind of call honestly. `MM/trace.py` gained the label 视觉观察 for the new span.

## 3. Contract (V0)

**`VisualReviewRequest`** (all validated on construction)

| Field | Meaning |
| --- | --- |
| `domain` | `modeling` \| `board` \| `drawing` \| `render` |
| `source_refs` | 1–4 exact `SourceRef` values:<br>• `model`: runId + stateDigest + assetSha256<br>• `page`: runId + assetSha256 + revisionRef + pageIndex (a null revision is exact, not a wildcard) |
| `view_recipe` | The owner views the frames must be, e.g. `top`, `front`, `right` or `page-0` |
| `task` | Bounded task summary (≤600 characters) |
| `criteria` | 1–8 `Criterion(criterion_id, text)` |
| `preserve` | ≤6 visible conditions that must not be disturbed |
| `budget` | The Harness allowance for this loop; it must equal the ledger's |
| `prior_observations` | ≤6 compact unresolved findings (`PriorFinding`) |

**`EvidenceFrame`**
- Contents: source, view ref, representation, PNG bytes, and width and height checked against the PNG header.
- Built from an owner answer (`model_view_frame(ModelViewDto)`, `page_frame(source, export bytes)`). V0 trusts its in-process caller to build frames this way. The route in §8 removes caller-supplied frames altogether by rendering them itself.

**`bind_frames`** refuses, before any provider call:
- a frame whose source is not exactly one of the request's (`VisualSourceMismatch`: the stale or foreign image case);
- a view outside the recipe;
- duplicates;
- a named source with no frame;
- anything over 4 frames, 4 MiB per frame, 2048 px or 16 MiB in total. These are the bounds the Studio already applies to document visuals.

**`VisualObservation`**
- `review_id` and `review_index`.
- `source_refs`, `view_refs` and `frame_sha256`. These are bound by the channel from the frames it sent; the provider never states them.
- `observations[]`, each with:
  - `finding_id`;
  - `type` (`spatial`, `proportion`, `relation`, `preserve`, `artifact`, `legibility` or `composition`);
  - `target_refs` (only `criterion:*` and `preserve:*` refs of this request);
  - `description`;
  - `confidence` (0–1);
  - `severity` (`info`, `minor` or `major`);
  - `evidence_region` (optional; a normalized box on a sent view).
- `unresolved_questions[]` and `suggested_checks[]`.

There is no verdict field. The strict provider schema (`observation_schema`) is closed over the request's own refs and views, and `parse_observation` re-checks the answer semantically.

**Provider seam**
- `VisualObservationProvider.capability()` / `.observe(request, frames)`.
- `StudioModelVisualProvider(compiler)` wraps the configured `CodexCompiler` or `AnthropicCompiler`, optionally inside `MonitoredCompiler`. The deterministic compiler is refused, and no fallback provider is chosen.
- A failed call raises `VisualProviderFailed` and keeps its usage.

**Read-only**
- The channel takes no project binding and writes nothing. The provider runs `--ephemeral` in a read-only sandbox.
- The observation holds no State, Stage or Candidate reference beyond the exact source identity. It holds no reasoning text.

## 4. Harness policy

| Task class | Allowance | Admissible reasons |
| --- | --- | --- |
| `deterministic_edit` (set, move, keep an exact value) | 0 | none: `VISUAL_REVIEW_NOT_WARRANTED` |
| `spatial_formal` (a meaningful spatial or formal batch) | 2 | `first_bundle` once, after the first complete execution. Then `after_repair` once, and only after `note_repair(addressed)` names findings of the last review. |
| `polish` (explicit "keep polishing") | 1–4, named by the request | `polish_round` |

- A refusal spends nothing. An admitted review stays spent even if the provider call fails.
- The Agent, not the channel, judges whether a finding is actionable. The Harness only requires that a follow-up answers a real finding.
- The allowance is in-memory loop state, not a project record.
- The benchmark suggests one refinement for the production caller: when the Agent attributes a finding to a condition the task preserves, it should ask the architect after the first review instead of spending the follow-up (§6.3).

## 5. Cost recording (#32)

- **Spans.** `observe_frames` wraps each review in a `visual_observation` `StudioMonitor.measure` span. The provider's `model_request` row, with its tokens, nests under it through the existing monitor context.
- **Span details** (existing vocabulary only):
  - `request_kind=visual_observation`;
  - `scope=visual-review:<domain>`;
  - `input_bytes`: image bytes;
  - `comparison_refs`: one `frame:<view>:<sha256>` per image, so the image count is recoverable;
  - `input_identity`: source, representation, provider and model;
  - `validator_pass`, `success`, and `output_refs` (the finding ids).
- **What Monitor showed.** `build_traces` over the benchmark's rows showed each review as its own trace, with the review span and its model request. Prompts and findings stay out of the log; a test asserts this.
- **Still missing.**
  - A dedicated image-count detail key; the vocabulary is closed.
  - Binding to a Hub turn id. That arrives when a Hub tool calls the channel with the turn context.

## 6. Benchmark

### 6.1 Design (criteria fixed before any run)

**Project and runtime**
- The project is a byte-identical copy of the installed Hub's test project; the original was never written. The base is one retained result (run `3639cf8b`, accepted as a Stage): four small corner rooms on one platform. They step up by half-levels around a central void and are linked by stair flights on the four edges.
- The runtime was `scripts/dev/run-project-runtime.ps1` with `ARCHFLOW_STUDIO_CAD_EXPORT=occt`.

**Vision provider**
- Codex CLI 0.153.4 through `StudioModelVisualProvider` (`CodexCompiler`, the CLI's default model, reported as `codex-cli-default`).

**Tasks**
- **(a) Deterministic.** Set every upper-room window to exactly 1.0 m wide (from 1.2 m). Criteria from readback:
  - each window 1.000 m wide, centred as before;
  - every other object unchanged;
  - relations held;
  - one parameter changed.
- **(b) Spatial/formal.** "Make the four corner rooms read as one composition around the shared void; the void should feel generous. Keep the nine-cell organisation, the half-level path, the windows and the roof construction." It was chosen because geometry can satisfy every hard rule while the composition still fails.
  - Hard criteria D1–D5, from readback:
    - execution and relations clean;
    - every object inside the platform;
    - void ≥ 3.9 m in both directions and clear;
    - half levels and flight heights unchanged;
    - unit footprints, window width, wall depth, roof thickness and roof rise unchanged.
  - Perceptual criteria P1–P3:
    - P1: the void reads as the dominant bounded space;
    - P2: the four units read as one ensemble;
    - P3: nothing floating, overhanging or colliding.

**Conditions**
- **A.** Readback only.
- **B.** A plus one review (top, front and right model views).
- **C.** B plus one repair and one follow-up review. The repair is chosen from the structured observation and readback only; the Agent does not see the images.
- A, B and C share the first execution. C's first review is B's.

**Who judged the perceptual criteria**
- No architect was available. P1–P3 were judged by the orchestrating agent as a proxy reviewer, from the final frames, after every verdict had been recorded. This is **not** human correction.

### 6.2 Results

An exec round is one proposal → candidate → readback cycle. Wall time is the sum of the API, execution, frame-rendering and provider spans. It excludes the orchestrating agent's own deliberation. Tokens are the provider-reported totals: input includes cached input, and output includes reasoning. Studio API calls exclude job polls, which are shown in brackets; (b) C also counts the one model-view read of the stale-frame check.

| Task / condition | Exec rounds | Vision calls | Images | Input / cached / output (reasoning) tokens | Studio API calls | Wall s | Visual reviews | Outcome | Changed by vision |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- | --- |
| (a) A | 1 | 0 | 0 | — | 6 (+54) | 78.7 | 0 | Complete; A1–A4 pass | — |
| (a) B | 1 | 1 | 1 | 19,212 / 0 / 479 (28) | 7 (+54) | 110.9 | 1 (forced; the policy refuses it) | Complete. 2 info findings; the observer cannot confirm exact widths. | No |
| (a) C | 1 | 1 | 1 | same as B | 7 (+54) | 110.9 | 1 | = B. No finding above info, so no repair and no follow-up. | No |
| (b) A | 1 | 0 | 0 | — | 6 (+27) | 50.8 | 0 | Claimed complete; D1–D5 pass. Proxy: P1 pass, **P2 fail**, P3 pass → false completion. | — |
| (b) B | 1 | 1 | 3 | 22,074 / 14,720 / 1,187 (63) | 9 (+27) | 119.1 | 1 | Partial. The void is verified generous; the composition gap is named; a question goes to the architect. | **Yes** (report) |
| (b) C | 2 | 2 | 6 | 44,247 / 14,720 / 2,396 (98) | 19 (+60) | 292.6 | 2 | Void tightened 4.2 → 3.9 m (D pass). The follow-up still sees separate towers; the budget is spent and the task escalates. Proxy: P2 fail. | **Yes** (geometry and report) |
| Board page | — | 1 | 1 (1600 px page) | 21,214 / 0 / 553 (0) | 2 | 30.2 | 1 | Same contract with a `page` source. 3 minor presentation findings. | n/a |

**Per-review costs**
- Provider time: 26 s (1 image), 49 s and 66 s (3 images), 27 s (Board page).
- Frame rendering by the model-view owner: 6.0 s for one view, and 19.3 s and 39.7 s for three views.
- One calibration call on the unchanged base (3 images: 22,080 input tokens, 1,239 output, 58.5 s) validated the transport and is not part of any condition.
- One text-only probe of the same transport (no image, a one-field schema) reported 17,321 input tokens, 15 output tokens and 14.7 s. This is the fixed cost every review pays.
- Total: 6 provider calls (5 reviews plus that probe), no quota or login errors, and every review answer valid against the schema on the first attempt.

**Stale-source check.** In condition C, the first result's top frame was offered to the review of the repaired result. It was refused with `VisualSourceMismatch` before the provider: 0 provider calls, and the budget was unchanged.

### 6.3 What the reviews showed

- **Task (a).** The review could only restate the request. It listed exact widths and "nothing else changed" as unresolved questions, and suggested the readback checks that had already passed. This confirms the policy of zero reviews for exact edits.
- **Task (b), first review.**
  - The observer confirmed the void now reads as a clear shared space (info, 0.98). It reported that in the side elevation the roof outlines are split by the wide void, and that the upper parts read as independent towers (minor, 0.89).
  - Readback cannot produce the second statement, and it is what makes A's completion claim false.
  - The observer also reported a gap between one flight's lower end and a corner room (minor). It asked itself whether that flight starts from the ground. Readback answered yes (bottom elevation 0.00), without another vision call.
- **Task (b), repair and follow-up.**
  - The Agent tightened the void to the smallest size the criteria allow, trading a little openness for cohesion.
  - The follow-up found the same split silhouette in both elevations.
  - The cause is the height spread fixed by the preserved half levels and roof rise, which a parametric tweak inside the preserve rules cannot change. The loop stopped at its budget, as designed.
  - The better policy would have sent this finding to the architect after the first review (§4).
- **Consistency and misses.**
  - The ground-start flight was flagged in all three reviews of this design: major 0.98 in calibration, minor afterwards.
  - The observer never mentioned that the re-centred grid left 0.3 m platform margins on two sides against 1.2 m on the others. The proxy reviewer saw that imbalance; the stated P3 criterion does not cover it.
- **Findings as structured data.** Every finding pointed at a stated criterion or preserve ref, so the Agent could act on it without reading the images.

### 6.4 Board check (issue step 6)

- **Frame.** A review sheet placed on the current Board scene revision was exported through `POST /api/board/export` and observed through the same `observe_frames`:
  - one page, 1600 px, PNG;
  - `page` source: run, asset, null revisionRef, page 0.
- **Request.**
  - `domain=board`;
  - criteria: hierarchy, legibility, and whitespace/crop;
  - one preserve condition: do not change the drawn content.
- **Result.** The same schema and validation applied, with no Modeling field. The findings were: three drawings of equal weight with no dominant one (minor); small dimension text (minor); an empty right half (minor); complete, uncropped drawings (info). The proxy reviewer agreed with all of them.
- **Gap.** The Board scene itself has no server-side, source-bound raster. A frame-level layout review needs one from `studio.board` (§8).

## 7. Drawing and Render integration points (not implemented)

- **Drawing.**
  - A drawing revision is a registered page with an exact `revisionRef` (the drawing receipt), so it is already a `page` source. Board export is its raster.
  - Criteria: hierarchy, legibility, label collisions and graphic density.
  - Deterministic checks run first, because the drawing owners know overlap, clipping, text overflow and dimension values exactly.
  - A cut plan should be reviewed only while `plan_status` (`API/application/drawing_plans.py:267`) says `current`.
- **Render.**
  - A render result is a registered document in its render run, so it is a `page` source.
  - The caller should pass the owner's `sourceState` and refuse `outdated`, rather than review a render of a model the project has left.
  - Criteria: composition, lighting, material read and visible artifacts. Findings lead to representation changes only, never to Design State.
- **Excluded until their owners verify pixels:** browser render views, viewport captures, Tracing Paper snapshots and client-rendered document visuals (§1.1).

## 8. Recommendation and next slices

1. **Route.** Add `POST /api/visual-reviews` in `studio.intent`. It resolves exact sources and renders frames in-process through the owners (`model_view`, `_page_raster`), so no caller-supplied pixels cross HTTP. It returns the observation and its usage. The Hub Agent calls it as a tool that holds one `VisualReviewBudget` per task loop, next to its turn context for Monitor. For spatial and formal tasks, the tool replaces the raw model-view image in the Agent's context; the raw read stays for views the user asks to see.
2. **Bundle.** For massing and composition, the smallest useful bundle measured here is top plus two elevations. An axonometric recipe is the missing view: `_elevation_view` (`API/application/drawings.py:84-117`) already takes any look/right/up vectors, so this is a small extension in the projection owner. It is held back because the drawing paths belong to other lanes. For a local edit, the policy default is no review.
3. **Cost.**
   - 17.3 k of every 19–22 k input tokens, and about 15 s of each review, are the Codex CLI's fixed agent prompt (measured by a text-only call). Each ≤1024 px frame added about 1.2 k tokens, and the 1600 px page about 3 k. A direct Messages or Responses provider behind the same seam would cut per-review input by several times. `AnthropicCompiler` already fits the seam.
   - The model-view owner should keep a transient per-(run, state, asset, view) cache; rendering the frames cost as much wall time as the review itself.
4. **Policy.** Before a repair, ask whether the finding's cause is a preserved condition. If it is, escalate after the first review. Add known readback facts (element elevations, clear dimensions) to the request, so the observer does not re-raise questions the runtime can answer.
5. **Admission (#294).** A task that declares a visual review can cite the observation's `review_id`, frame digests and resolved or deferred findings as C8 evidence through the admission record's existing evidence field. It does not grant authority and needs no new store.

## 9. Risks and open questions

- **Validity.**
  - One model, orthographic line drawings, two tasks, one run each: no repetition and no statistics.
  - The perceptual verdicts are a proxy, not the architect's. The architect's own judgement of P2 is the unverified acceptance.
  - The observer produced a confident false positive (the ground-start flight) and missed a margin imbalance.
- **Reproducibility.**
  - The CLI reports no concrete model id (`codex-cli-default` under `--ignore-user-config`).
  - The CLI offers no seed.
- **Latency.** One review (frames and provider) costs about as much wall time as one candidate execution. For task (b), B took 2.3 times the readback-only loop time and C 5.8 times.
- **Environment.**
  - The Studio's default `codex` executable name is not found by `CreateProcess` on this Windows machine; the benchmark tool resolves it with `shutil.which`. A Hub-launched runtime would need `ARCHFLOW_STUDIO_CODEX`. This was not checked in the installed Hub.
  - `test_intent_agent_timeout.py::test_the_agents_children_are_gone_after_the_timeout` is load-sensitive (1 s timeout). It failed in three runs while the benchmark runtime and vision calls were active. Later the whole file passed 7/7 twice on this branch and once on `main`. The channel does not touch that path.
- **Privacy.** Frames of a private project go to the configured provider, as the Agent's own images already do.

## Checks run

- `apps/archflow-studio/api/tests/test_visual_observation.py` (16 tests):
  - source binding refuses a stale digest before the provider;
  - budget allowance, ordering, exhaustion and polish bounds;
  - schema and semantic validation;
  - exact frames reach the Codex transport;
  - the receipt phase;
  - the Monitor span nests its model request and carries no prompt text.
- Affected suites: `test_monitoring.py`, `test_study_model.py`, `test_model_usage.py`, `tests/monkeymonitor/test_trace.py` and `tests/test_production_policy.py`.
- `python tools/archcheck.py` and `python tools/archcheck.py --changed origin/main`.
- The benchmark tool: `python tools/benchmark_visual_observation.py --help`. Its functions drove the live runs.
