# PR #330 follow-up — historical checks and cold persistence

Executed 2026-09-26 on code `1c4e821b7491d1fb559dac634065f7a6619cfcf9`,
integrating main `07e1a40be5cdf195d6b49d525ab3f2496dae2f0a`.
**Overall PARTIAL. PR remains draft; #319/#321/#324/#327 remain open.**
This supersedes the historical-gate and persistence rows in the
[earlier report](../README.md), not its unimplemented-feature boundaries.
Original `8afa896f`, input files and earlier evidence remain unchanged.

## Changes and ownership

- `tools.archcheck`: compatibility for a single explicit subject suffix `(#n)`
  only when no explicit work claim exists. The historical `GH-n` registry must
  exist; every path still satisfies the historical parent/lane claim. Explicit
  GH/P claims take precedence; incidental/ambiguous/malformed references confer
  no scope. No SHA allowlist, scope exemption, assertion removal or history rewrite.
  CONTRIBUTING documents the compatibility; new commits still use `GH-n`.
- `studio.artifacts` / existing conversion reader: main #339 made retained
  `_object` reads require a source/output role. The scene consumer omitted it,
  causing a real 500 on geometry selection and read. It now passes `role='source'`.
  Missing retained source returns `EXPORT_SOURCE_MISSING`; restoring the same
  bytes permits readback without changing selection or Design HEAD. No new owner.
- Existing Physical browser test waits for its disabled import control to become
  enabled and captures the actual Physical page/server log on failure. Original
  assertions and 30-second canvas deadline remain. This readiness change alone
  did **not** cure the 500; the second failed run proves that distinction.
- Main was merged without rewriting `8afa896f`. Conflicting generated clients
  and development maps were regenerated; current upstream closed claims were
  respected. #336/#339 work is upstream contribution, not credited to this PR.
- After the historical gate opened, full CI found missing classification of the
  three new record schemas in `test_version_refs`. Their actual writers carry
  artifact/content digests and sequences, not canonical project versions; the
  existing no-version table now records each reason. The coverage assertion is
  unchanged. Full CI also found the chat shell's old AI-default assumption: its
  explicitly synthetic fixture now answers empty Physical reads, the test
  asserts Physical is default and explicitly chooses AI before editing the same
  draft. Original AI draft/Board-return assertions remain.

## Required checks

The main branch API reports these five required contexts (not release publish):
`verify`, `Studio web`, `Studio first-run (ubuntu-latest)`,
`Studio first-run (windows-latest)`, `Windows desktop package`.
[Remote review snapshot](remote-review.json) includes the prior jobs, skipped
steps, and the four Issues' bodies/latest comments.

