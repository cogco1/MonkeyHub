# The open ArchFlow protocol, version 2 (draft)

**Status:** draft, written from the code on 2026-09-04. The one description of the wire is the
FastAPI application (`apps/archflow-studio/api`); this document says what of it a client may
rely on, and what version 2 has reserved but not yet built.

ArchFlow is the methodology and this protocol. **MonkeyArch** is one implementation of it: the
server `monkeyarch-api` and the client in `apps/archflow-studio/web`; a conforming server need
be neither. Every field on the wire is `camelCase`, `serverVersion` included.

---

## 1. The handshake

A client asks one thing before anything else:

```http
GET /api/protocol
```

```json
{
  "protocol": "archflow/2",
  "server": "monkeyarch-api",
  "serverVersion": "0.1.0",
  "mode": "local",
  "capabilities": ["artifacts", "cad-export", "candidates", "captures", "compare", "events", "gestures",
                   "intents", "pick", "program", "projection", "proposals",
                   "user-settings", "validation"]
}
```

`protocol` is `archflow/<major>`. `server` and `serverVersion` name the implementation, never the
protocol. `mode` is `local` or `remote` (§10.1). `capabilities` are the feature names this process
actually serves now, sorted; `cad-export` appears when geometry export is enabled, and
`rhino-export` only when the configured export backend is explicitly Rhino. `user-settings`
appears only in local mode.
`/api/health`, `/api/protocol` and `/api/projects` are not capabilities — a conforming server
always has them. The route opens no project, so a client can tell "this is not a server I speak
to" from "this server cannot find its project": different problems, different people.

---

## 2. Identity

Two identities and never three (ADR-003):

| name | identifies | answer to |
| --- | --- | --- |
| `recordDigest` | the selected record's **content**, invariant under binding | "is this the same record?" |
| `stateDigest` | that record **bound to a run and a base** | "is this the state I was given?" — a mismatch is `409 STALE_BASE` |
| `head.stateSha256` | the project's published canonical version | "has the project moved?" |

`head.stateSha256` is not a third identity of the design; it identifies the published container,
not the record.

A record is named by a URI of the form `project://<projectId>/<relative/path>.json`, and the
digest in that file name is its identity. A content-addressed record therefore **cannot carry
its own reference** (ADR-005): a client takes a record's URI from whatever pointed at it, never
from a field inside it. A run is a directory, not a record; it is named the same way
(`project://<projectId>/runs/<runId>`) and has no digest.

Digests are never abbreviated on the wire; a client that shortens one for display sends back the
full value it was given.

---

## 3. The four container states

ISO 19650 calls a set of information a container and gives it a state. ADR-007 fixed each state
on one place, and every resource below reads or writes exactly one of them.

| state | what it holds | who writes it | how the protocol exposes it |
| --- | --- | --- | --- |
| **work in progress** | the authored State Record and the seat pack — loose files, nothing retained | the designer | read-only, through the projection (`GET /api/state`) |
| **shared** | one run: the exact record used, the developed state, programs, relation checks, receipts, exported artifacts | the runner | listed, read, and **written** by running a candidate |
| **published** | the one compare-and-swap position; moving a run there is an **issue** (出图) | `prepare_transition` + `compare_and_swap`, from a `PromotionDecision@1` | read-only: `head` on the binding |
| **archived** | every canonical snapshot the published position has left behind | nobody deletes; the chain is the archive | not exposed in v2 (§10.5) |

A shared run does not by itself establish acceptance. An explicit design Stage can
reference its complete result as the current working design on one design branch (§5.4).
The published container remains the formally issued position. Version 2 has no route
that issues a project or writes canonical `HEAD`.

---

## 4. Resources

`v2` says whether a client may rely on the shape: **stable** does not change within major 2 (a
minor version may add fields); **provisional** may change within major 2 and a client should
tolerate it.

