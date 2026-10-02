# ADR-012 — The open project's runtime is its only writer; changes from elsewhere are found at open and on refresh

**Decision (Kaiwen, 2026-10-02, #599):** a write counts as design state only when it goes through the open project's Project Runtime. The runtime therefore knows every legal write as it happens, and nothing watches the whole project in the background.

1. **One writer while open.**
   - While a project is open, its Project Runtime holds the project's writer lease: an OS lock on `writer.lock` in the project folder, which dies with the process that holds it.
   - Any other process that writes the project while the lease is held is refused (`PROJECT_WRITER_BUSY`) and calls the runtime instead.
   - A tool or test that writes a project nobody has open takes the same lease for the length of its write.
   - The holder may be a process that is exiting, so the next one retries for a bounded time, as the index keeper does for `index.lock`.
2. **The Hub writes through the runtime.**
   - The Hub process keeps its read binding and writes nothing itself.
   - Its one direct write today, the 15-minute working-draft prune, moves into the runtime.
   - Work-copy registration already goes through the runtime (`POST /api/documents`).
3. **The Hub Agent writes through the Hub.**
   - The agent's CLI sees the project folder read-only: Write and Edit are denied there, and its scratch directory stays writable.
   - Its design changes go `studio_request` → Hub → runtime, as its guide already says.
   - Bash cannot be fully sandboxed. A file the agent writes with it is a change from elsewhere (point 5), never design state by itself.
4. **No project-wide watch.**
   - The runtime's own writes reach the index through the repository's write observer, which moves the revision and announces `index.committed` as today.
   - Neither the worker nor the Hub keeps a layout watch on the project: no recursive notification, no 1 s root poll, no 120 s safety walk.
5. **Changes from elsewhere are found at open and on refresh.**
   - A file changed by hand, by a sync tool, by a restore or by an agent's Bash is noticed when the runtime compares the layout fingerprint. That happens at open, as today, and when the person asks the Hub to read the project again (an explicit refresh).
   - When the fingerprint has moved, the runtime says that the project folder changed outside MonkeyHub, projects again what moved, and moves the revision. Clients follow, as for any commit.
6. **Work copies are the one expected outside edit.**
   - A copy the person opened in an external editor is watched on its own: that file, while it is open.
   - A changed copy is registered through the runtime as today (#314).
   - Nothing else is watched.

**What stays from ADR-008:**
- the index file, its keeper and revision;
- `index.committed`;
- the change log and conditional reads;
- the fingerprint check at open.

**What ADR-012 replaces:** the layout watch (#363) as the way the index and the Hub learn of changes. Conditional-read tags keep their meaning; their layout part moves only at open, on refresh and with the runtime's own writes.

**Why:**
- **Cost.** On the 150-run synthetic project (#547, #599):
  - the Hub re-read every run on a 30 s timer;
  - each open project carried two project-wide watches, one in the worker and one in the Hub;
  - cold reads recomputed the tree from the runs.
- **The watches existed because writes were not gated.** ADR-008's open-time check "catches writes made by the CLI, by agents or by a prune", and such writers were real: the Hub's own prune, an agent with Write, Edit and Bash on the project folder, and tools. Gating the writes removes the reason to watch, and makes an agent use the Hub by default.
- **Kaiwen's words:**
  - 「是否应该必须经过某个gate写进去的才能合法，这样agent默认就走hub了」
  - 「打开的时候再打开，更新的时候再写入，而不是后台持续监控」

**Known limit:** a change made outside MonkeyHub while a project is open appears after the next refresh or open, not by itself. Examples are a sync from another machine, a hand edit or an agent's Bash. Kaiwen accepted this for one person working on a local disk (2026-10-02).

**Do not:**
- add a project-wide poll or watch in the runtime, the Hub or a client;
- write a project from the Hub process, or from any process while its runtime holds the lease;
- give an agent write access to the project folder;
- treat a file that changed outside the runtime as design state before the runtime has looked at it and reported it;
- keep the lease as a marker file whose presence is the lock: it is an OS lock that ends with its process.

**Implementation (#599):**
- **Slice 4:** the writer lease, the Hub's prune moved into the runtime, and the agent's read-only project folder.
- **Slice 5:** the watch removed, refresh added, and work copies watched on their own. ADR-008's phase-1b text is updated as each part lands.