Prior head `a30e4bc4`: 4/5 required checks succeeded; `verify` failed at
Write scopes for 28 paths in `8afa896f`. Install and all subsequent backend
suites were **SKIPPED**, not PASS. The exact prior run is
[ArchFlow Verify 36222157218](https://github.com/cogco1/MonkeyHub/actions/runs/36222157218).

The historical gate now passes locally at the preserved history. The PR's live
Checks page is authoritative for the final pushed head; a local PASS or a run
for an earlier SHA is not all-five remote acceptance. CI links/status are updated
in the PR description after the evidence commit. No protected-branch settings
were changed. The draft status does not imply acceptance even if CI is green.

## Actual tests, including failures

| Execution | Result and evidence |
| --- | --- |
| New historical claim regressions before parser fix | 12 run, 2 FAIL: [log](claim-alias-before.log) |
| Full GH + legacy scope regression after fix | 61 PASS: [log](claim-alias-after.log); unknown issue, later registration, out-of-scope file and mandatory lane remain rejected |
| Historical range / static architecture | PASS: [range](pr330-scope-final.log), [tree](pr330-architecture-final.log) |
| Root conversion/Blender/record kinds | 45 PASS, no skip, real configured Blender: [log](pr330-root-tests.log) |
| Added upstream split-normal / legitimate-black regression | 1 pytest PASS: [log](pr330-conversion-regressions.log); separate from unittest discovery |
| API after main integration, before role fix | 4 FAIL, 55 PASS, 4 subtests PASS: [log](pr330-api-tests.log); all four failures are the missing role |
| First combined API/root pytest rerun | Collection ERROR due two packages named `tests`: [log](pr330-api-fixed.log). No tests counted as passed; rerun the packages separately |
| Same affected API suites after role fix, plus missing-source regression | 60 PASS + 4 subtests PASS, no skip, real Cycles: [log](pr330-api-fixed-rerun.log) |
| Browser first run / diagnostic rerun | Both fail at Physical import (500); [first](pr330-browser.log), [diagnostic](pr330-browser-rerun.log). 12 earlier cases pass; final cold Hub case not reached |
| Full browser after fix | 14/14 PASS: [log](pr330-browser-fixed.log). Includes all original AI-path assertions, camera orbit/pan/dolly, projection/scale, save/refresh, late-read race and cold Hub |
| TypeScript/Vite production build | PASS, chunk warnings retained: [log](pr330-build.log) |
| First remote verify after historical fix | 1678 tests: 1 FAIL / 64 environment skips, missing schema classification: [excerpt](ci-version-refs-failure.log). Later suites did not run |
| First remote Studio web | FAIL in chat shell AI input lookup: [excerpt](ci-chat-shell-failure.log). Other drawing/tree browser steps still ran; not an all-web PASS |
| Version-reference classification after completion | 23 PASS: [log](pr330-version-refs.log); existing coverage assertion retained |
| Full chat shell browser after explicit AI selection | PASS, 139 fixture writes, errors empty; both camera screenshot comparisons remain zero differing pixels: [log](pr330-chat-shell.log). Synthetic shell fixture, not Penguin product evidence |

The browser uses an explicitly offline AI adapter. Live paid AI generation is
NOT TESTED. Expected injected 503/network failures and 404 noise remain in logs.
No new Penguin Cycles image was rendered in this review; the original real job
and the newly run tiny real Cycles regression are separate evidence.

## Actual Penguin save/reopen loop

All edits below used the visible product controls. Close/open used the actual
Hub lifecycle API and verified process exit, followed by IAB page reload;
the close-menu UX itself is NOT TESTED.

1. Backed up saved scene/drawing records. Selected orthographic camera, position
   `[1.4,-2.1,.85]`, target `[.05,0,.55]`, up Z, FOV 32, ortho height 1.5;
   roughness .67, key intensity 180, EV .4, 1000×1200, 96 samples, denoise.
   Clicked **Save scene**. Revision became `1c499c0de19c…`; geometry remained
   `b8fac80bbbca…`. This was an actual changed state, not merely reopening old data.
2. Clicked **Generate / update four views** from the same retained geometry,
   with `eye-center` explicitly retained as **unresolved**, not a stable CAD ID.
   Four new immutable records were added; all original eight remained.
3. Refreshed the whole page; scene and drawing responses exactly matched saved
   responses. Camera fields in the DOM matched the saved numeric values.
4. Switched Modeling → Board → Drawing → Render. Modeling's neutral derivative
   loaded; Board loaded and reported updated pages. Drawing still reported
   **no retained working state**. No claim of OCCT binding or Board association.
5. Closed Penguin: worker **35936** reached stopped/null PID and the process
   was absent. Reopened the same P036 directory: worker **40936** became ready.
   Scene and all **12 records (8 current, 4 stale)** matched exactly, including
   camera/material/region/light/background/render settings and drawing recipes.
   [Scene before](saved-scene.json), [after](reopened-scene.json),
   [drawings before](saved-drawings.json), [after](reopened-drawings.json),
   [camera DOM](reopened-camera-ui.json), [camera screenshot](reopened-camera.png),
   [actual preview](reopened-preview.png).
6. Independently reopened all four new SVG/PNG bytes and checked their SHA256.
   Projected actual geometry vertices along each recipe's right/up axes and
   compared extents to recorded dimensions: **maximum error 0 m** in this mesh.
   Front/side height 1000 mm; dimensions and unresolved references survived.
   [Numerical evidence](persistence.json), [front](front.svg), [side](side.svg),
   [top](top.svg), [isometric](isometric.svg). This is not an OCCT editing test.
7. Restored original saved scene **content and digest** `06a5995bc2ad…` through
   the UI; new immutable test records remain. A new scene sequence and binding
   to current selection sequence 3 replace historical metadata sequence 1,
   so the entire restored response is not falsely described as byte-identical
   to the initial response. [Initial](initial-scene.json), [restored](restored-scene.json).
   Design HEAD stayed version 0 / `cad0ab2377fa…`; source bytes were unchanged.

The snapshot immediately after an automation scroll showed a transient blank
canvas and unsaved state: [raw screenshot](saved-preview.png). Full refresh
restored the saved camera and visible model, with records unchanged. That instant's
camera/texture timing was not captured, so its cause is **NOT ESTABLISHED**.
Do not call the historical “background only” problem fixed. The separately
reproduced geometry API 500 has a proven cause/fix. No model was deleted,
reimported, fitted automatically or geometrically changed for this acceptance.
Board dynamic-module failure was not reproduced; [loaded Board](board.png).

## Uncovered acceptance mapped individually

Statuses below concern acceptance coverage, not a claim that every Issue is done.

| Issue / requirement | This review and remaining scope |
| --- | --- |
| #319 retained scene and cold persistence | PASS for the exact imported Penguin loop above; three-model generality NOT TESTED |
| #319 imported geometry revision → stale/update | Prior 1000→1100 mm explicit test-copy evidence retained; this review regenerates at original revision. Not a new OCCT edit acceptance |
| #319 actual Modeling / automatic OCCT Working Head | FAIL / not implemented for this project; [Drawing page](drawing-workspace.png) still has no working state |
| #319 bidirectional camera orbit, pan, dolly/zoom | Not implemented; each cross-workspace gesture NOT TESTED this review. Physical-only browser gestures PASS |
| #319 perspective/ortho, presets, FOV/target/up parity | Prior actual negative two-way check remains FAIL; new Physical orthographic cold restoration PASS, no live shared camera |
| #319 remount/resize/aspect/output-gate parity | Physical workspace/whole-page restoration PASS; feedback loops, latest Modeling camera, 16:9/4:3/1:1, three models NOT TESTED |
| #319 newest camera freeze → Cycles / later edits | Existing API real-Cycles pin/outdated regression PASS; live gesture from either workspace → Penguin Cycles NOT TESTED |
| #319 Board engineering Side current/stale/update/reopen | PARTIAL: module/page update visible; full model-associated Side chain NOT TESTED. A Cycles image is not engineering drawing evidence |
| #319 missing-model/background-only diagnosis | PARTIAL: new 500 fixed; transient blank capture and historical incident not proven resolved |
| #321 generic model/user-selection regions, CRUD/highlight | PARTIAL existing coarse regions only; hardcoded `penguinSuggestions` remains. Generic structure/selection-driven workflow not implemented |
| #321 same-name regions, multi-model/project selection isolation | NOT TESTED; Penguin is not the generic acceptance matrix |
| #321 material chat, candidate preview/accept/reject/undo | Not implemented by this PR; existing unrelated candidate infrastructure is not material workflow proof |
| #321 UV/texture/mask upload and invalidated region rebinding | NOT TESTED end to end; no UV invented, source geometry unchanged |
| #321 eye/belly boundary diagnosis and generic repair | NOT TESTED controlled neutral/material-ID/wireframe comparisons; jagged boundaries visible in preview. No smoothing/remeshing to hide them |
| #321 scene material vs exported appearance | Existing Three.js/Cycles assignment evidence preserved; production 3DM remains neutral, not a complete appearance export |
| #324 existing photo binding / reopen | PASS for the selected Antarctic resource in this saved scene; original Cycles photo evidence remains prior-run evidence |
| #324 generic upload/preview/select/replace/unbind/restore UX | PARTIAL / not implemented as the complete resource workflow; project data is not a universal upload UX |
| #324 same filenames, slow uploads, rapid replacement, missing decode | NOT TESTED project A→B→C→A / multi-scene adversarial matrix |
| #324 project ownership/version admission, historical assets | Existing stable references used here; full UI/API failure/isolation acceptance NOT TESTED |
| #324 resource roles, framing, alpha, HDRI | Photo background is not HDRI, depth, shadow or DOF. Advanced formats/lighting/framing remain uncovered |
| #327 retained source error integration | PASS for source-role read, missing source diagnostic, same-byte recovery with unchanged selection/HEAD; narrow regression only |
| #327 #336/#339 upstream slices | Their comments report split normals/legitimate black and missing-file retry on main; this PR reuses them, and separately runs the real upstream split-normal test |
| #327 preflight, attributes, units/transforms, independent consumers | PARTIAL prior Penguin quantitative readback; comprehensive attribute/transform/consumer matrix NOT TESTED here |
| #327 purpose gates, quarantine, cancellation/partial write/timeout | NOT TESTED complete UI/API admission and failure isolation |
| #327 reversible repair, historical cache/version recovery | NOT TESTED complete candidate/rollback/history workflow; preserving originals here does not prove it |
| #327 Penguin + architecture + furniture end to end | NOT TESTED; browser architectural fixture is bounded regression, not three-model certification |

Source requirements: [#319 camera comment](https://github.com/cogco1/MonkeyHub/issues/319#issuecomment-5843313615),
[#321](https://github.com/cogco1/MonkeyHub/issues/321),
[#324](https://github.com/cogco1/MonkeyHub/issues/324),
[#327 latest follow-up](https://github.com/cogco1/MonkeyHub/issues/327#issuecomment-5846330720).

## Reproduction and artifact handling

Run from the repository with its Python/browser dependencies:

```text
python -m unittest tests.test_archcheck_github_claims tests.test_archcheck_scopes -v
python tools/archcheck.py --changed origin/main
python tools/archcheck.py
node apps/monkeyhub/web/workspaces/test/renderWorkspace.browser.mjs
python -m pytest tests/test_model_conversion_regressions.py -q
```

Run the five affected API files listed in the API log separately from root tests,
with repo + Studio API on PYTHONPATH and ARCHFLOW_BLENDER_EXECUTABLE configured.
The existing browser runner documents PYTHON and PLAYWRIGHT_MODULE overrides.
Full real Penguin reproduction needs the preserved original local inputs; the
portable architectural browser fixture does not. Compare JSON before/after and
hash SVG bytes independently; do not accept screenshots alone as persistence.

Only bounded logs, JSON, actual screenshots and geometry SVGs are published.
No source GLB, background original, .blend or complete P036 project was added.
[Manifest](manifest.json) records content hashes with Git LF text normalization;
screenshots are unmodified. Original raw transcripts and prior delivery remain.