| method | path | returns | container | v2 |
| --- | --- | --- | --- | --- |
| GET | `/api/health` | `{status, service, projectBound}` — is the process up, does its binding open | none | stable |
| GET | `/api/protocol` | server identity and capabilities (§1) | none | stable |
| GET | `/api/projects` | the projects this server binds: `projectId`, `name`, `isDefault`. Path-free | none | stable |
| GET | `/api/projects/{projectId}` | that project's binding: `head`, `referenceRun`, `intentProvider` | reads published + shared | stable |
| GET | `/api/project` | the same, for the default project (§10.2) | reads published + shared | stable |
| GET | `/api/state?run=` | the projection: component tree, `Element@1` rows and their numeric fields, parameters and their locks, dependency edges, `stateDigest`, `recordDigest`, `honesty[]` | reads work in progress + shared + published | stable |
| GET | `/api/artifacts` | one row per certified file, with `format`, `representation`, `available` / `unavailableReason`; an OCCT STEP (`step` / `exact`) and mesh preview (`3dm` / `preview`) share one producing `receiptRef` | reads shared | stable |
| GET | `/api/artifacts/{sha256}/bytes` | the certified bytes, re-hashed before they are served; `ETag`, RFC 6266 `Content-Disposition`, `Cache-Control: no-store` | reads shared | stable |
| POST | `/api/captures` → 201 | a viewport PNG retained under the named existing run's `workspaces/studio-captures/`; body carries `runId` and `pngBase64`, response carries its project-relative path and digest | **writes shared workspace** | stable |
| POST | `/api/pick/resolve` | what the object a user clicked actually is (§6) | reads work in progress + shared | stable |
| POST | `/api/proposals` → 201 | a typed, exact-base `DecisionOperator` with its closure and impact. Never applied | reads work in progress + shared | stable |
| GET | `/api/proposals/{proposalId}` | that proposal, as it was returned | server memory | stable |
| POST | `/api/proposals/{proposalId}/candidate` → 202 | a job id and the run id the candidate will make | **writes shared** | stable |
| GET | `/api/jobs/{jobId}` | that job as the server last saw it, failures included | server memory | stable |
| GET | `/api/candidates/{candidateId}` | the finished candidate, read back out of the records its run retained | reads shared | stable |
| POST | `/api/candidates/combine` → 202 | a new candidate from independent saved component changes sharing one Stage (§5.4) | writes shared | provisional |
| GET | `/api/design-history?branchId=main` | design branch pointers and their reachable committed Stages | reads shared + design refs | provisional |
| POST | `/api/design-stages/initialize` → 201 | explicit initial Stage from a complete exact model | writes review + design ref | provisional |
| POST | `/api/candidates/{candidateId}/accept` | immutable Stage and atomic advancement of its expected design branch head | writes review + design ref | provisional |
| POST | `/api/design-branches` → 201 | a sustained branch forked from a reachable historical Stage | writes design ref | provisional |
| POST | `/api/drawings/elevations` → 201 | exact-model elevation document with drawing/revision/Stage/view references | writes shared drawing artifacts and document registration | provisional |
| GET | `/api/candidates/{candidateId}/validation` | the kernel's validation receipt and the server's review readiness (§5) | reads shared + published | stable |
| POST | `/api/intents` → 201 | one of four outcomes: the resolved target and the proposal it became, or the pending intent the refusal belongs to (§5.1) | reads work in progress + shared | provisional |
| POST | `/api/proposals/{proposalId}/decision` → 201 | an explicit judgement: accepted with its successful `candidateId`, rejected, or modified into a linked replacement (§5.2) | accepted is **written into its named run**; other decisions stay in memory until a candidate run against the same state | provisional |
| GET | `/api/episodes?stateDigest=` | the judgements this process holds, each saying whether it lives in a run or only in memory (§5.2) | server memory + reads shared | provisional |
| GET | `/api/episodes/{episodeId}` | one of them | server memory + reads shared | provisional |
| GET | `/api/candidates/{candidateId}/compare?against=` | before / after / why, from the inspection records both runs retained | reads shared | provisional |
| GET | `/api/events` | the server-sent event stream (§7) | server memory | provisional |
| GET | `/api/state/frame` | the record's frame: each `Level@1` and `GridAxis@1` with its role, its value, the elements whose own references name it, and the closure of changing it; `honesty[]` | reads work in progress + shared + published | provisional |
| POST | `/api/state/closure` | what changing `changedRefs` would move, and the propagating edges that carried it. Reads only; the POST carries the list and the `stateDigest` it is asked against | reads work in progress + shared + published | provisional |
| GET | `/api/state/volumes` | the record's `Volume@1` boxes with their levels and their own plan area, and what the massing as a whole measures (§5.3) | reads work in progress + shared + published | provisional |
| POST | `/api/options` → 201 | one massing option: a deterministic transform of the record's own pack, measured, with the findings of the envelope the request carried (§5.3) | server memory, pack **written into shared** as its own `option-NNN` run | provisional |
| GET | `/api/options?run=` | the selected record's massing as the baseline and every option this process holds beside it, measured the same way; each option keeps its own source | server memory + reads shared | provisional |
| POST | `/api/options/{optionId}/select` → 202 | run that option as a candidate, through the same candidate path a proposal takes; answers a job id, never a run | writes a **detached run** | provisional |

