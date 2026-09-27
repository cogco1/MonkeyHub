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
   - Clients hear about changes from one event, `index.committed {revision, domains}`.
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
