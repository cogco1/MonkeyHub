# Two-Stage Change — Fast Preview, Exact Commit (P108, next development)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans, in-session (Kaiwen: planning and
> the UI are Fable's; reviews go to fresh subagents). Steps use checkbox (`- [ ]`) syntax.

**Goal:** make a change feel immediate — intent → typed proposal → ghost preview in well under a second —
while the exact stage (geometry export, validation) keeps its cost and its honesty; profile every stage so
nothing is rebuilt that did not change; let the agent find the control variable once and hand refinement to
direct manipulation; bind gestures to the model so "this one, up a bit, keep that" is a fact the agent reads.

**Architecture:** no kernel edits (gaps become cards). The API grows timings on the wire, a gesture-aware
intent request, and a dependency-aware job queue; the web shell grows a ghost layer in the viewer, a
refinement slider on the proposed-change card, an annotation overlay, and three visible states —
CURRENT → GHOST PREVIEW → VALIDATED. Principles that bind: **Fast Preview ≠ Validated Commit. Preview can
be approximate. Commit must be exact.** Ambiguous intent → agent once; known operation → deterministic.
Slow because we are validating is acceptable; slow because we rebuilt what did not change is not.

**Sources:** `.superpowers/sdd/kaiwen-feedback-2026-09-03-b-performance.md` (the note),
`kaiwen-feedback-2026-09-03.md` (five questions, Before/After/Why, parallel runs), and Kaiwen's third note on
annotations (select + draw + say; Intent = Language + Selection + Gesture + Viewpoint).

## Global Constraints

- Honesty laws unchanged (README §6): the browser decides no verdict, invents no digest, hides no server
  sentence; the three relation states never collapse. A ghost is a *drawing* of the proposal's numbers,
  labelled approximate on the source chip and the card, never listed as a version, never in the receipts.
- `viewer/ThreeDmViewport.tsx` changes only by one additive controller method (`ghost`) and one annotation
  overlay hook; the load/pick paths stay as they are.
- Kernel (`archflow/`) untouched; every kernel gap below is a card relayed to the main session.
- API changes keep the one error body; new DTO fields are additive; `npm run api:generate` + `api:check` after
  each schema change; api tests on real records, no mocks; archcheck PASS.
- Write scope `apps/archflow-studio/**` and dated lane docs; explicit-path commits with the trailer
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; smoke only on a temp copy of a project.

---

## A. Where the time goes today (measured 2026-09-03, villa temp copy)

| stage | code | measured | class | rebuilds what did not change? |
| --- | --- | --- | --- | --- |
| agent compiles an abstract sentence | `application/intent_agent.py` `CodexCompiler.compile` (whole `record_sheet()` per call) | 45.1 s | T_reasoning | **yes**: the sheet is identical between calls; the agent has no memory and re-reads all 41 components + elements every time |
| grammar sentence → typed proposal + closure | `application/intent.py` `DeterministicIntentProvider.propose`, `impact.py` | ~0.1 s (route < 0.2 s) | validating | no |
| candidate harness run, no export | `application/candidate.py` `execute_candidate` → `run_project` | 0.13–0.25 s | validating | no |
| Rhino export per seat | `archflow/runtime/project_runner.py` `_export` (~455–528): `_prior_export` at :476 looks only in **this run's** records (`records_dir = layout.run(run.run_id).records`) — a candidate is a fresh run, so the patch branch is never taken and every seat is `rebuild` | 39.4 s + 39.7 s, serial | T_geometry | **yes, twice**: both seats rebuilt in full though one element changed; and each export starts its own `Rhino.exe -Embedding` (host witness), so ~40 s of each is Rhino startup |
| validation receipt + verdict | `application/validation.py` | ~1 s | validating | no |
| export download + parse | `GET /api/artifacts/{sha}/bytes` (2 MB) + `Rhino3dmLoader.parse` | ~0.5 s | T_transport + T_render | whole model reloaded per preview (acceptable now) |
| total, one change with export | | ≈ 80 s (+45 s when the agent is asked) | | |

Reading: the kernel is milliseconds; the two long poles are the agent (re-reading a static sheet) and the
geometry exporter (full rebuild per seat + a Rhino start per seat). Both are "rebuilding what did not
change". Validation is about 1 % of the wall time.

## B. Phase 0 — instrument first (no behaviour change)

- API: `CandidateDto.timings: TimingsDto { runS, exports: [{seatId, stageId, path: "rebuild"|"patch", seconds, rebuiltObjects, keptObjects}], validationMs }`
  read from what the runner already retains: the seat row's `cad` block (`path`, `seconds`; `rebuilt_objects` /
  `kept_objects` when `path == patch`) and `wall_time_s` (`project_runner.py:492-497`). No kernel change.