| GET | `/api/program?run=` | the program sheet: departments, spaces with target area / count / clear height / function, adjacency requirements, `totals`, `honesty[]`. `source` is `input` (the architect's own `input/runner/program-sheet.json`) or `derived` (what the record's own `Space@1` zones say); an explicit run reads only that retained record's derivation | reads work in progress + shared + published | provisional |
| POST | `/api/program` → 202 | apply a sheet to the record **as a candidate**: a job id, the run id it will make, and the server's own `totals`. The authored record is never rewritten. `saveInput: true` also writes the architect's own sheet file — local mode only (§10.1) | **writes shared**; with `saveInput`, **writes work in progress** | provisional |
| GET | `/api/semantics` | every registered `role.*` and `condition.*` with its meaning and aliases: the vocabulary canonical state may name (ADR-006). Opens no project | none | provisional |
| GET | `/api/settings/user` | saved local preferences; `{}` when no file exists; local mode only | reads user settings, no project | provisional |
| PUT | `/api/settings/user` | replace the saved local preferences; omitted/null fields clear their override; local mode only | atomically writes `%APPDATA%/MonkeyArch/settings.json`, no project | provisional |

**Local user settings.** The optional fields are `language` (`en` or `zh-CN`), `theme`
(`dark`, `light`, `system`), `fontScale` (0.9, 1, 1.1), `intentProvider` (`deterministic`,
`codex`, `anthropic`), a nonempty `intentModel`, and positive finite `intentTimeoutS`.
Other fields are refused. Both routes return saved fields only; nulls are omitted. PUT replaces
the file, so a client preserves any saved fields it is not editing. A malformed file answers
422 `USER_SETTINGS_INVALID` and can be replaced by an explicit valid PUT. Remote mode omits
the capability and authenticated requests answer 404; the usual remote token gate still applies.
The client restores appearance from GET. At the next local launch, saved intent fields override
the corresponding runtime/environment defaults; clearing them restores the existing runtime
over environment rule. Saving does not change the current compiler. The launcher ignores an
unreadable or invalid file with a warning and never changes project, CAD or credential settings.

`/api/intents` is provisional because who
signs an agent's compilation receipt is still moving; the three deliberation resources because a
judgement not yet met by a run is still one process's memory; `/api/compare` because its `why` comes from one
process's memory of a proposal; `/api/events` because its event types are not a closed set and
authenticated streams have no answer yet (§7); `/api/state/frame` and `/api/state/closure` because
levels and axes are not yet editable — the grammar has no sentence for them — so what an
architect can do with the frame is still moving; the four massing resources because an
option's metrics are held in the server's memory — there is no retained record kind whose payload
is a set of measurements, so only the option's pack survives a restart, in its own run; the program resources because who owns an
authored sheet on a shared server has no answer yet.

**Selected sources for program and massing.** `GET /api/program?run=<runId>` and
`GET /api/options?run=<runId>` read that run's retained record. Both POST requests accept
optional `sourceRunId` alongside the selected projection's `stateDigest`. Responses echo
the source on the program view, options table and individual options. Omitting the source
preserves the existing default-binding behavior. Selecting an option uses its creation
source for both preflight and execution, even after the client views another run.
Missing or inexact explicit runs are errors, not a fallback to authored WIP. These are
optional API inputs; a client must pass the selected source to use this continuation.

**The program sheet.** A sheet is `ProgramSheet@1` and travels whole in both directions, carrying
the `stateDigest` of the record it was read from. `POST /api/program` refuses `409 STALE_BASE` when
that is not the state the project answers with now, and `422 PROGRAM_SHEET_INVALID` when the kernel
refuses a row — a `function` outside the semantic registry, a `requirement` the kernel relation
vocabulary has no kind for (`near` and `visual` have none; `adjacent` and `apart` become `adjacent`
and `clearance`), a `spaceId` that would rewrite an existing entity. It refuses
`422 PROGRAM_SHEET_NOT_APPLICABLE` when the record the sheet would make is one the kernel will not
build a design view of — a space with no zone becomes a `Space@1` with no volume, and a record that
already draws massing has no room for a zone that occupies nothing.

A candidate made from a sheet carries no text proposal. Follow its execution through
`GET /api/jobs/{jobId}`; a completed Studio candidate is readable through
`GET /api/candidates/{candidateId}` from its retained runner records, including after a
process restart. This does not recover an interrupted in-memory job or a missing proposal.

