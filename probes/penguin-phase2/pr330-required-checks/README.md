# PR #330 required-check diagnosis and bounded correction

2026-09-26. **Draft / overall PARTIAL.** Related to #319, #321, #324, #327;
none is complete. This supplement preserves [the preceding report](../review-pr330/README.md)
and the original `8afa896f` history. It does not certify D5 rendering or add product features.
PR branch: `codex/319-penguin-scene`. Local delivery branch: `codex/pr330-ci-delivery`.
The PR description records the final published SHA and its five actual check results;
earlier checks below are diagnosis, not final-HEAD acceptance.

## Baseline and evidence limitations

- Starting HEAD `2057f486`: verify and both first-run checks passed; Studio web
  and Windows desktop failed. [Web failure](https://github.com/cogco1/MonkeyHub/actions/runs/36257572784/job/108447156174),
  [desktop failure](https://github.com/cogco1/MonkeyHub/actions/runs/36257572696/job/108447155989).
  Failure excerpts (trailing whitespace removed for Git checks): [web](2057-web-failure.log), [native](2057-native-failure.log).
- That historical run did not upload camera PNG/state or the rejected file path.
  Those missing observations cannot be reconstructed or claimed to have been collected.
- Diagnostic-only HEAD `92a62c82` retained all old assertions and the old file scan.
  CI tested merge tree `ec40937e` with new main `6b2b0ecc` (Stage chrome from #337).
  Web and native checks passed on this diagnostic run: **not evidence of a fix**.
  Its scope gate failed on the synthetic merge's changed browser file; backend
  suites therefore did not execute. [Failure](92a-scope-failure.log).
  Main was then normally merged as `7bc906a3` with the existing GH-319 claim;
  scope enforcement was not weakened or bypassed.
- Separate uncommitted scene-history/chat-interface work appeared in the shared
  checkout. It was preserved and excluded: final testing and delivery use a new
  isolated worktree. No force push, reset of others' work, merge or Issue closure.

## A. Camera screenshot failure

**Established test defect:** `locator('canvas').screenshot()` captures the
composited page rectangle, including the toolbar and editing-base card over it.
It is not an image of only the Three.js canvas. This made a camera-preservation
assertion depend on unrelated DOM compositing.

Controlled Windows Chrome 154 / ANGLE Vulkan SwiftShader reproduction at the old
layout produces **14 differing overlay text pixels, max delta 1**, while the
underlying WebGL image has **zero differing pixels** and camera/viewport values
are **exactly equal**, excluding observation timestamps. See
[comparison](camera-analysis.json), [raw state before](overlay-reproduction/before-switch.json),
[after](overlay-reproduction/after-switch.json),
[expected screenshot](overlay-reproduction/switch-comparison.expected.png),
[actual](overlay-reproduction/switch-comparison.actual.png),
[diff](overlay-reproduction/switch-comparison.diff.png),
[raw before](overlay-reproduction/before-switch.raw.png),
[raw after](overlay-reproduction/after-switch.raw.png).

The original five Linux pixels at x=667/668, y=801..803 fall on the lower-right
toolbar corner in the same 755x920 layout, not the model. This supports the same
failure mechanism, but the exact old compositor timing is **not recoverable**.
The separate historical-size refresh replay produced zero differences; it must
not be reported as reproducing those exact five old pixels.
[Refresh screenshot](historical-size-refresh/before-refresh.png),
[refresh state](historical-size-refresh/after-refresh.json).

The minimal correction:

- Existing ThreeDmViewport exposes a read-only event **only when a test explicitly
  marks its canvas**. It reports the camera actually used to draw: position,
  quaternion, target, up, projection, FOV, zoom/frustum, clip planes, both matrices,
  model identity, drawing size, DOM rectangle, pixel ratio and DPR. It never sets
  camera state or schedules rendering. The existing renderer already preserves
  its drawing buffer; this change does not enable that option.
- The same browser scenarios still require **exact state equality and zero decoded
  WebGL pixel differences**. All model identity, retained canvas and no-reinstallation
  assertions remain. No tolerance, baseline re-recording or skipped original scenario.
- Full composited PNGs/diffs remain diagnostic artifacts. Screenshot capture occurs
  before GPU readback, so diagnostics do not flush the GPU before that screenshot.
  Request/response/finish and render/input timestamps, environment and PNGs are
  uploaded on failure as well as success.
- Test both the current 755x801 canvas and the historical 755x920 dimensions.
  This is candidate refresh preservation, **not Modeling ↔ Physical camera sync**.

Owners: existing `hub.shell` viewport and test. Modification locations:
`ThreeDmViewport.tsx` render observer; `chatShell.browser.mjs` capture/comparison
and two-height refresh scenario; existing verify workflow artifact upload.

## B. Windows native reopen read

**Deterministically established scanner defect:** the old `project_bytes()` read
P036's advisory `HEAD.lock`/`design/branches.lock` as if they were persistent content.
Windows denies a read overlapping the byte-range lock during an ordinary guarded
repository operation. Python reports errno 13 without a filename/native winerror.
An independent read-only `CreateFileW` succeeds, then `ReadFile` reports
**33 / ERROR_LOCK_VIOLATION**, not a filesystem ACL denial.

[Before-fix real-process reproduction](diagnosis/330-lock-before.log),
[two forced regressions](diagnosis/330-lock-regressions.log), and
[lock owner/release events](lock-events/) retain file paths, actual holder PID,
launcher PID, exceptions, native error and timestamps. After release, the original
whole-file dictionary was identical. No accepted task was still writing project
content in this controlled case: the child held the existing P036 lock only.

The old CI's exact rejected file remains **unknown** because its exception omitted
the path. The diagnostic CI passed all eight native tests and recorded clean process
drain/reopen, rather than reproducing that old failure. [Actual CI lifecycle events](diagnosis/92a-native-reopen-events.json).
Therefore this report establishes and fixes the scanner/lock incompatibility,
not a claim that it recovered a missing historical trace or proved a handle leak.

The minimal correction stays in the existing desktop lifecycle test owner:

- Get the two exact advisory paths from `FilesystemProjectRepository.lock_paths()`.
  Exclude those only, as specified by the existing P036 persistence/transfer contract.
  Do not glob `*.lock`, ignore arbitrary temporary files or catch-and-continue.
- Keep the full original byte-dictionary assertions for all real project content
  before/after open, accepted work, native close, reinstall and reopen.
- Real Windows child processes force both owner locks; the old read fails and the
  corrected snapshot remains exactly equal during and after release.
- Negative control: `user-asset.lock` is still data, is compared in full, propagates
  PermissionError when locked, and a content modification changes the snapshot.
- Capture exact failing path, operation, full stack, errno/winerror, independent
  native read and owned-process exit state; rethrow unexpected content read errors.
  Existing close/drain/reopen implementation and security settings are unchanged.

The unrelated ChatGPT temporary screenshot-path failure is not used as an explanation.

### Follow-up from the first corrected CI run

HEAD `49563f72` passed Studio web and both first-run checks, but Windows native
setup failed **10/10** before exercising the lifecycle: runner temporary paths used
`C:/Users/RUNNER~1/...`, while P036 returned canonical `C:/Users/runneradmin/...`
lock paths. `relative_to()` correctly rejected the different lexical prefixes.
[Actual CI exception stacks](49563-native-short-path-failure.log),
[job](https://github.com/cogco1/MonkeyHub/actions/runs/36266944296/job/108473332493).
Subsequent package/shortcut and signed-install tests did not run in that attempt.

The scanner now resolves the project root once before enumeration, matching the
repository's canonical namespace. It still excludes only the same two owner paths;
no error suppression, additional exclusion or permission change. A new real Windows
`GetShortPathNameW` alias regression fails before this correction and passes after,
including while the actual owner locks are held. It also exercises a parent/name
alias on volumes without generated 8.3 names, instead of silently skipping.
[Before](local/short-path-before.log), [all three lock regressions after](local/short-path-after.log).
The final commit must run all five required checks again; none of the earlier
green jobs is substituted for that run.

## Additional regression found during required follow-up

The isolated full AI/Physical browser run failed at the old AI source-replacement
assertion: selected `source.png` instead of `revised-source.png`. The job was already
outdated from a reference replacement; waiting for that same label after a second
asynchronous refresh did not wait for the new document list. The test now waits
until that replacement appears in the actual source selector before clicking
Use updated source. Original identity/no-generation assertions remain unchanged.
No production AI path change. [Failure](local/render-before-readiness.log),
[rerun](local/render-after-readiness.log).

## Execution ledger

| Run | Result / scope |
| --- | --- |
| Initial local browser without PLAYWRIGHT_MODULE | Environment ERROR before test execution; [log](diagnosis/330-browser-diagnostic.log) |
| Old-layout SwiftShader switch | FAIL, 14 overlay pixels; raw/state zero difference; [log](diagnosis/330-browser-swiftshader-raw.log) |
| Old-layout refresh diagnosis | Refresh comparison passed; full run later interrupted by rebuilding the served dist during navigation (`ERR_HTTP_RESPONSE_CODE_FAILURE`). This operator-induced run is not an acceptance PASS; [log](diagnosis/330-browser-swiftshader-refresh.log) |
| First local lock rerun without Studio API import path | 2 collection/setup errors, no passes; [log](diagnosis/330-final-lock-tests.log) |
| Isolated Windows forced-lock tests | 2 PASS; [log](local/lock-tests.log) |
| Isolated TypeScript/Vite build | PASS, chunk warnings retained; [log](local/build.log) |
| Isolated full chat shell / software renderer | See [complete log](local/chat.log); exact real camera and canvas pixels at both sizes, all original shell assertions |
| Isolated full AI/Physical browser after readiness correction | See [complete log](local/render-after-readiness.log); all 14 scenarios, offline AI provider, real Runtime/P036/WebGL and cold Hub |
| Final remote required checks | Final SHA, merge-test SHA, all five job links and result/skip counts in PR body; old runs are not substituted |

The CI artifacts use existing Actions ownership/retention: browser/native diagnostics
14 days; packaged ZIP/EXE follow existing 7-day review artifact policy. The bounded
evidence here survives artifact expiry. Large originals, background files, .blend
and full P036 data are not committed. [Manifest](manifest.json) gives normalized Git
text hashes; PNGs are unmodified. [Current browser evidence](current-browser/) includes
full request timing and actual rendered state. No fake screenshot or generated image.

## Product evidence and revision limits

The [actual prior changed-state Penguin loop](../review-pr330/README.md#actual-penguin-savereopen-loop)
remains valid evidence of its stated code and scope: camera/material/light/render
settings saved, four real drawings added, refresh/workspace switches/cold reopen,
12 retained drawing records (8 current / 4 stale), exact dimensions (max error 0 m).
Original saved scene content was restored as a new immutable sequence.
[This follow-up's pre-operation snapshot](product-before.json) retains scene/drawings
and a hash of the full geometry response; full arrays stay in the user's project.
Final-HEAD cold readback and UI observations are reported separately in the PR body.

Additional actual product regression on `49563f72` (before the test-only path fix):
isolated Hub health confirmed that SHA; actual native worker 25620 exited and the
reopened worker was 44272. All **141 persistent files** retained identical hashes;
full scene, 12 drawing records and geometry response matched exactly after refresh
and reopen. All **103 DOM input/select values** matched before switching, after
switching and after cold reopen. Source GLB hash remains `b8fac80bbbca66bd0ed0ab6f3544e46eab29ad840ceb21f71e9ab89d4e7a6c54`.
[Summary](product-49563f72/summary.json), [saved state](product-49563f72/before.json),
[reopened](product-49563f72/after-reopen.json), [actual UI](product-49563f72/after-reopen.png),
[file hashes](product-49563f72/files-after-reopen.json), [closed worker](product-49563f72/closed.json).
No new scene revision or drawing regeneration was claimed in this readback-only run;
the prior changed-state test and full browser update scenarios remain separate evidence.
The first local close probe used the wrong `projection == stopped` condition; the
correct API condition is `state == closed` and stopped workers/null PIDs. That probe
was not counted as close acceptance; the documented cycle was repeated in order.
An early loading screenshot had only one control and failed the 103-control check;
acceptance waited for the complete real scene, rather than calling that blank state
a missing-model fix. Browser pointer targeting was offset in the narrow host window;
actual workspace switches were completed using keyboard activation and checked.
Board loaded (no dynamic-module error); Drawing still reports no retained Working
Head, and jagged Penguin material boundaries remain visible.

Geometry source remains MonkeyHub/OCCT or retained imported mesh; P036 is the writer.
Geometry revision identifies source geometry, scene revision identifies appearance,
drawing revision identifies geometry + recipe. Scene/drawing sequences are not
canonical OCCT versions. Explicit 1000→1100 mm source replacement does not prove
automatic modeling-parameter associativity. Neutral production 3DM is not an export
of the full scene appearance. Photo background is not HDRI, DOF, ground shadows or D5.

## Coverage matrix (four distinct categories)

Latest Issue bodies/comments re-read this round: [source comments](issues-reviewed.json).
“Not tested” never means implemented; the second column only names existing paths.

| Issue / capability | Implemented and verified | Implemented, not fully verified | Not implemented | Existing failure / unresolved evidence |
| --- | --- | --- | --- | --- |
| #319 retained mesh scene / drawings | Scene save/refresh/cold reopen; explicit geometry revision stale/update; true mesh drawing dimensions; immutable task scene pin | Broad resize/output-gate matrix and three-model end-to-end acceptance | Automatic OCCT Working Head input / modeling-parameter-driven update | Drawing workspace has no retained working state for Penguin |
| #319 camera | Physical-only gestures/persistence; this candidate refresh guard | Full output aspect ratios and live Penguin gesture → Cycles freeze | Default Modeling ↔ Physical two-way orbit, pan, dolly/zoom, perspective/ortho, preset/FOV/target/up sync | Prior negative two-way check remains; not cured by screenshot correction |
| #319 Board / loading | Workspace module loads and prior pages retained | Full engineering Side current→stale→update→reopen association | Complete Penguin engineering association chain | Prior unassociated Side report; historical background-only incident still not diagnosed |
| #321 regions/materials | Saved Penguin regions/material assignments and existing scene consumers | Same-name/multi-model isolation, UV/texture/mask paths and rebinding | Generic structure/user-selection-driven region CRUD/highlight; material chat candidates/accept/reject/undo | Hardcoded penguinSuggestions; eye/belly jagged boundary root cause not established |
| #321 appearance export | Geometry readback and neutral 3DM | Full supported-attribute export fidelity across consumers | Complete retained scene appearance in production 3DM | Neutral material limitation, no geometry modification to hide boundaries |
| #324 resources | Existing selected photo binding, scene restore and prior real Cycles photo path | Versioned ownership/failure paths across projects/scenes | Complete generic upload/preview/select/replace/unbind/undo workflow | A→B→C→A, same filenames, slow upload, rapid replace, missing/decode failures NOT TESTED |
| #324 environment | Photo background and exposure in shared scene | Supported scene options only, not a quality certification | Complete HDRI/alpha/framing/DOF/shadow product workflow | D5-level presentation not achieved |
| #327 conversion foundation | Production black-model baseline; #336 split normals/legal black; #339 missing-source diagnostics/retry; scene source-role fix | Wider normals/UV/material/transform/independent consumer matrix | Comprehensive preflight + per-purpose gates + quarantine/cancel/timeout + reversible repair/history recovery | Attribute losses and appearance-boundary diagnosis remain; no three-model certification |

Existing upstream #336/#339 work is attributed to those PRs, not newly credited here.
All four Issues remain open. Next independent slices, after #330 review: #319 shared
camera/Working Head ownership and Board engineering association; #321 generic regions
and controlled neutral/mask/wireframe diagnosis; #324 project resource lifecycle;
#327 purpose admission and reversible recovery. Each needs non-Penguin acceptance.

## Reproduce

Use installed repository/API dependencies and Chrome/Playwright. On Windows:

```text
python apps/monkeyhub/desktop/tests/test_runtime.py ProjectSnapshotTests -v
npm --prefix apps/monkeyhub/web run build
node apps/monkeyhub/web/test/chatShell.browser.mjs
node apps/monkeyhub/web/workspaces/test/renderWorkspace.browser.mjs
python tools/archcheck.py --changed origin/main
```

The lock tests need repo + Studio API on PYTHONPATH if packages are not installed.
Optional `MONKEYHUB_TEST_RENDERER=swiftshader` reproduces the software renderer.
`MONKEYHUB_TEST_ARTIFACTS` preserves PNG/state/log artifacts. `PLAYWRIGHT_MODULE` and
`PYTHON` select installed runtimes. The full installed EXE lifecycle needs the package
workflow environment; optional/native skips are not passes. Live paid AI provider,
new Penguin final renders, signed production release publishing remain NOT TESTED.