- `IntentDto.agent.latencyMs` exists; add `IntentDto.timings { compileMs, typeMs }`.
- Web: candidate card footer `78.5 s · kernel 0.2 s · export 39.4 s + 39.7 s (rebuild)`; Evidence → Receipts
  gains a `Timings` block; the ledger records **T_full vs T_incremental** per candidate once Phase 3 lands
  (the research metric: computation leakage beside mutation leakage).
- Also record `rebuildRatio = rebuiltObjects / (rebuiltObjects + keptObjects)` when the receipt carries it.

## C. Phase 1 — ghost preview (the fast stage)

The proposal already *is* the fast stage: a grammar sentence returns Target / Change / Will update / Keep in
under 0.3 s. What is missing is the picture. When a `proposal` entry lands:

1. **Ghost in the viewer.** `ViewportController.ghost(spec | null)` with
   `GhostSpec { elementId, objectPrefix, field, factor, axis: "z", affected: string[] }`: the viewer finds the
   loaded objects whose `archflow:object_ref` / name begins with `obj-<elementId>` (the convention the server
   resolves picks by), clones their meshes into a translucent accent-coloured group, and for `field ==
   "height"` scales the clone along world Z by `new / old` about the clone's bounding-box bottom; for any
   other field it draws the clone unscaled (highlight only) — a scale we cannot justify is not drawn.
   Objects of `affected` element ids get a faint translucent highlight (the "impact cloud"). The original
   geometry stays visible; `ghost(null)` removes the layer.
2. **Three states on the source chip:** `CURRENT` (the loaded model as certified), `GHOST PREVIEW ·
   approximate` while a ghost is drawn, `VALIDATED · may advance` / `VALIDATED · blocked` once a candidate's
   export is loaded and its verdict read. The ghost never appears in the versions strip.
3. **PREVIEW card** = the proposed-change card with one added line: `ghost shown · approximate — Apply for the
   exact geometry`. Adjust or a new sentence replaces the ghost; Apply keeps it until the candidate's export
   replaces the model, then removes it.
4. Honesty: the ghost is computed from `change.old → change.new` and the element id the server named; the
   card says "approximate"; the drawer's Receipts never mention it. Nothing about it is a claim about
   geometry.

## D. Phase 2 — agent once, then direct manipulation