`saveInput: true` on a remote server answers `409 WIP_WRITE_REMOTE` and **still makes the
candidate**, naming its run and job in the refusal: running a sheet is a read of the record, and
only keeping one needs an owner this protocol does not yet have.

**Server memory.** Proposals, jobs and events live in the process and are lost on restart. A
client treats `PROPOSAL_NOT_FOUND` and `JOB_NOT_FOUND` as ordinary and never uses the event
stream as a record of anything: what a run did is in the run.

---

## 5. Proposal → candidate → review readiness

One chain, and each arrow is a route.

1. **Read the state.** `GET /api/state` answers `stateDigest`. Every request that would change
   something carries it back.
2. **Propose.** `POST /api/proposals` (a sentence already in the grammar) or `POST /api/intents`
   (any words; the server resolves what they are about, its agent compiles them into the grammar
   and the grammar types them). Either way the answer is the *record's* proposal: a typed
   `DecisionOperator` with the change, the closure it propagates through, and the conflicts it
   reaches. Nothing has run. `POST /api/intents` ends in exactly one of four outcomes (§5.1),
   and only the first is a proposal.

### 5.1 The four outcomes of an intent

An `authoredControlDraft` is diagnostic context on a `MISSING_EDITABLE_CONTROL` answer,
not a control written into the model. The unused provisional `/api/controls` registration
routes were removed on 2026-09-06: they only stored that draft in memory and had no design
consumer. No retained project data requires migration. Actual design changes use typed
proposals and candidates; the draft and existing missing-control diagnostics remain available.


An intent is not a free exchange. It ends in one of four named answers, every one of which says
which it is in an `outcome` field:

| `outcome` | status and `code` | what it means |
| --- | --- | --- |
| `COMPILED` | `201` | the words became a proposal |
| `NEEDS_CLARIFICATION` | `422 BLOCKED_NEEDS_HUMAN` | something only a person can settle, with a concrete `question` and the `acceptedForms` |
| `MISSING_EDITABLE_CONTROL` | `422 MISSING_EDITABLE_CONTROL` | it is in the model and the record declares no control for it — a missing *system binding*, not a missing answer. Terminal |
| `UNSUPPORTED` | `422 UNSUPPORTED_REQUEST` | no action can express the request, or the clarification stopped advancing. Terminal |

An agent may explicitly answer `unsupported` with its reason when its available tools cannot
perform the request; this is not missing information from the architect. Invalid agent scalar
grammar or an agent-authored field/element/unit that the existing lowering refuses is
`502 INTENT_AGENT_FAILED`, without a human question or grammar instructions. Direct deterministic
input keeps its existing clarification behavior. This boundary does not implement agent self-repair.

Each carries a **`pendingIntent`**: `requestId`, `stateDigest`, `originalUtterance`, `actionKind`
(`change_existing_value` / `declare_missing_control` / `clarify` / `unsupported`),
`targetComponentId`, `elementId`, `requestedSemanticProperty`, `knownSlots`, `missingSlots`,
`candidates`, `scopeOptions`, `rejectedCandidates`, `reasonCode`, `continuationToken` and `turn`.

**A selection is not a scope.** A request that resolved to one element has said *what*, not *how
far*. Where the record reads the change as reaching a stack that seats on the element — columns →
capitals → entablature, along the `support` relations and the `base` references the kernel already
resolves — that is one more step, `NEEDS_CLARIFICATION` with reason `SCOPE_UNRESOLVED` and
`missingSlots: ["scope"]`. The options are on `pendingIntent.scopeOptions`
(`{scope: element | stack | datum, elementIds, label}`) and repeated as `candidates` with refs
`scope:<name>`, so the question names the ids rather than asking anyone to imagine them. The
architect answers in words (`整个叠层` / `the whole stack` / `整条标高` / `只这个`) or the client
sends `scope` on the request; either settles `knownSlots.scope`. A single reading is not a
question and is never asked. **A wider scope is a coverage, not a mutation**: the settled scope
travels on `proposal.scope` (`{scope, elementIds}`, `null` for a proposal made straight from a
selection) as what the client shows revalidated, and the operator still moves one scalar on one
element. A datum is a grouping and not a propagation — it is offered and never stops a change on
its own.

