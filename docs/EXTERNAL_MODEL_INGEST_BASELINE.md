# External model ingestion: #255 baseline

## Scope and reproduction

Current user-approved MVP scope is Rhino `.3dm` first. SketchUp `.skp` is
explicitly deferred and is not a blocker for the current delivery. The upstream
#255 issue has a broader benchmark matrix; this local slice does not claim to
close its SKP acceptance.

Measured on 2026-09-23 against main `77fa6c5a` plus optional Monitor stage
instrumentation. This change extends `studio.artifacts`; it does not introduce
another importer, model store, or rendering runtime.

Run with the existing API test dependencies installed:

```powershell
cd apps/archflow-studio/api
python -m tests.profile_model_ingest --model <authorized-local-model.3dm>
```

The manual harness reads the source unchanged and invokes the real registration
and download API using an automatically removed empty test project. It creates
no semantic interpretation of the external model. It emits anonymous measurements on stdout; it does not retain
the private model in the repository or upload it. Each process measures a first
registration and an unchanged repeat. OS disk caches are not flushed. The test
client runs in process, so these are not network or user-visible load times.

Normal runtime registration now emits `model_ingest` and nested stages through
the existing optional Monitor. Records carry project/run/source identities and
input byte count, not filenames, model contents or server paths. Existing HTTP
operation correlation remains intact. Disabled or failed logging does not fail
the import. Nested durations must not be added to their enclosing duration.

## External-source path and browser verification

Open Model is an explicit user request to view the selected file. The handler
awaits registration and then awaits the retained artifact's existing viewer-load
path; this is independent of Agent-result auto-selection. The import calls the
existing model-assets API without a fabricated run or state. It saves original
bytes in P036 and an immutable external registration in
a source run keyed by asset digest. Separate revisions cannot accidentally load
together as different seats of one generated model. Its artifact explicitly says
`external`; designStateDigest/modelSource/sourceStageRef remain null. Registration
reference plus asset digest identifies the source; it does not certify editable
architectural semantics. Existing composed registrations keep their exact-state
checks. No second model database, runner receipt, candidate, Stage or HEAD change
is introduced. The server emits a model registration event only for a new source.

A real browser trial in a new disposable Hub project successfully imported the
user's 10.4 MB model and displayed its geometry and embedded ground image. Monitor
recorded 1,415 ms for server registration, followed by 1,630 ms for retained model
load: download 186 ms, parse 865 ms, installation 354 ms, projection 225 ms. These
are observed application intervals, not a hardware GPU-completion measurement or
a file-picker-to-Board SLA. The version list can reopen the retained source.
Board view/camera binding and AI Render remain separate work; a saved external
artifact is not yet a live Board card. Board and Render remain separate workspaces.

The manual benchmark now exercises this external path in an empty test project.
The numbers below describe the earlier composed-registration baseline, retained
for comparison rather than relabelled as measurements of the new path.

## Authorized real 3DM sample

One user-supplied architectural study model: 10,355,208 bytes, 216 objects,
13 layers, four materials and no instance definitions. No private geometry is
included here. A second independent invocation of the checked-in harness gave:

| Measurement | First registration | Unchanged repeat |
| --- | ---: | ---: |
| In-process HTTP request | 1,588.61 ms | 128.11 ms |
| Registration application interval | 1,488 ms | 69 ms |
| Exact run/state binding | 17 ms | 15 ms |
| Base64 decode/header validation | 38 ms | 28 ms |
| Source digest | 6 ms | 6 ms |
| Registration lock wait | 1 ms | 1 ms |
| Existing registration lookup | 5 ms | 6 ms |
| 3DM contents inspection | 1,352 ms | skipped |
| P036 object and registration writes | 38 ms | skipped |
| Retained-source verification | 18 ms | 6 ms |
| Exact bytes read back through API | 17.35 ms | 17.48 ms |

