# ADR-008 — One decision tree, many projections: a derived index and a content-keyed cache

**Decision (2026-09-26):** P036 stays the only source of truth. What a surface or an agent reads about the
decision tree comes from two derived stores. Either store can be deleted at any time and rebuilt from P036.

1. **Project index.**
   - One SQLite file per project, kept in the Hub cache (`%LOCALAPPDATA%\MonkeyHub\cache\projects\<runtime_id>\index.sqlite`) and never in the project folder.
   - It holds states, candidates, stages, branches, edges, and the records, artifacts and documents they cite.
   - The project runtime is its only writer. The runtime applies every P036 write through one repository observer.
   - On open, the runtime compares a fingerprint of the pointer files and record directories with the one stored in the index. If the two differ, or the schema version differs, it rebuilds the index. This catches writes made by the CLI, by agents or by a prune.
   - Every committed change adds one to a monotonic `revision`.
2. **Projection cache.**
   - Thumbnails, display meshes and drawings are keyed by `sha256(input content digest, kind, recipe, renderer version)`.
   - A key is a cache address. It is not a record identity (ADR-003).
   - A background queue produces the outputs when a state is committed, not when someone first opens the state.
   - A projection someone has to keep, such as an issued drawing, is still retained through P036.
3. **Change notification.**
   - Clients hear about changes from one event, `index.committed {epoch, revision, domains}`.
   - They then read a snapshot, or the changes `since=<revision>`, under an ETag.
   - Content-addressed bytes are served `immutable`, and each open project has one client store shared by every surface.

**Why:** the following was measured on 2026-09-26 on three projects of about 500 records each.
- Every refresh re-parses and re-validates hundreds of JSON files. Design history takes 0.25–0.75 s and worktrees take 1.1–2.2 s, and an open Design Tree repeats both every 10 s.
- With the tree open and nothing changing, the project worker spends about 2.8 s of CPU in every 10 s.
- Every model download lists all artifacts again, so a 128 kB file takes about 1 s.
- Each request proxied through the Hub adds 170–320 ms.
- Building an index from scratch reads those files once (0.4–1.6 s). The result is a SQLite file of about 250 kB that answers a query in microseconds. P036 verification takes 10–35 ms and stays.

**Rules taken from systems that already work this way** (read at source level, 2026-09-26):
- **Fossil.** The schema names the canonical tables and the derived ones. One idempotent `manifest_crosslink` runs on commit, on sync and on `rebuild`. We do the same: one projector serves both the observer and the rebuild.
- **Git commit-graph.**
  - The file is optional and gets a cheap check on open. Full verification runs only on demand.
  - A new file is written, then renamed into place.
  - Racy-git rule: a file whose mtime is at or after the last check is checked again.
  - Kernel history queries run 7–250 times faster with it.
- **Bazel / REAPI.**
  - A cache key covers the input digests, every parameter that affects output, the tool's version and a salt.
  - A missing or corrupt entry counts as a miss.
  - A format change deletes the cache instead of migrating it.
- **APS Model Derivative and Onshape.**
  - One manifest per source records each derivative's status.
  - Resubmitting a job is idempotent.
  - Tessellation is computed on demand and cached by microversion.
  - A thumbnail falls back to a placeholder that is not cached.
- **Speckle previews and Blender thumbnails.**
  - A preview row goes pending → done | error, and inserting the row acts as the lock.
  - A pending row is a lease, so it is reclaimed after a timeout. Otherwise a lost job leaves a permanent placeholder.
  - The image records its own input and recipe.
  - A renderer change retries earlier failures.
- **Linear, Replicache, tldraw.**
  - One counter moves once per transaction.
  - A poke carries no data, and the client then pulls.
  - On reconnect the client sends its last revision. The server sends a diff, or a reset when the client is ahead or older than the history the server keeps.

**Do not:**
- Write the index or the cache into the project folder, or migrate them with the project.
- Migrate an index. A different stamp (schema version, projector version, project, manifest) means rebuild.
- Cite an index row or a cache key as evidence. The P036 record is the evidence.
- Let a second process write the index.
- Key a projection by run id or by time, or leave the renderer version out of its key.
- Garbage-collect a cache entry without a grace window.
- Add a poll or a per-surface copy beside the client store.
- Keep a route's interim response memo after that route reads the index.