**A derived control is shown, never compiled.** Where the number the request named is one a
reference already pins — a height whose `top` is a level, a base that takes another element's
published top — the catalog marks that capability `status: "derived"` with
`source: "derived from <ref>"`, and the answer names the source with reason `CONTROL_IS_DERIVED`
rather than typing a change the kernel refuses afterwards. Where the source is another element
whose own capability is editable, the answer is `NEEDS_CLARIFICATION` and its controls are the
`candidates`; where it is a level — a level's elevation is not an element capability — the answer
is the terminal `MISSING_EDITABLE_CONTROL`, naming the level, with the draft's `suggestedAction`
saying to move it. Neither reaches the agent.

**The continuation is the whole of the continuity.** A client that answers sends back
`continuationToken` and nothing else — never a transcript, and never its own idea of the
selection. The token is single-use: the server closes it and issues a new one, so a reply cannot
be replayed against a round that has moved on. A `continuationToken` of `null` means the answer
was terminal and there is nothing left to ask; a client that renders an input box against one is
building the loop this outcome exists to end.

Two rules bind the server. **A pending intent is bound to a `stateDigest`**: one opened against a
state the project has left is void and answers `409 STALE_CLARIFICATION` rather than being
applied to the state that answers now. And **a clarification advances or terminates**: every
reply must shrink `missingSlots`, correct the target, narrow `candidates`, compile, or terminate.
A round that changes none of them is answered `UNSUPPORTED` with reason
`CLARIFICATION_MADE_NO_PROGRESS` — the server says what it is missing instead of asking the same
question again.

`MISSING_EDITABLE_CONTROL` may carry an **`authoredControlDraft`**: the control somebody would
have to author (`targetComponentId`, `suggestedElementId`, `semanticProperty`, `producer`,
`binding`, `unit`, `provenance`, `confidence`, `dependencyRequirements`, `suggestedAction`). It is
a value the server returns and never retains; no route writes it, it invents no number, and a
neighbouring element whose field shares a name is named there only to be refused.
3. **Run it as a candidate.** `POST /api/proposals/{id}/candidate` answers `202` and a job id.
   This is the one write: a **harness run** in the shared container. It never closes a stage and
   never becomes the reference run. Refusals come before the job starts when the proposal
   conflicts with something the utterance asked to keep (`409 PROPOSAL_NOT_RUNNABLE`), its
   client digest is stale (`409 STALE_BASE`), or the selected reference is not an exact retained
   State Record on current HEAD (`409 REFERENCE_STATE_NOT_EXACT`, `REFERENCE_BASE_STALE` or
   `REFERENCE_STATE_MISMATCH`). Everything after that is the job's, and a run the runner refuses
   is a *failed job carrying the runner's own sentence*, never an HTTP error.
4. **Follow it.** `GET /api/jobs/{jobId}`, or the event stream.
5. **Read review readiness.** `GET /api/candidates/{id}/validation` returns the kernel's
   validation receipt unedited, and beside it the server's `reviewReady`, a fixed conjunction of five named
   clauses with `blockedBy[]` naming every clause that refused. Three-state law, verbatim:
   *held / violated / unchecked are distinct; unchecked is never green; the server reports
   candidate review readiness.* A client renders the server's boolean and computes no readiness
   result of its own. `reviewReady` grants no issue authority: only `project.issue` can issue a run
   or advance a stage.

Review readiness is memoised per (candidate, published version): reading it twice is one result.

### 5.2 The judgement is retained

Running a candidate makes a reversible result, not a design decision. It does not mark that
proposal accepted or reject other open proposals. The job registry already links the proposal
and candidate; the run only flushes earlier explicit judgements against its base.

The existing proposal endorsement route is `POST /api/proposals/{id}/decision` with
`{"decision":"accepted","candidateId":"<the chosen candidate>","reason":"<optional reason>"}`.
The candidate must have succeeded in this process and belong to that proposal. No latest-run
default is used. `candidateId` is required for acceptance and refused for the other decisions;
`modifiedTo` is only for modification. Acceptance runs nothing, moves no HEAD and grants no
formal-issue authority. It retains the existing `DeliberationEpisode@1` in the named candidate run
and preserves other proposals and alternatives against the same base. It does not create
a design Stage. Previously retained episodes remain readable.

Rejections and modifications made before a run remain process memory: `producedRun` is `null`
and `persistence` is `in-memory (not version history)`. When a candidate meets those judgements,
they are flushed into that run and say `run:<id>`. Restart recovery of active proposal/job state
is not implied by retained episode or completed-candidate readback.

### 5.3 Massing options