Both registrations returned 201; both downloads returned 200 and matched every
original byte. File read before the requests took 4.41 ms. An earlier invocation
gave 1,320.54 / 161.27 ms for first/repeat requests. These are two observations,
not latency budgets or evidence of an optimization speedup.

The process working-set peak reached 288,526,336 bytes at first registration and
299,950,080 bytes after the repeat. The harness reports working set at stage end
and the **process-lifetime cumulative peak**, not independent per-stage peaks.
It includes the Python host, test client, JSON/base64 copies and Rhino library;
it is not the model's isolated memory cost. Non-Windows hosts report memory as
unavailable. Per-stage peak sampling and a separate client/server measurement
remain to be done.

A separate cProfile inspection run attributed approximately 800 ms to the first
Rhino library import. With the library already loaded, inspection wall time was
about 259 ms, with `FromByteArray` accounting for about 67 ms. Profiling adds
overhead; these observations only identify where to measure next. They do not
justify eager startup loading or dropping source inspection.

## Remaining acceptance

- Split inspection into SDK decode, hierarchy/material metadata and optional
  enrichment timings when measuring a larger file. The current inspection
  interval includes all of them and must not be called parser-only time.
- Measure actual file selection/upload, browser decode/GPU work, first visible
  frame and source-bound Board/Render readiness. Existing browser timing phases
  (`model_download`, `model_parse`, `model_install`, `model_projection`) are
  available for retained model loads but were not exercised by this harness.
- Continue with the supplied real 3DM, including browser preview and exact
  Board source/view binding. A larger 3DM is a later coverage extension. SKP
  samples, parser integration and benchmarks are deferred by user decision and
  are not prerequisites for this Rhino-first MVP.
- Real model edit/update latency and browser warm reopen remain unmeasured.
  Fixture tests cover two exact revisions, retained old bytes, restart and
  unchanged-source reuse; they are not a real architectural update benchmark.
- The browser allows local parsing up to 512 MB while registration rejects more
  than 128 MiB. Large-file product behavior needs explicit validation before
  calling the whole import path usable.
- #254 Board/AI Render/Publish and downstream staleness are separate remaining
  product acceptance; successful registration alone does not satisfy them.

## Repeatable synthetic-fixture profile, 2026-09-26

The manual harness now accepts `--repeats` and an optional, distinct
`--update-model`. Each repetition creates a new empty P036 project, registers
the first digest (cold), retries that exact digest in the same project (warm),
and then registers the update digest. It reports min/median/max rather than a
single observation. It also samples process RSS every 5 ms around each measured
operation on Linux and Windows. RSS includes the Python/API host and allocations
retained by earlier stages; it is a process peak for that interval, not isolated
model memory.

The source-side profile separately reports original file read, SHA-256, the
`rhino3dm` `FromByteArray` call, and native object indexing excluding that decode.
The API profile retains the existing base64 decode, digest, lookup, complete
contents inspection, P036 persistence and verification stages. Finally, a GET of
the exact retained bytes is labelled `retained_bytes_view_consumption`. That GET
is **not** a browser view: client transfer, browser decode, GPU upload, first
visible frame, Board and Render readiness remain explicitly unmeasured.

One run in the isolated Linux cloud container used Python 3.12.13 and
`rhino3dm` 8.35.0. Inputs were the committed synthetic fixtures
`native-source-index.3dm` (8,248 bytes, five objects, one layer) and
`model-source-b.3dm` (11,308 bytes, two objects, one layer). Five repetitions
gave:

| Stage | count | min | median | max |
| --- | ---: | ---: | ---: | ---: |
| Cold registration request | 5 | 67.981 ms | 75.278 ms | 82.153 ms |
| Cold complete contents inspection | 5 | 5 ms | 7 ms | 10 ms |
| Cold P036 persistence | 5 | 3 ms | 3 ms | 3 ms |
| Warm unchanged registration request | 5 | 5.347 ms | 5.648 ms | 6.565 ms |
| Warm unchanged lookup | 5 | 1 ms | 1 ms | 2 ms |
| Changed-source registration request | 5 | 14.458 ms | 15.750 ms | 18.427 ms |
| Changed-source complete contents inspection | 5 | 5 ms | 5 ms | 6 ms |
| Changed-source P036 persistence | 5 | 3 ms | 3 ms | 6 ms |
| Cold retained-byte GET | 5 | 2.938 ms | 3.054 ms | 3.770 ms |
| Warm retained-byte GET | 5 | 2.685 ms | 2.850 ms | 3.179 ms |
| Changed-source retained-byte GET | 5 | 3.728 ms | 3.833 ms | 8.241 ms |

The highest sampled request RSS was 190,119,936 bytes. The first standalone
source profile measured read 0.050 ms, digest 0.029 ms, `rhino3dm` decode 3.760
ms and total native indexing 51.887 ms (48.127 ms excluding decode); that first
index interval includes lazy Python module loading. The already-loaded update
measured read 0.057 ms, digest 0.022 ms, decode 3.492 ms and total indexing 5.470
ms (1.978 ms excluding decode). These tiny generated fixtures validate stage
separation and exact-digest behavior only. They are not representative of a
user's large building and do not establish a product latency budget.

The unchanged runs returned the first digest, while changed-source runs returned
the distinct update digest; every retained-byte response exactly matched its
input. Inspection and persistence were absent from all warm runs, demonstrating
the existing exact-source reuse path rather than a new cache. No optimization is
introduced from this fixture evidence. A real, authorized architectural 3DM is
still required to locate a bottleneck. SKP remains unmeasured because this cloud
task did not use the SketchUp SDK or a private model.

## Agreed Render boundary for subsequent work

MonkeyHub/P036 remains the authoritative home for model and editable scene
data, materials, lights, cameras, jobs and results. WebGL2 remains interactive
preview. AI is the first executor; Blender and D5 join the same workspace through
separate adapters with their own parameters. Executor selection and deployment
location are separate decisions. Future workers consume exact source/scene and
asset references, expose task status/cancellation/failure and return source-bound
results to MonkeyHub. A generated Blender project is a rebuildable execution
artifact, not another authoritative project database. Remote Blender deployment
is not part of this profiling slice; these are design constraints, not claims
that a remote worker or a universal scene contract is implemented.

## Integration verification, 2026-09-26

The cloud smoke was rerun from main `07e1a40b` (including PR #339), with Node
24.15.0, Google Chrome 154.0.8037.57, the repository's `npm run sync` output and
the real disposable Project Runtime. New external registrations use the existing
`StudioModelAsset@1` record with `representation: external` and no modelSource.
Readers also accept the earlier experimental external schema; both original-source
registrations remain protected from automatic draft cleanup.

The checked-in `externalModelImport.browser.mjs` exercises the real Runtime and
P036 in a disposable project: drag/drop, two distinct retained revisions, unchanged
retry, corrupt-file refusal without losing the current view, browser reopen and
version selection. External imports do not expose semantic continuation, model
undo/redo or Stage acceptance. The project HEAD remains unchanged.

All five functional cases passed with their observable application assertions:
the target card was selected, loading ended, the viewport had no error, corrupt
input preserved selected source B and the two-source count, reopen selected A and
B in turn, HEAD did not move, and every retained byte response matched its input.
The smoke also verifies that the synced Rhino JavaScript, worker and WASM assets
are present and that Chrome receives the WASM successfully.

No latency value is reported for this run. The drag/drop test does not receive an
operation id spanning a diagnostic start/end event window, so request arrival plus
an equal SHA would not reliably exclude a late event from another load. Its timing
is therefore `null`; source visibility remains unknown and first visible frame is
not measured. The earlier authorized-model observations above remain historical
baseline data, not measurements from this fixture run.
