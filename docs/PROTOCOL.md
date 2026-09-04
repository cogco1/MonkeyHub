# The open ArchFlow protocol, version 1 (draft)

**Status:** draft, written from the code on 2026-09-03. The one description of the wire is the
FastAPI application (`apps/archflow-studio/api`); this document says what of it a client may
rely on, and what version 1 has reserved but not yet built.

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
  "protocol": "archflow/1",
  "server": "monkeyarch-api",
  "serverVersion": "0.1.0",
  "mode": "local",
  "capabilities": ["artifacts", "candidates", "compare", "events", "gestures",
                   "intents", "pick", "projection", "proposals", "validation"]
}
```

`protocol` is `archflow/<major>`. `server` and `serverVersion` name the implementation, never the
protocol. `mode` is `local` or `remote` (§10.1). `capabilities` are the feature names this process
actually serves now, sorted; `rhino-export` appears only where an export can really happen.
`/api/health`, `/api/protocol` and `/api/projects` are not capabilities — a conforming server
always has them. The route opens no project, so a client can tell "this is not a server I speak
to" from "this server cannot find its project": different problems, different people.

---

## 2. Identity

Two identities and never three (ADR-003):

| name | identifies | answer to |
| --- | --- | --- |
| `recordDigest` | the authored record's **content**, invariant under binding | "is this the same record?" |
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
| **archived** | every canonical snapshot the published position has left behind | nobody deletes; the chain is the archive | not exposed in v1 (§10.5) |

Two rules a client may not soften. A shared run is never "the current design" — only the
published container is, and only until the next issue. And a server on this protocol **never
issues**: version 1 has no route that writes the published container, and a client that offered
one would be offering something no conforming server can do.

---

## 4. Resources

`v1` says whether a client may rely on the shape: **stable** does not change within major 1 (a
minor version may add fields); **provisional** may change within major 1 and a client should
tolerate it.

| method | path | returns | container | v1 |
| --- | --- | --- | --- | --- |
| GET | `/api/health` | `{status, service, projectBound}` — is the process up, does its binding open | none | stable |
| GET | `/api/protocol` | server identity and capabilities (§1) | none | stable |
| GET | `/api/projects` | the projects this server binds: `projectId`, `name`, `isDefault`. Path-free | none | stable |
| GET | `/api/projects/{projectId}` | that project's binding: `head`, `referenceRun`, `intentProvider` | reads published + shared | stable |
| GET | `/api/project` | the same, for the default project (§10.2) | reads published + shared | stable |
| GET | `/api/state?run=` | the projection: component tree, `Element@1` rows and their numeric fields, parameters and their locks, dependency edges, `stateDigest`, `recordDigest`, `honesty[]` | reads work in progress + shared + published | stable |
| GET | `/api/artifacts` | what the seat execution receipts certify, each row keeping its own `available` / `unavailableReason` | reads shared | stable |
| GET | `/api/artifacts/{sha256}/bytes` | the certified bytes, re-hashed before they are served; `ETag`, RFC 6266 `Content-Disposition`, `Cache-Control: no-store` | reads shared | stable |
| POST | `/api/pick/resolve` | what the object a user clicked actually is (§6) | reads work in progress + shared | stable |
| POST | `/api/proposals` → 201 | a typed, exact-base `DecisionOperator` with its closure and impact. Never applied | reads work in progress + shared | stable |
| GET | `/api/proposals/{proposalId}` | that proposal, as it was returned | server memory | stable |
| POST | `/api/proposals/{proposalId}/candidate` → 202 | a job id and the run id the candidate will make | **writes shared** | stable |
| GET | `/api/jobs/{jobId}` | that job as the server last saw it, failures included | server memory | stable |
| GET | `/api/candidates/{candidateId}` | the finished candidate, read back out of the records its run retained | reads shared | stable |
| GET | `/api/candidates/{candidateId}/validation` | the kernel's validation receipt and the server's verdict (§5) | reads shared + published | stable |
| POST | `/api/intents` → 201 | one of four outcomes: the resolved target and the proposal it became, or the pending intent the refusal belongs to (§5.1) | reads work in progress + shared | provisional |
| GET | `/api/candidates/{candidateId}/compare?against=` | before / after / why, from the inspection records both runs retained | reads shared | provisional |
| GET | `/api/events` | the server-sent event stream (§7) | server memory | provisional |

Eighteen resources: fifteen stable, three provisional. `/api/intents` is provisional because who
signs an agent's compilation receipt is still moving; `/api/compare` because its `why` comes from
one process's memory of a proposal; `/api/events` because its event types are not a closed set and
authenticated streams have no answer yet (§7).

**Server memory.** Proposals, jobs and events live in the process and are lost on restart. A
client treats `PROPOSAL_NOT_FOUND` and `JOB_NOT_FOUND` as ordinary and never uses the event
stream as a record of anything: what a run did is in the run.

---

## 5. Proposal → candidate → verdict

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

An intent is not a free exchange. It ends in one of four named answers, every one of which says
which it is in an `outcome` field:

| `outcome` | status and `code` | what it means |
| --- | --- | --- |
| `COMPILED` | `201` | the words became a proposal |
| `NEEDS_CLARIFICATION` | `422 BLOCKED_NEEDS_HUMAN` | something only a person can settle, with a concrete `question` and the `acceptedForms` |
| `MISSING_EDITABLE_CONTROL` | `422 MISSING_EDITABLE_CONTROL` | it is in the model and the record declares no control for it — a missing *system binding*, not a missing answer. Terminal |
| `UNSUPPORTED` | `422 UNSUPPORTED_REQUEST` | no action can express the request, or the clarification stopped advancing. Terminal |

Each carries a **`pendingIntent`**: `requestId`, `stateDigest`, `originalUtterance`, `actionKind`
(`change_existing_value` / `declare_missing_control` / `clarify` / `unsupported`),
`targetComponentId`, `elementId`, `requestedSemanticProperty`, `knownSlots`, `missingSlots`,
`candidates`, `rejectedCandidates`, `reasonCode`, `continuationToken` and `turn`.

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
   never becomes the reference run. Two refusals come before the job starts — the proposal
   conflicts with something the utterance asked to keep (`409 PROPOSAL_NOT_RUNNABLE`), or its
   base has moved (`409 STALE_BASE`). Everything after that is the job's, and a run the runner
   refuses is a *failed job carrying the runner's own sentence*, never an HTTP error.
4. **Follow it.** `GET /api/jobs/{jobId}`, or the event stream.
5. **Read the verdict.** `GET /api/candidates/{id}/validation` returns the kernel's validation
   receipt unedited, and beside it the server's `advance`, a fixed conjunction of five named
   clauses with `blockedBy[]` naming every clause that refused. Three-state law, verbatim:
   *held / violated / unchecked are distinct; unchecked is never green; the server issues the
   advance verdict.* A client renders the server's boolean and computes no verdict of its own.

The verdict is memoised per (candidate, published version): reading it twice is one decision.

---

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
authenticated stream (§10.1) has no answer in v1. A minor version will name one — a bearer
token on the stream request through a fetch reader, or a short-lived stream ticket — and until
then a remote deployment's clients read the stream by other means or not at all.

---

## 8. Honesty lines

Several answers carry `honesty[]`: plain sentences saying what that answer does **not** tell
you. A projection whose bound view the kernel refused says so and still serves the entities the
record declares; a candidate whose seat exported and left no artifact record says so; a
verdict says which of its clauses was vacuous.

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

## 10. What version 1 reserves

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
outside v1. There is no login resource, no session, no user identity on any answer, and no
per-project authorisation. A minor version adds them; nothing in v1 pretends they exist.

### 10.2 Multi-project addressing

The general form of every resource is project-scoped:

```
/api/projects/{projectId}/…
```

The unscoped paths — `/api/state`, `/api/pick/resolve`, `/api/proposals`, and the rest — are the
**default-project shortcut** for it. They are not deprecated and are not a second API: they mean
the one project a server names `isDefault` in `GET /api/projects`.

Version 1 serves the scoped prefix for the two listing resources only
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
`proposals`, `candidates`, `compare`, `artifacts`, `validation`, `events`, and `rhino-export`
where a server can really drive one. The list is sorted and reflects the running configuration,
not the build.

A client must tolerate a capability it does not know — that is how a minor version adds one —
and must not infer a capability from a successful call.

The identity in `/api/health` (`service: "archflow-studio-api"`) is the running package's name,
kept for the operators and probes that already read it. `/api/protocol` is the protocol's own
answer, and `server` there is the implementation's product name.

### 10.4 Versioning

- **Major.** A client refuses a server whose protocol major differs from its own, and says so
  rather than reporting answers it may have misread. `archflow/1` is the only major that exists.
- **Minor.** Additive only: new resources, new capabilities, new fields on existing answers, new
  error codes. Never a removal, a rename, or a change of meaning. A client ignores fields it does
  not know and does not fail on an unknown error `code`.
- **Server version.** `serverVersion` is the implementation's, and a client never branches on it.
  Branch on `capabilities`.
- **Provisional resources** (§4) are the exception a minor version may change. A client that
  depends on one says so.

### 10.5 Not in version 1

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
computes no verdict, no impact and no propagation of its own.