A record's massing is its `MassingLevel@1`, `Volume@1`, `Space@1` and `Connection@1` entities
together with the declared `option`. `GET /api/state/volumes` shows the boxes, and every option is
one **deterministic transform** of the record's own `SchematicPack@1`: `add_floor`, `remove_floor`,
`shift_volume`, `scale_volume`, `split_volume`, or `pack` — a whole pack the client sends, which is
the socket a generative massing agent plugs into and the only transform that takes free-form
geometry. The vocabulary is closed and `GET /api/options` carries it in `transforms[]`, so a client
offers no button the server would refuse.

**The frame the numbers are in.** A volume box is two coordinate triples in the kernel's voxel
lattice — x and z are plan, y is up, both ends inclusive cells — and one plan cell is one square
metre (`SpatialGridBasis(horizontal_area_per_cell=1.0)`). `footprintM2` is the union of every
volume's plan rectangle, counted once where they overlap; `grossFloorAreaM2` is the sum of the
per-level footprints; `heightM` is the top face of the highest massing level less the base of the
lowest; and `efficiency` is the program targets as a share of the floor area, or `null` when the
request carried no target. Whatever could not be measured is a line in `metrics.honesty[]` and
never a zero.

**What is retained.** Each option is written into a run of its own, `option-NNN`, as the kernel's
own `SpatialOptionProposal@2` under the existing `selected-spatial-option` kind — the same record
the runner writes for the option a run executes. The metrics are not retained: no record kind's
payload is a set of measurements, so they live in the server's memory, and `persistence` on every
option says so. Selecting an option runs it as a candidate through the same path a proposal takes;
the run the runner leaves retains that massing as its own `selected-spatial-option`, and the
authored record is never written.

---

### 5.4 Design history and exact-source drawings

Servers advertising `design-history` expose immutable accepted Stages and persistent
design branch pointers. Stage labels such as S0/S1 are display names. Initial acceptance
takes `{projectId, branchId, label, modelSource}`; legacy exports are references until
the architect explicitly establishes the first Stage. Native models must cover the
complete producing run; a partial seat preview cannot become a full Stage.

Candidate acceptance takes `{projectId, branchId, expectedHeadStageRef, label?}` and
the persistent candidate id in the URL. It verifies the saved base and replayable
operator chain, complete model and validation before advancing the exact design head.
It reuses the existing model, returns the same Stage on a successful retry, and refuses
a changed branch head. An unreachable record written before an interrupted pointer
update is absent from committed history. Fork takes `{projectId, branchId,
parentBranch, stageRef}` and produces no geometry.

`sourceStageRef` on state/intents/proposals/program/options names the exact committed
design base. A candidate's retained delta restores that source after restart. Successive
candidate adjustments retain their actual parent record and operator; they create no
intermediate Stage. Historical Stage exploration keeps its historical canonical base;
the validation readout checks that same source base. Legacy candidates without a Stage
retain the current-published-base validation rule. Formal issue still requires alignment
with the current published base.

`POST /api/candidates/combine` takes `{projectId, candidateIds}`. Saved candidates
must descend from one exact Stage. The StateRecord owner normalizes supported independent
component changes into one typed operator and checks shared writes and dependencies
across all supplied changes. Conflicts are returned before a new run is created. The
new candidate goes through the same generation, preview, validation and acceptance path.
Candidate workspaces may compute overlapping scopes independently; queue limits are
worker capacity and the single Rhino export resource.

Servers advertising `drawing-elevations` accept `{projectId, sourceStageRef, view}`
or an exact candidate `modelSource` instead of `sourceStageRef`. Views are front,
back, left and right. An optional `drawingId` groups revisions; the response's
`revisionRef` names an immutable drawing receipt. SourceDocument adds `drawingId`,
`revisionRef`, `sourceStageRef`, `viewRecipe` and `generatedAt`; original uploaded documents retain
their existing shape with these fields absent/null. Document bytes may specify
`revisionRef` to retrieve the exact generated revision. The first consumer needs a
complete matching OCCT STEP and refuses partial native sources for composed models.
New drawing registrations retain their UTC generation time once; repeating an identical
request preserves its original revision and time. Default selection compares generated
revisions only within the current exact model source. Later drawings do not modify the
accepted Stage or prior page annotations.

Document annotation pages, saved annotation references and visual inputs may carry
`drawingRevisionRef`. For generated drawings this pins the exact revision through
annotation saving, intent compilation and candidate execution, even when another
drawing has identical PNG bytes. Annotation heads are separate per drawing revision;
older records without this optional field keep their existing serialization.

