# W1-A — Deliberation episodes: the judgement is retained (E1)

## Status

DONE_WITH_CONCERNS

Everything the brief asks for is built, tested and passing. The concerns below
are two design points I decided in the open rather than silently (the failure
channel when retention fails, and the `scope` slot that does not exist yet),
plus three registrations the controller has to apply.

## What was built

**1. The record kind.** `archflow/project/record_kinds.py` gains
`DELIBERATION_EPISODE = "deliberation-episode"` with the schema literal
`DeliberationEpisode@1`, area `RUN_RECORD`, and the note the brief dictated. It
sits with the other Studio kinds. No `*_authority` block is added anywhere —
the payload states facts only (ADR-004; confirmed by the kernel owner's note
mid-task, and asserted by a test).

**2. The episode model.**
`apps/archflow-studio/api/archflow_studio_api/application/episodes.py` (new):

- `DeliberationEpisode` (frozen): `episode_id` (`ep-<12 hex>`), `project_id`,
  `state_digest`, `intent`, `proposals`, `protected`, `evidence_refs`,
  `validation_refs`, `created_at`, `produced_run`, `chosen_scope`; `to_dict()`
  is camelCase and carries `"schema": "DeliberationEpisode@1"`; `persistence`
  is derived — `run:<id>` or the in-memory sentence, which is imported from
  `proposals.PERSISTENCE` rather than re-spelled.
- `EpisodeIntent`, `EpisodeProposal`, `EpisodeChange` — the nested values the
  brief describes as mappings, written as frozen dataclasses with their own
  `to_dict()`, matching the house style. `EpisodeProposal.__post_init__`
  refuses a decision outside `accepted / rejected / modified`.
- `EpisodeStore`: a locked in-process list (locked because the accepting
  judgement is written on the candidate worker thread while `GET /api/episodes`
  is answered on the event loop). `retain(repository, run, episode)` writes
  through `put_json(run=, destination=PersistenceDestination(RUN_RECORD,
  run_id=...), record_kind=DELIBERATION_EPISODE, payload=...)` and binds
  `produced_run` to the run it wrote into. `flush(repository, run,
  state_digest)` retains every held episode of that state into the run.
- `reject`, `modify`, `accept` — the three judgements as functions;
  `still_open` (which options are undecided) and `validation_refs_read` (which
  verdicts this process holds for them).
- `repository` parameters are typed `archflow.project.ports.RecordSink`, the
  narrow write capability, rather than the concrete repository.

**3. The writing points.**

- `POST /api/proposals/{proposal_id}/decision` in `routes/proposals.py`
  (extended, no new router module), DTOs in `transport/proposal.py`
  (`ProposalDecisionRequestDto`, `ModifiedToDto`, `EpisodeDto` and its nested
  DTOs, `episode_dto`). A `modified` decision re-proposes the replacement
  sentence through the same `DeterministicIntentProvider` path, against the
  same component/element selection, and `modifiedTo` carries both the utterance
  and the new `proposalId`. Mismatched bodies (`rejected` + `modifiedTo`,
  `modified` without one) answer `422 DECISION_INVALID`.
- Accept: `routes/candidates.py` computes what was on the table **at request
  time** (that is when the architect judged) and the work callable calls
  `episodes.accept` after `execute_candidate` returns. That opens the episode
  with `accepted` for the run proposal, `rejected` with
  `superseded by <proposal_id>` for every other still-open proposal of the same
  state digest, sets `produced_run`, retains it, and flushes the pending
  in-memory episodes of that state into the same run. A failed run reaches
  neither line, so no judgement is claimed for a run that does not exist.
- `GET /api/episodes?stateDigest=` and `GET /api/episodes/{episodeId}` in a new
  `routes/episodes.py`, included in `routes/__init__.py`;
  `app.state.episodes = EpisodeStore()` in `main.py`.

**4. Two supporting extensions** (both needed, both additive, both read-only
except the first):

- `Proposal` gains one field, `pending: PendingIntent | None = None` — the same
  frozen value the pending-intent store holds, carried by reference, set in
  `routes/intents.py` next to `compilation_receipt` and using the exact
  mechanism that field's comment describes ("a field with a default is the
  route's to fill in afterwards"). Without it the episode could not name the
  `request_id` or the `known_slots` the brief asks for. Nothing copies the
  pending intent field by field, so there is no second vocabulary for it.
- `ProposalStore.for_state`, `JobRegistry.candidates_of`,
  `ValidationStore.receipt_ids` — three small read accessors so a judgement can
  name the options that were on the table and the verdicts that had been read.
  They compute nothing and trigger no verdict.