**Phase 1b as built (#365, 2026-09-27):**
- `archflow.project.index` owns the file, its stamp and its revision; the Studio's `StudioProjector` is the one projector. It
  stores what `record_refs`, `_run_artifacts`, `_run_documents`, `_candidate_stage_source` and `design_history` already
  return, per run and for the tree; it derives nothing new.
- The Hub names the directory: the project's one cache directory, `<runtime root>/cache/projects/<runtime_id>`, reaches
  the project runtime as `ARCHFLOW_STUDIO_CACHE_DIR`; the index keeps its file, `index.lock` and any `.corrupt-*` copy in
  its `index` subdirectory, beside the projection cache's `projections`. Without that variable, no index is kept and
  every route reads the runs.
- `index.lock` beside the file is the writer lease. A second process that asks is refused and reads P036; its keeper
  retries for a bounded time, since the holder may be a process that is exiting. Closing a binding (or collecting one
  nobody closed, or a shared-project pull replacing it) stops its keeper and gives the lease up.
- One `IndexKeeper` thread per index is its only writer. It hears of changes from the project's layout watch (#363): each
  publication (`LayoutSighting`) carries the fingerprint's own lines and the directories a notification or a write asked
  it to read again. This process's writes also reach it at once through the repository's write observer. No request
  thread projects, stats or writes for the index; the projector runs before the write transaction opens.
- Readers take one snapshot (one read transaction) for a whole listing. Any failure to read the index - loading,
  rebuilding, SQLite refusing, a run whose reading failed when projected - makes the listing read the runs, so every
  refusal stays the runs' own. A listing reads the index only once it holds every write this process made; until then
  it waits up to `INDEX_CATCH_UP_S` for the keeper, and then reads the runs.
- The projector version includes a digest of its own source and of the readers it stores, so a changed reader
  rebuilds the index rather than trusting rows it no longer produces.
- `place` keeps every layout line. A reopen diffs the lines and projects again only the runs (or the tree) whose
  lines moved or were racy when written; a run that cites another place (a registered model's blob, a drawing
  revision's receipt, the design branches for a candidate) is projected again when that place moves. HEAD and the
  working draft are cited by no row: saving a draft or issuing a version projects nothing (since #366 it still
  moves the revision; see phase 2).
- The revision moves once per commit that changed a row.
- `/api/artifacts`, `/api/documents` and the byte routes' lookups read the index; they keep no whole-page memo once an
  index answers, and their tag adds the index's epoch and revision to the process's token. It is stable only when the
  index was projected under the token's own fingerprint and holds the token's writes. The process's `READ_EPOCH` stays
  in every tag: the index's epoch survives a restart, the process's counters do not. Design history, worktrees,
  working source and the board keep their phase-0a memo until they read the index.
- Known limit: a file rewritten in place without a directory entry changing is seen only where a notification
  (Windows) or this process's write names it. Byte routes still re-hash what they serve.

**Phase 2 as built (#366, 2026-09-28):**
- The cursor is `(epoch, revision)`, both the index's. A client keeps entities: `run:<id>` (a run's own body,
  candidate, artifacts, documents and record count), `aside:<id>` (how many records a run keeps beside what it
  shows: Board scene revisions, page and model annotations, one per save; the projector names their kinds),
  `tree` (the tree's body and stages), `working` (the working position a head is read from: `current`, `active`
  and the digest of the retained runs' rows, without the local recovery it names) and `area:<name>` (the layout
  lines of every other area: HEAD, the working draft's file, the manifest, each top-level directory without
  rows). Modeling saves its local recovery 250 ms after each edit; that save moves `area:working` alone (the
  recovery run only when it is first made), never `working`, so a surface that shows the head can tell the two
  apart. A Board scene saved 700 ms after a change and a page's annotations saved per stroke move `aside:<id>`
  alone, never `run:<id>`.
  An area moves the revision when one of its lines changed or this process wrote there, so a Continue or a saved
  draft reaches clients although it projects nothing; a line only read again (racy) moves nothing.
- The `change` table is the bounded change log: per entity, the revision that last changed or deleted it, kept for
  the last 512 revisions above `meta.floor`. A rebuild starts a new epoch with an empty log (floor 1).
- `GET /api/index?since=R&epoch=E`: the same epoch at the current revision answers empty (`from == to`); a revision
  the log still holds answers `{from, to, upserts, deletes}`; another epoch, a revision ahead of the index or below
  the floor, or no `since` answers `{reset: true}` and every entity. Tagged `"epoch:revision"`; the server keeps no
  state per client. It never answers from a partial read: a refused snapshot is `INDEX_UNAVAILABLE` (503).
- The keeper announces each commit that moved the revision, and its first load (`domains: [reset]`), to the
  listeners registered for the project (`add_commit_listener`); the Studio publishes it on its event stream as
  `index.committed`. Nothing depends on receiving it.
- The Studio's own stream keeps its job events. A frame's id is now `<stream>:<seq>`, the stream naming the
  process: a resume point from another process (a worker restart) or older than the 200-event buffer answers
  `stream.reset` and the whole buffer instead of silently skipping. The index revision never depends on `seq`.
- A write's answer carries `X-Monkey-Index: <epoch>:<revision>` once the index holds it (the Hub forwards it).
- The Hub attaches once to each open project's worker stream and relays `index.committed` on
  `/api/runtime/events` as an `index` frame, and the job events as `studio` frames that name the worker stream
  they were numbered on: a restarted worker numbers from 1 again, and clients know an event by `<stream>:<seq>`.
  When the attachment may have missed something - the first one to a worker, or a `stream.reset` - it also sends
  an `index` frame without a revision: read again. A reattachment that resumes sends none, and a stream that ends
  at once is attached again after 0.5 s, then after a delay doubling to 30 s. The Hub keeps each project's last
  200 Studio events and opens every page's stream with them after its snapshot, marked `replay`, so an event
  panel opens with a replay as it did on its own stream and no surface takes them for news. Frames the Hub relays are never dropped against the snapshot's sequence:
  the snapshot does not hold them. A Hub page therefore holds one event stream, whatever it shows; the per-page
  proxy of the worker stream (`/api/runtime/projects/{id}/studio/api/events`) is retired and answers 404.
- Each open project has one client store (`workspaces/src/api/projectStore.ts`) at the ChatShell level, shared by
  its surfaces and released by count: `{epoch, revision, byId}`, one request in flight, `wanted = max(wanted,
  hint.revision)`, a delta applied only onto its `from`, answers that are not newer dropped, another epoch reset.
  It pulls on every connection of the Hub stream (reconnects included) once its snapshot arrives - the Hub
  subscribes before it sends that frame, and the connection's `open` comes before either - and on window focus,
  and only after the stream first did. A store is made during render but held only by the provider's effect, so a
  render React discards leaves none behind; a write that stopped waiting for the store is no longer kept. A surface that wrote waits for the store to reach the write's revision before it ends "in
  progress". Surfaces read it through `useSyncExternalStore` selectors, notified at most once a frame.
- The Design Tree, Board and Render read their views again when the store moves, not on a timer; a view without
  a content hash is kept under the revision it was read at (`ProjectStore.derive`), so showing a surface again on
  an unchanged project asks for nothing. The store keeps when each entity last moved (`moved`), and a surface
  follows only what it shows (`movedAt`): the Design Tree follows `tree`, `working`, `area:head` and every
  `run:<id>`, never `aside:<id>` or another area, so twenty edits in Modeling, twenty Board saves or twenty strokes
  on a drawing page read it no more (each read is a Worktree Graph, working source and design history). Its reads never overlap: one runs and whatever asks meanwhile is one more
  after it. A job's lifecycle event (`*.queued|waiting|running|succeeded|failed`) reads its running work, which
  the runtime holds in memory and no commit announces; a tree off screen (a hidden project tab stays mounted)
  reads it once when shown again, and a replayed event not at all. Modeling reads the Working Head again only when
  `working`, `tree` or `area:head` moves. Render reads its attempts again 1, 2, 4, 8 and 16 s after a submit whose
  answer was lost; outside the Hub it and the tree read on focus. Model bytes and previews stay content-addressed. The
  store is not persisted (no IndexedDB): a snapshot of a local index costs a few milliseconds.
- Periodic requests left when idle are the Hub's own: the chat attention read (30 s while no turn runs), the
  application list (60 s) and the software-update status (60 s); the Monitor page polls only while it is open.
- Known limit: surfaces still read their existing views after a move; they do not yet render from the store's
  rows. Only the Design Tree waits for the store to reach its own write's revision before it ends "in progress";
  Board and Render end it when the write answers, and show the write when the store next moves. A process without an index (no cache directory) sends no hints, so its surfaces refresh only on explicit
  reloads and their own writes.

**Phase 3 as built (#367 parts A and B, 2026-09-28):**
- The status of every projection key lives in the project index (`projection` table), not in memory: a restart
  finds what it drew, and a rebuild of the index carries the rows into its new file. Inserting a row is the claim on
  a key (insert-ignore); a per-key lock keeps two requests in one process from racing. A pending row the worker took
  is a lease, reclaimed at startup and once it outlives the job timeout. A failure retries three times with backoff;
  a cancel, or a source the render process refuses, gives its attempt back.
- A done row is an entity of its own (`projections:<key>`: input, kind, recipe, renderer and blob digest, never a
  requester's source). Becoming done moves the revision and is announced as `index.committed` with domain
  `projections`; a client store therefore holds each model's thumbnail digest, and a surface draws it with no status
  request. `useProjectRevision` (Board, Render) ignores these entities: no view is read from them.
- After every commit of the index, the worker queues each model the design tree shows that has no row: the working
  position's first, then what a reader asked for (a card in view that found no thumbnail), then every Stage (newest
  first) and candidate. A new renderer version gives every model a new key; an older renderer's done picture stays
  until the current one's is drawn, so thumbnails change one at a time and requests for visible cards go first.
- Every request checks its source first (the run's exact state and its registered model; no geometry is read), even
  for a done key: a fabricated state digest is refused and never answered with another run's source. So do the idle
  retries and the re-queue behind `GET /api/projections/{key}`, which answers no source at all.
- The render process reads a model once and draws its next size from the same shapes (the finer mesh of the first
  size is cleared first, so the bytes are those of a fresh read). The mesh renderer's version is derived from the
  source of its drawing functions, `tessellate_shape` and the model reader, and the pipeline digest from the source of
  the view's framing: editing either redraws every thumbnail without a hand bump. Curve-only objects are left out of
  the axonometric's frame as well as its drawing.
- The collector keeps a row while an artifact row of the index names its input; an unreachable row, and an older
  renderer's, goes after the grace window (an older renderer's at once once replaced), and a blob no row names goes
  after the grace window. A blob a remaining row names is never removed.
- The Design Tree, its list and the inspector share one thumbnail cache per page (`modelThumbnails`): each blob is
  downloaded and decoded once, at most 48 decoded images are kept (least recently used out), and the canvas draws
  a copy at about twice its cell. Images arriving together rebuild the canvas scene at most once a frame, and not at
  all below the close level. #326's viewport screenshots stay P036 documents for the Board; the tree no longer looks
  them up.
- Measured on the synthetic candidate fixture (one small model): the tree's thumbnail is done 1.8 s after the
  project is opened with a cold render process (load 0.86 s, render 0.12 s); a second size of the same model loads in
  0 ms and renders in 18-61 ms. Opening the tree in the Hub makes no status request for a drawn thumbnail and one blob
  request per image, after which the browser keeps it.
- Known limits: a runtime without the Hub's cache directory keeps no index, so it answers
  `PROJECTION_INDEX_UNAVAILABLE` and shows placeholders. The queue follows the tree the index holds; it does not know
  which cards a client has on screen until that client asks.