## 6. Pick and gesture resolution

A pick sends what the object on screen claims about itself — its `archflow:*` user strings, the
document's own strings, the object name — and the server answers **resolved**, **unbound** (this
project never produced that object: a fact, not an error) or **unknown_component** (it claims an
identity this record cannot honour). An element resolves only *within the component the object
claims*; an object naming one component and matching an element of another is two claims that
disagree, and the component answers alone.

Separately, and enforcing nothing, the answer reports `sourceState` — `current`, `stale` or
`unknown`. Opening last week's export to look at it is legitimate; the base is enforced where a
change is proposed, not where one is inspected.

Gestures travel with a sentence on `POST /api/intents`: a circle with no pick **is** the
selection, a keep mark **is** a keep clause. The server reads them into the record's own names
and prints back what it read as facts. A client never resolves a gesture itself.

---

## 7. The event stream

`GET /api/events` is `text/event-stream`. Each frame carries the event type as its SSE `event`,
the body as JSON, and the sequence number as its `id`. A client that reconnects sends the last
`id` it saw as `Last-Event-ID` and is given what it missed; a resume point that cannot be read
as a sequence replays everything rather than silently skipping ahead.

`?limit=N` is the other way to read it: a catch-up that sends at most N events, replay included,
and closes as soon as the backlog drains — so a bounded read always terminates, even on a quiet
process. Without `limit` the connection is held open and carries live events.

**Reserved.** The browser's `EventSource` cannot send an `Authorization` header, so an
authenticated stream (§10.1) has no answer in v2. A minor version will name one — a bearer
token on the stream request through a fetch reader, or a short-lived stream ticket — and until
then a remote deployment's clients read the stream by other means or not at all.

---

## 8. Honesty lines

Several answers carry `honesty[]`: plain sentences saying what that answer does **not** tell
you. A projection whose bound view the kernel refused says so and still serves the entities the
record declares; a candidate whose seat exported and left no artifact record says so; a
review-readiness result says which of its clauses was vacuous.

These are part of the protocol, not decoration. A conforming server states what it could not
compute rather than answering with an empty list, and a client shows them verbatim rather than
summarising them — an empty list being the one way a client can turn a refusal into a silent,
confident-looking answer.

---

## 9. Errors

Every failure, without exception, is one body:

```json
{"code": "STALE_BASE", "detail": "…"}
```

plus `question` and — when non-empty — `acceptedForms` for `BLOCKED_NEEDS_HUMAN`, and `outcome`,
`pendingIntent` and `authoredControlDraft` for a refusal that belongs to a clarification chain
(§5.1). That includes
unknown paths (`404 NOT_FOUND`), wrong methods (`405 METHOD_NOT_ALLOWED`), unreadable requests
(`422 REQUEST_INVALID`) and server bugs (`500 INTERNAL_ERROR`, which says nothing about itself).
Anything the framework refuses before a route runs keeps its own status under `HTTP_ERROR`.

A `detail` never carries the server's own filesystem path. An error is read by whoever ran into
it, and where a server keeps a project on disk is no part of an answer about a design.

---

## 10. What version 2 reserves

### 10.1 Authentication

A server is in one of two modes, and says which at `/api/protocol`.

- **`local`** — unauthenticated. One user on one machine, a loopback listener. Nothing about
  today's local pair changes: no token, no CORS, no header.
- **`remote`** — every `/api/*` resource except `/api/health` and `/api/protocol` requires
  `Authorization: Bearer <token>`. A request without one, or with the wrong one, is
  `401 UNAUTHENTICATED` with `WWW-Authenticate: Bearer` — including for a path that does not
  exist, so an anonymous caller learns nothing about which paths a server has. A remote server
  that cannot name a token does not start.

The token is opaque to the protocol: how a client obtained it, and what it stands for, is
outside v2. There is no login resource, no session, no user identity on any answer, and no
per-project authorisation. A minor version adds them; nothing in v2 pretends they exist.

That absence has one consequence a client can see. Writing an authored work-in-progress file —
today, `POST /api/program` with `saveInput: true` — is a **local-mode act only**: a remote server
cannot say whose sheet it would be saving, so it answers `409 WIP_WRITE_REMOTE` rather than let one
architect's brief silently replace another's. The read and the candidate are unaffected, and the
refusal names the run that was made. When per-user authoring lands, this refusal goes with it.

### 10.2 Multi-project addressing

The general form of every resource is project-scoped:

```
/api/projects/{projectId}/…
```