After a proposal, the card gains a **refinement control** bound to `target.key`: a slider from
`old × 0.5` to `old × 1.5` (element fields) or the parameter's range when declared, origin at `change.old`,
current at `change.new`, plus `−` / `+` steps of 1 % of `old`. Each release (debounced 250 ms) sends
`set <key> to <n>[ keep …]` through `POST /api/intents` — the grammar short-circuit means no agent call
(under 0.3 s) — and the answer replaces the proposal in the same transcript entry (the entry keeps
`refinements: n`; every proposal stays in the server's store). The `keep` clause is carried from the compiled
sentence. `STALE_BASE` re-projects and says so. The agent is asked again only when a new sentence arrives.

## E. Phase 3 — incremental rebuild scope (kernel cards + one studio option)

- Kernel card K-A: `RunOptions.patch_base` (a prior model path + its program record, or a `prior_run_id`)
  so `_export` can take the **reference run's** export as `RhinoPatchBase` for a candidate; today
  `_prior_export` searches only the same run (`project_runner.py:476`). With it the receipt reports
  `export_path: patch`, `rebuilt_objects`, `kept_objects` — Rebuild Ratio for free.
- Kernel card K-B: one Rhino process per run (or a warm Rhino) instead of one per seat; the host witnesses
  show a fresh `-Embedding` Rhino per export and ~40 s of each seat is startup.
- Kernel card K-C: seats exported in parallel, or only the seats whose programs changed (`program_digest`
  differs from the reference run's).
- Studio: when K-A lands, `execute_candidate` passes the reference run's export as the patch base and
  `patch_oracle` under a settings flag for the experiment; the card shows `rebuilt 1 of 60 objects · 4.2 s
  vs 39.7 s full`. Experiment: villa envelope, T_full vs T_incremental across candidate sizes.

## F. Phase 4 — parallel candidates, dependency-aware queue

`JobRegistry` runs one worker (`application/jobs.py:70-71`, `ThreadPoolExecutor(max_workers=1)`). Add a queue
policy: two candidates conflict when their closures intersect (`impact.direct ∪ propagated ∪ {target}` of
their proposals, plus `protected`); disjoint candidates may run on parallel workers (kernel-only runs are
cheap; exports stay serialised until K-B); a conflicting one waits with `JobDto.waitingFor: <candidateId>`
and `CandidateDto.queue: "parallel" | "waiting"`. Cards: `RUNNING · West portico height ███░ validating ·
Roof cornice · waiting for West portico height`. The DAG is never drawn; it shows as behaviour.

## G. Phase 5 — annotations: select + draw + say

An overlay canvas on the stage with three tools — **circle/lasso** ("this area"), **arrow** (direction /
move / extend / compress), **keep ✓ / remove ✗** marks. A stroke is sampled; each sample is raycast into the
loaded model (the viewer's existing raycaster) and the hit objects' user strings, the world points and the
camera are recorded:

```
GestureDto { kind: "circle"|"arrow"|"keep"|"remove", screen: [[x,y],…],
             camera: {position, target, up, fov}, hits: [{objectName, userStrings, world:[x,y,z]}],
             worldStart, worldEnd, worldDirection (unit), lengthModelUnits }
```

`IntentRequestDto.gestures: GestureDto[]`. The server resolves each hit through the pick resolver (element
ids, never guessed), and the record sheet gains a `gestures` section in facts: `arrow on
portico-roof-abutment-west · world direction +Z · length ≈ 0.4` / `circle covering portico-roof-abutments
(4 elements)` / `keep mark on pediment-west`. Deterministic parts stay deterministic: a keep mark appends
`keep entity:<id>`; a circle sets the selection to the component holding most hits. The agent reads
"the user expressed a +Z change on the west portico roof and protected the pediment", not "there is an
arrow in the picture". Intent = Language + Selection + Gesture + Viewpoint; the compiled sentence and its
`why` still show who decided what. Ideal path: pick → circle → arrow → "a little taller, keep the
pediment" → ghost in under 1 s → "yes" → Apply.

---

## H. Tasks

### Phase 0
- [x] **T0.1 timings on the wire** — `transport/candidate.py` `TimingsDto`; `application/candidate.py` reads
  `cad.path/seconds/rebuilt_objects/kept_objects` per seat and `wall_time_s`; `IntentDto.timings`; tests on
  real receipts (`support.retain_rhino_receipt` carries `seconds` and `export_path`); regenerate the client.
  Commit `P108: every stage says how long it took`.
- [x] **T0.2 timings on screen** — candidate card footer, Evidence → Receipts `Timings`, ledger metric line.
  Commit `P108: the candidate card shows where the seconds went`.

### Phase 1
- [x] **T1.1 viewer ghost layer** — `ThreeDmViewport.tsx`: `ghost(spec | null)` on `ViewportController`;
  clone + translucent material (`--accent` at 0.35), Z-scale for `height` about the bbox bottom, highlight
  group for `affected`; disposed on `clear()`, on a new load and on `ghost(null)`. Manual check in the pane.
  Commit `P108: the viewer can draw a ghost of a proposal`.
- [x] **T1.2 three states + preview line** — `App.tsx` calls `ghost(...)` when a proposal entry lands
  (`target.elementId`, `change.old/new`, `target.key`, `impact.propagated` → element ids) and `ghost(null)`
  on Adjust / new sentence / export loaded; `SourceChip` state tag CURRENT / GHOST PREVIEW / VALIDATED;
  proposed-change card line `ghost shown · approximate`. Commit `P108: CURRENT → GHOST PREVIEW → VALIDATED`.

### Phase 2
- [x] **T2.1 refinement control** — `ProposalCard` slider + steps; `App.refine(entryId, value)` sends the
  deterministic sentence with the carried `keep` clause; entry updated in place with `refinements`.
  Commit `P108: after the agent finds the variable, the hand refines it`.

### Phase 3
- [ ] **T3.1** cards K-A / K-B / K-C to the main session; **T3.2** studio option + experiment once K-A lands.

### Phase 4
- [x] **T4.1** queue policy in `jobs.py` (conflict = closure intersection; `waitingFor`); **T4.2** cards.

### Phase 5
- [x] **T5.1 overlay + gesture capture** (stage overlay canvas, raycast per sample, `GestureDto` on the
  client); **T5.2 server side** (`IntentRequestDto.gestures`, resolution, sheet facts, deterministic keep /
  selection); **T5.3 agent prompt** additions and tests on real records with scripted compilers.

Order of execution: 0 → 1 → 2 → 5 → 4, with 3 whenever the kernel cards land. Phase 1 is the one that
changes how the product feels; it ships before any new modelling capability.

---

## I. Review addendum (second Fable session, 2026-09-03 15:30 UTC) — corrections and added tasks

Context: Kaiwen asked both sessions to write this plan after the first session's process was torn down
(interrupt + rewind at 10:04 local, two subagents lost). The first session had already committed the plan
(fb0b91c), Phase 0 (df7c8d6, a5e6c51) and was mid-Phase 1 in its working tree when this review was made. This
addendum does not restate the plan; it corrects three claims against the code and adds the tasks the sources
ask for that the plan does not yet carry. The working tree's Phase 1 files were not touched by this session.

### I.1 Corrections to section A

- **The agent's 45 s is not the sheet.** On the villa temp copy `record_sheet()` serialises to ≈ 0.9 KB
  (4 elements, 0 parameters, 5 honesty lines; measured from `/api/state`), and a trivial `codex exec` with
  the same flags answers in 6 s. The cost is the agent process and its reasoning per call, not re-reading a
  static sheet. The lever is therefore Phase 2 (ask once) plus `ARCHFLOW_STUDIO_INTENT_MODEL` / reasoning
  effort, and `IntentDto.timings.compileMs` is the number to watch — not sheet slimming. Section A's
  "rebuilds what did not change: yes" for the agent row should read "no; the cost is per-call reasoning".
- **`_prior_export` root cause confirmed** (`archflow/runtime/project_runner.py:434-453`): it globs
  `records_dir = repository.layout.run(run.run_id).records` and the model must be under *this run's*
  workspace, so a candidate run — unique id, fresh workspace (`application/candidate.py:159-170`) — never
  finds a prior and every seat is a full rebuild. K-A is the right card; the studio cannot fake a prior
  record into a new run without inventing a receipt.
- **Parallel candidates: the repository is not the risk.** `create_run` holds only the process-local
  `self._lock` (`archflow/project/repository.py:513-541`), run directories are per run id, and no candidate
  route writes HEAD. The one shared resource is Rhino COM (one `-Embedding` process per export, PID-owned).
  Phase 4 can run kernel-only candidates on parallel workers today; exports stay serialised until K-B.

### I.2 Added tasks

- [x] **T0.3 codex timeout must kill the tree (API).** `CodexCompiler.compile` uses `subprocess.run(timeout=)`
  (`application/intent_agent.py:307-316`) on `codex.cmd`, a cmd shim that spawns `node codex.js`. On Windows
  a timeout kills `cmd.exe` only; `communicate()` then waits on pipes the orphaned `node` still holds, and the
  route hangs past the 120 s it promised. Fix: `Popen(..., creationflags=CREATE_NEW_PROCESS_GROUP)` and on
  `TimeoutExpired` run `taskkill /T /F /PID <pid>` before reading, or resolve the shim to
  `node <…>/@openai/codex/bin/codex.js` and call it directly. Test with a scripted executable that sleeps past
  the timeout and holds stdout open; assert the route answers `502 INTENT_AGENT_FAILED` within timeout + 2 s.
  Commit `P108: a codex that does not answer is killed with its children`.
- [x] **T2.1 precision.** Slider domain for a unit-less element field: `[old × 0.5, old × 1.5]` clamped to
  `> 0`, step `1 % of old`; the `set <key> to <n>` sentence carries `n` rounded to 6 decimals (the grammar's
  `ROUNDING`). A release while a refinement is in flight is coalesced (last value wins), the entry keeps
  `refinements: n`, and the ghost is redrawn from the *new* proposal's `change.new` — never from the slider
  value, so the picture is always the server's number.
- [ ] **T3.2 experiment harness on the wire.** `options.patch_oracle` already runs the full rebuild beside
  the patch and writes `cad.oracle.seconds` (`project_runner.py:519-526`); that pair *is* T_full vs
  T_incremental in one run. Add `ExportTimingDto.oracleSeconds` and `oracleEqual` (null when no oracle ran) so
  the research metric is read off the receipt, not the ledger.
- [x] **T4.0 concurrency proof before T4.1.** An api test that submits two kernel-only candidates against one
  temp project on two workers and asserts two run directories, two receipts, no shared record and HEAD
  unchanged. Only then raise `max_workers`.
- [ ] **T6 Before / After / Why (Kaiwen's feedback item 4, missing from the plan).** Phase 6, after Phase 2:
  `GET /api/candidates/{id}/compare?against=<runId>` reads the two runs' retained `seat-3dm-inspection`
  records (`named_object_bboxes`, written at `project_runner.py:499-501`) and answers per element
  `changed | unchanged | added | removed` with the bbox delta — kernel facts, no browser inference. The
  version card gains **Compare**: the viewer loads both exports and cross-fades them (a second additive
  controller method, `compare(a, b, t)`), and the card reads `WEST PORTICO · changed because "<utterance>" ·
  affected 4 · unchanged 27`. Ships before Phase 4; it is the sentence ArchFlow sells.
- [ ] **T7 versions strip per run.** Group `VersionsStrip` cards by `runId` (reference first, then candidates
  newest first, older folded behind a count) — six cards after two candidates is already noise.
- [ ] **Global Constraints, viewer line.** Phases 5 and 6 need two more additive controller methods
  (`raycast(screenPoints)` for gesture samples, `compare(a, b, t)`); amend "only by one additive controller
  method" to "only by additive controller methods; load and pick paths unchanged".

### I.3 Order after this review

0 (incl. T0.3) → 1 → 2 → 6 → 7 → 5 → 4 (with T4.0 first), and 3 whenever K-A lands. Phase 1 remains the
one that changes how the product feels.