**5. Docs.** `docs/PROTOCOL.md` §4 gains the three routes as `provisional`, the
count sentence now reads "Twenty-one resources: fifteen stable, six
provisional" (verified against the app's own OpenAPI: 21 paths), and §5.2 "The
judgement is retained" is three sentences after the verdict step.

## Commits

- `638e99a` — Deliberation episodes: the judgement the studio made is retained
  (all code, tests, generated client, docs, and this report)
- a second, report-only commit follows it, correcting the hash written above:
  a report that names its own commit cannot know the hash before that commit
  exists, and amending would only produce a third hash.

## Tests

Studio API suite (from `apps/archflow-studio/api`, `PYTHONPATH` = worktree
root):

```
set PYTHONPATH=D:\ARCHFLOW_V4\.claude\worktrees\agent-a0b0477c53e4bab92
py -3.12 -m unittest discover -s tests -t .
Ran 369 tests in 125.891s
OK (skipped=2)
```

New file only:

```
py -3.12 -m unittest tests.test_episodes
Ran 9 tests in 3.285s
OK
```

Repo kernel suite (from the worktree root):

```
py -3.12 -m unittest discover -s tests -t .
Ran 340 tests in 31.120s
OK
```

```
py -3.12 -m unittest tests.test_record_kinds
Ran 19 tests in 0.378s
OK
```

Architecture:

```
py -3.12 tools/archcheck.py
ARCHITECTURE PASS (186 files, 1.742s)
```

Web contract (from `apps/archflow-studio/web`, after `npm ci` there and in
`tools/openapi-ts` — the worktree had no `node_modules`):

```
npm run -s api:generate     ->  ./src/api/generated · 4 files
npm run -s api:check        ->  api:check — 16 generated files match the current schema.
npm run -s typecheck        ->  (clean, no output)
```

`src/api/generated/{index,sdk.gen,types.gen}.ts` are committed with the change.
`npm run -s build` was **not** run: no TSX changed and no i18n key was added
(the brief adds no web surface), so `typecheck` is the check that applies.

### What `tests/test_episodes.py` proves

1. A rejected proposal yields an episode holding the decision, the reason
   verbatim, the option's own closure and its `old`/`new`, with
   `producedRun: null` and `persistence` the in-memory sentence.
2. It is readable by `stateDigest` and by id; a state nothing was decided
   against answers `[]`, and an unknown id answers 404 `EPISODE_NOT_FOUND`
   naming where episodes do not survive.
3. A modified proposal links the replacement: `modifiedTo` names the new
   `proposalId`, that proposal is retrievable, and it carries the new value.
4. Executing a candidate writes **two** `deliberation-episode` records into the
   run — the flushed earlier rejection and the acceptance — read back through
   `list_json` (so P036 digest-verifies them). The acceptance marks the other
   open proposal `rejected` with `superseded by <id>`, does *not* re-reject the
   one already decided, carries the intent and the record's `evidenceRefs`, and
   carries no `*_authority` key of any kind.
5. A failed candidate retains no judgement at all.
6. The authored record at `input/runner/state-record.json` is byte-identical
   after a rejection, a modification and a run — with a positive assertion
   beside it so the test cannot pass by nothing having happened.

## Registrations needed

The registry was not edited (rule 3). Three entries need changes.

### `studio.intent` (owns `proposals.py`, so it owns the episode work)

`files` — add:

```
apps/archflow-studio/api/archflow_studio_api/application/episodes.py
apps/archflow-studio/api/archflow_studio_api/routes/episodes.py
```

`owns` — add:

```
"the DeliberationEpisode value: one judgement of the studio - the intent, the options on the table, the decision on each with its reason, and the run it produced"
"the in-process episode store, and the flush that writes judgements made before a run into the next run made against the same state"
```

`public_api` — add:

```
POST /api/proposals/{proposal_id}/decision
GET /api/episodes
GET /api/episodes/{episode_id}
DeliberationEpisode
EpisodeIntent
EpisodeProposal
EpisodeChange
EpisodeStore
open_episode
intent_of
decided
reject
modify
accept
still_open
validation_refs_read
scope_of
superseded_by
```

`depends_on` — add `project.ports`, `project.record_kinds`, `project.refs`
(episodes.py writes through the repository port).

`tests` and `used_by` — add
`apps/archflow-studio/api/tests/test_episodes.py`.

`invariants` — add:

```
"a judgement is retained only into a run that happened; a failed candidate leaves none"
"an episode that has met no run says so on the wire and is lost on restart, exactly as a proposal is"
"accepting one option closes every other option still open against the same stateDigest, naming the proposal that superseded it"
"the episode payload states facts only: no authority block, no digest of itself (ADR-004)"
```

### `studio.candidate`

`public_api` — add `JobRegistry.candidates_of` (a read-only reverse of
`for_candidate`). No file moves; `routes/candidates.py` and `jobs.py` are
already its files.

### `studio.validation`

`public_api` — add `ValidationStore.receipt_ids` (a read of verdicts already
computed; it triggers none).

### `project.record_kinds`

`notes` / `owns` — the new kind. The `owns` line that reads "the six kinds no
spine module writes" is unaffected (the studio writes this one). `used_by` —
add
`apps/archflow-studio/api/archflow_studio_api/application/episodes.py`.

The record kind itself is already in `archflow/project/record_kinds.py`, which
the brief permitted for this one kind.

## Concerns

1. **A retention failure fails the job.** `episodes.accept` runs inside the work
   callable after `execute_candidate` returns. If `put_json` refuses, the
   exception reaches the job registry and the candidate is reported `failed`
   even though its run succeeded and its records are on disk — after which
   `GET /api/candidates/{id}` answers `CANDIDATE_NOT_FOUND` for a run that
   exists. I chose this over swallowing the refusal, because "do not silence a
   refusal" outweighs a mislabelled job in a case that means the repository is
   broken. A second failure channel (a job that succeeded with a retention
   finding) would be new mechanism the brief does not ask for; flagging it
   rather than building it.

2. **`chosenScope` is always `null` today.** Nothing in
   `clarification.py` fills a `scope` slot — `SLOT_TARGET`, `SLOT_PROPERTY`,
   `SLOT_VALUE`, `SLOT_ORIENTATION` are the four that exist. `scope_of` reads
   `known_slots["scope"]` and accepts only `element` / `stack` / `datum`, so it
   answers `None` until W1-D (scope-derived) fills that slot, and then it
   starts answering without another change here. I did not invent the slot: a
   default nobody chose would be worse than a null.

3. **No `episodes` capability in `protocol.py`.** `server_capabilities` lists
   ten feature names and the brief did not ask for an eleventh; adding one
   touches `test_protocol.py`/`test_health.py` expectations, which is the
   controller's call, not a worker's. Until it is added, a client discovers
   `/api/episodes` as a route rather than as a capability. Recommended as a
   one-line follow-up.

4. **`routes/episodes.py` is a new router module.** The brief says "extend, do
   not add a new router module" about the *decision* route (which I put in
   `routes/proposals.py`), and then says to add the episodes routes to the
   `routes/__init__.py` include list, which needs a module of their own.
   `/api/episodes` under `tags=["proposals"]` would have been the alternative;
   I read the include-list instruction as the governing one. Its DTOs stay in
   `transport/proposal.py`, exactly as the brief directs, so the judgement and
   the proposal it is about share one wire vocabulary.

5. **`validationRefs` is honest but usually empty.** It is filled from
   `ValidationStore.receipt_ids` for every candidate this process ran from the
   options on the table. A proposal is normally accepted or dropped before
   anything ran, so the list is empty — which is the fact, not a gap. It fills
   in the case the field exists for: run a candidate, read its verdict, then
   reject or accept.

## Left out and why

- **`make_portico_project` was not used for the run tests.** The brief names it;
  `tests/test_episodes.py` uses `make_project` instead. `make_project` is the
  fixture `test_candidate.py` uses for *real* candidate runs, and its record has
  two elements under one seat-owned component (`portico-base`,
  `portico-cornice`) — exactly what a test of "three options on one table"
  needs. `make_portico_project` exists for the clarification reproduction (a
  component with no editable descendant), which this task does not exercise.
  `write_runner_record` and `run_records` from the same module are used.
- **No web UI.** The brief asks for no TSX and no i18n keys, so none were added;
  the generated client is regenerated and committed so the web half can consume
  the routes when a later wave builds the panel.
- **`governance/module_registry.json` and `docs/SYSTEM_MAP.md` were not
  touched**, per rule 3. The registrations above are what the controller
  applies; `docs/SYSTEM_MAP.md` is rendered from the registry, so it follows.
- **No Villa data anywhere.** The fixture is the synthetic `demo-project`; no
  `D:\PROJECTS\...` path is read or written by anything in this change.