The unscoped paths — `/api/state`, `/api/pick/resolve`, `/api/proposals`, and the rest — are the
**default-project shortcut** for it. They are not deprecated and are not a second API: they mean
the one project a server names `isDefault` in `GET /api/projects`.

Version 2 serves the scoped prefix for the two listing resources only
(`/api/projects`, `/api/projects/{projectId}`). Duplicating a dozen routes under a prefix that
resolves to the same binding would be a second copy of the API to keep honest. What is built now
is the part that cannot be retrofitted: the project segment has **one resolver**, so a server
binding several projects fills that in, mounts the prefix, and no client and no route changes.
An id that server does not bind is `404 PROJECT_NOT_FOUND`.

A client that wants to be portable reads `GET /api/projects` first and uses the scoped form
wherever a server offers it.

### 10.3 Server identity and capabilities

`capabilities` is how a client hides what a server cannot do instead of discovering it as a 404.
A capability name is a feature, not a route: `projection`, `pick`, `gestures`, `intents`,
`proposals`, `candidates`, `captures`, `compare`, `artifacts`, `program`, `validation`, `events`, and
`cad-export` when geometry export is enabled, `rhino-export` when Rhino is explicitly selected,
and `user-settings` in local mode only.

An explicitly configured MonkeyMonitor diagnostic directory adds `operation-timing`
and `operation-diagnostics`. The former retains `POST /api/events/model-load` for
older clients. The latter adds `POST /api/events/timing` with `ClientTimingDto`:
client event/operation UUIDs, optional parent UUID, the closed phase/status set,
project/run/source binding, UTC interval and measured client duration. Optional
details contain numeric waits/bytes, asset identity and request kind; no prompts,
responses or provider token counters are accepted from this endpoint.
`X-Monkey-Operation` and `X-Monkey-Parent` correlate individual requests and their
candidate workers. They are diagnostic association only and grant no project action.
The acknowledgement reports whether logging succeeded; diagnostics cannot retry a
business request. Model-request round trips and nested service intervals are not
pure inference time, and only an explicit client interaction root supplies total
elapsed time. See [MonkeyMonitor timing](../monkeymonitor/README.md#时间口径).
OCCT exports an exact STEP and a mesh 3DM preview from the same program. Clients load only the
3DM in the viewer and offer the STEP as a download; two files sharing a receipt are one export.
The list is sorted and reflects the running configuration,
not the build.

A client must tolerate a capability it does not know — that is how a minor version adds one —
and must not infer a capability from a successful call.

The identity in `/api/health` (`service: "archflow-studio-api"`) is the running package's name,
kept for the operators and probes that already read it. `/api/protocol` is the protocol's own
answer, and `server` there is the implementation's product name.

### 10.4 Versioning

- **Major.** A client refuses a server whose protocol major differs from its own, and says so
  rather than reporting answers it may have misread. This implementation serves `archflow/2`.
  Version 2 replaces `advance` with `reviewReady` in the stable validation response and event
  payloads. The field reports candidate review readiness, never issue or stage-advance
  authority. This rename is incompatible with `archflow/1`: both client and server must use
  major 2, and a mismatched client stops at the handshake before reading design responses.
- **Minor.** Additive only: new resources, new capabilities, new fields on existing answers, new
  error codes. Never a removal, a rename, or a change of meaning. A client ignores fields it does
  not know and does not fail on an unknown error `code`.
- **Server version.** `serverVersion` is the implementation's, and a client never branches on it.
  Branch on `capabilities`.
- **Provisional resources** (§4) are the exception a minor version may change. A client that
  depends on one says so.

### 10.5 Not in version 2

Named here so nobody reads their absence as an oversight: issuing (writing the published
container), the archived container as a resource, stage closure, per-user identity and
authorisation, project creation, authored-record writing (the work-in-progress container is
read-only over the wire), server-side lookup of an artifact by digest across projects, and a
second author's work-in-progress slot. Each has a place in the layout already; none has a route.

---

## 11. Conformance

A **server** conforms when `/api/protocol` answers §1, `/api/health` and `/api/projects` answer,
every stable resource in §4 it lists as a capability answers in the shape named there, every
failure is the body in §9, every reference is a `project://` URI and every identity a digest
(§2), no resource writes the published container, and `honesty[]` says what an answer does not
cover rather than the answer being silently narrowed.

A **client** conforms when it probes `/api/protocol` first, refuses a foreign major, sends back
the `stateDigest` it was given, renders the server's codes and honesty lines verbatim, and
computes no review readiness, impact or propagation of its own.
