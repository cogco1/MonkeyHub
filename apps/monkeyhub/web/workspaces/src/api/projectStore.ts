/**
 * One open project's client store (#366, ADR-008 phase 2): what its project
 * index holds, kept current by one pull at a time, shared by every surface.
 *
 * The store keeps `{epoch, revision, byId}` and learns of changes three ways,
 * none of which carries data: an `index` hint relayed on the Hub's one event
 * stream (`wanted = max(wanted, hint.revision)`), the stream opening (the
 * first time and on every reconnect), and the window regaining focus. Each
 * one asks `GET /api/index?since=<revision>&epoch=<epoch>` when the store may
 * be behind; at most one such request is in flight, and a request asked for
 * meanwhile runs once it answers. A delta applies only when its `from` is the
 * store's revision; a response that is not newer is dropped; another epoch (a
 * rebuild) replaces everything. Correctness never depends on a hint arriving:
 * a missed one is caught up by the next open, focus or hint.
 *
 * A write answers with the revision that holds it (`X-Monkey-Index`); the
 * surface that made it waits for `caughtUp()` before it ends "in progress".
 *
 * Surfaces read through `useSyncExternalStore` selectors (`useProjectStore`),
 * and are notified at most once per frame. What a surface derives from the
 * project's views without a content hash is kept under the revision it was
 * read at (`derive`): switching surfaces on an unchanged project reads nothing.
 *
 * Nothing here persists: a store is dropped when the last surface of its
 * project lets it go, and the next one reads a snapshot again (a few ms).
 */

export interface IndexEntity {
  readonly id: string;
  readonly domain: string;
  readonly rev: number;
  readonly body: Record<string, unknown>;
}

/** The answer of `GET /api/index`: a snapshot (`reset`) or the changes `from`..`to`. */
export interface IndexAnswer {
  readonly epoch: string;
  readonly revision: number;
  readonly reset: boolean;
  readonly from?: number | null;
  readonly to: number;
  readonly upserts: readonly IndexEntity[];
  readonly deletes: readonly string[];
}

/** A hint that the index moved: to `revision` of `epoch`, or (both absent) that it may have. */
export interface IndexHint {
  readonly epoch?: string | null;
  readonly revision?: number | null;
  readonly domains?: readonly string[];
}

export interface ProjectStoreState {
  /** `idle` before the first snapshot, `unavailable` while the index cannot answer. */
  readonly status: "idle" | "ready" | "unavailable";
  readonly epoch: string | null;
  readonly revision: number;
  readonly byId: ReadonlyMap<string, IndexEntity>;
  /** The revision at which each domain (`run`, `tree`, `area`) last moved; a reset moves them all. */
  readonly domains: Readonly<Record<string, number>>;
}

/** Reads the index: the changes since `since`, a snapshot without it; null while it cannot answer. */
export type IndexReader = (since: { readonly epoch: string; readonly revision: number } | null) => Promise<IndexAnswer | null>;

/** How long a write waits for the store to reach its revision before the surface stops waiting. */
export const WRITE_CATCH_UP_MS = 10_000;

const EMPTY: ProjectStoreState = { status: "idle", epoch: null, revision: 0, byId: new Map(), domains: {} };

function nextFrame(callback: () => void): void {
  if (typeof requestAnimationFrame === "function" && typeof document !== "undefined" && !document.hidden) {
    requestAnimationFrame(() => callback());
  } else {
    setTimeout(callback, 0);
  }
}

export class ProjectStore {
  private state: ProjectStoreState = EMPTY;
  private published: ProjectStoreState = EMPTY;
  private readonly listeners = new Set<() => void>();
  private frameAsked = false;
  private inFlight = false;
  private again = false;
  // The newest revision a hint or a write named, in `wantedEpoch` (null: the store's own).
  private wanted = 0;
  private wantedEpoch: string | null = null;
  private waiters: { epoch: string; revision: number; resolve(): void }[] = [];
  private readonly derived = new Map<string, { key: string; promise: Promise<unknown> }>();
  private readonly frame: (callback: () => void) => void;
  /** Requests made, for tests and diagnostics. */
  pulls = 0;

  private readonly read: IndexReader;

  constructor(read: IndexReader, options: { frame?: (callback: () => void) => void } = {}) {
    this.read = read;
    this.frame = options.frame ?? nextFrame;
  }

  readonly getSnapshot = (): ProjectStoreState => this.published;

  /** The state as of the last answer, before listeners have been told of it (they are, next frame). */
  current(): ProjectStoreState {
    return this.state;
  }

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  /** An `index` hint from the event stream: pull when the store may be behind it. */
  hint(hint: IndexHint | null): void {
    const revision = hint?.revision ?? null;
    const epoch = hint?.epoch ?? null;
    if (revision === null || epoch === null) { this.pull(); return; }
    if (this.state.epoch !== null && epoch !== this.state.epoch) {
      // Another epoch: the store resets on the next answer.
      this.want(epoch, revision);
      this.pull();
      return;
    }
    this.want(epoch, revision);
    if (revision > this.state.revision || this.state.epoch === null) this.pull();
  }

  /** Read the changes since the store's revision (the stream opened, the window came back). */
  pull(): void {
    if (this.inFlight) { this.again = true; return; }
    this.inFlight = true;
    void this.run();
  }

  /** A write committed at `revision` of `epoch`: resolves once the store holds it (or gives up waiting). */
  wrote(epoch: string, revision: number, timeoutMs = WRITE_CATCH_UP_MS): Promise<void> {
    this.want(epoch, revision);
    if (this.holds(epoch, revision)) return Promise.resolve();
    const reached = new Promise<void>((resolve) => { this.waiters.push({ epoch, revision, resolve }); });
    this.pull();
    return Promise.race([reached, new Promise<void>((resolve) => setTimeout(resolve, timeoutMs))]);
  }

  /** Resolves once the store holds every write it was told about (at once when it holds them all). */
  caughtUp(timeoutMs = WRITE_CATCH_UP_MS): Promise<void> {
    if (this.wantedEpoch === null || this.holds(this.wantedEpoch, this.wanted)) return Promise.resolve();
    return this.wrote(this.wantedEpoch, this.wanted, timeoutMs);
  }

  /**
   * `compute()` for `key`, kept: the same name and key answer the same promise,
   * whether it is still being read or has answered. One entry per name: a new
   * key replaces the last. A key that names the revision (`epoch:revision`)
   * makes a derived view that no content hash names cost nothing to show again.
   */
  derive<T>(name: string, key: string, compute: () => Promise<T>): Promise<T> {
    const kept = this.derived.get(name);
    if (kept && kept.key === key) return kept.promise as Promise<T>;
    const promise = compute();
    this.derived.set(name, { key, promise });
    // A failed read is not kept: the next ask reads again.
    promise.catch(() => { if (this.derived.get(name)?.promise === promise) this.derived.delete(name); });
    return promise;
  }

  private holds(epoch: string, revision: number): boolean {
    return this.state.epoch === epoch && this.state.revision >= revision;
  }

  private want(epoch: string, revision: number): void {
    if (this.wantedEpoch !== epoch) { this.wantedEpoch = epoch; this.wanted = revision; }
    else this.wanted = Math.max(this.wanted, revision);
  }

  private async run(): Promise<void> {
    let moved = false;
    try {
      const { epoch, revision } = this.state;
      this.pulls += 1;
      let answer: IndexAnswer | null = null;
      try {
        answer = await this.read(epoch === null ? null : { epoch, revision });
      } catch {
        answer = null;
      }
      if (answer === null) {
        if (this.state.status !== "unavailable" && this.state.epoch === null) this.commit({ ...this.state, status: "unavailable" });
      } else {
        moved = this.apply(answer);
      }
    } finally {
      this.inFlight = false;
    }
    const behind = this.wantedEpoch !== null && !this.holds(this.wantedEpoch, this.wanted);
    // Asked for again meanwhile, or still behind a hint after an answer that moved it: once more.
    if (this.again || (behind && moved)) { this.again = false; this.pull(); }
  }

  /** Apply one answer; true when the store moved. */
  private apply(answer: IndexAnswer): boolean {
    const current = this.state;
    if (answer.reset) {
      // Another epoch always replaces; the same epoch only when newer.
      if (current.epoch === answer.epoch && answer.revision <= current.revision) return false;
      const byId = new Map(answer.upserts.map((entity) => [entity.id, entity] as const));
      const domains: Record<string, number> = {};
      for (const entity of byId.values()) domains[entity.domain] = answer.revision;
      for (const domain of Object.keys(current.domains)) domains[domain] = answer.revision;
      if (this.wantedEpoch !== answer.epoch) { this.wantedEpoch = answer.epoch; this.wanted = answer.revision; }
      this.commit({ status: "ready", epoch: answer.epoch, revision: answer.revision, byId, domains }, true);
      return true;
    }
    if (answer.epoch !== current.epoch || answer.from !== current.revision || answer.to <= current.revision) {
      if (current.status === "unavailable") this.commit({ ...current, status: "ready" });
      return false;
    }
    const byId = new Map(current.byId);
    const domains = { ...current.domains };
    for (const entity of answer.upserts) { byId.set(entity.id, entity); domains[entity.domain] = answer.to; }
    for (const id of answer.deletes) {
      const gone = byId.get(id);
      byId.delete(id);
      domains[gone?.domain ?? id.split(":")[0]] = answer.to;
    }
    this.commit({ status: "ready", epoch: answer.epoch, revision: answer.to, byId, domains });
    return true;
  }

  private commit(next: ProjectStoreState, reset = false): void {
    this.state = next;
    this.waiters = this.waiters.filter((waiter) => {
      // A snapshot of the index as it is now holds every write of an earlier epoch too.
      if (!this.holds(waiter.epoch, waiter.revision) && !(reset && waiter.epoch !== next.epoch)) return true;
      waiter.resolve();
      return false;
    });
    if (this.frameAsked) return;
    this.frameAsked = true;
    this.frame(() => {
      this.frameAsked = false;
      if (this.published === this.state) return;
      this.published = this.state;
      for (const listener of [...this.listeners]) listener();
    });
  }
}

type StudioListener = (event: Record<string, unknown>) => void;

/**
 * Every open project's store, ref-counted, and the Hub's one event stream
 * they share. ChatShell owns the stream: it reports `opened` (each time,
 * reconnects included), relays `index` hints and Studio events by runtime,
 * and asks every store to pull when the window regains focus. A store taken
 * before the stream first opened waits for it: the stream is open before the
 * first snapshot is read, so no commit falls between the two.
 */
export class ProjectStores {
  private readonly stores = new Map<string, { store: ProjectStore; count: number }>();
  private readonly studio = new Map<string, Set<StudioListener>>();
  private open = false;
  /** Whether a Hub stream is relaying for these stores; without one, nothing waits for them. */
  attached = false;

  /** The project's store, created (and read, once the stream is open) when it has none; not counted. */
  store(key: string, read: IndexReader): ProjectStore {
    let entry = this.stores.get(key);
    if (!entry) {
      entry = { store: new ProjectStore(read), count: 0 };
      this.stores.set(key, entry);
      if (this.open) entry.store.pull();
    }
    return entry.store;
  }

  /** The project's store, counted as used until `release`. */
  acquire(key: string, read: IndexReader): ProjectStore {
    const store = this.store(key, read);
    this.stores.get(key)!.count += 1;
    return store;
  }

  /**
   * One use fewer. The last one drops the store, after the current task: a
   * surface that is only re-mounted (React's effect rehearsal) takes it again first.
   */
  release(key: string): void {
    const entry = this.stores.get(key);
    if (!entry) return;
    entry.count -= 1;
    if (entry.count > 0) return;
    setTimeout(() => { if (this.stores.get(key) === entry && entry.count <= 0) this.stores.delete(key); }, 0);
  }

  get(key: string): ProjectStore | undefined {
    return this.stores.get(key)?.store;
  }

  /** The Hub's stream opened (first time or again): whatever it carried meanwhile is read now. */
  opened(): void {
    this.attached = true;
    this.open = true;
    this.pullAll();
  }

  closed(): void {
    this.open = false;
  }

  /** The Hub's stream ended for good (its owner unmounted). */
  detached(): void {
    this.open = false;
    this.attached = false;
  }

  pullAll(): void {
    if (!this.open) return;
    for (const { store } of this.stores.values()) store.pull();
  }

  hint(key: string, hint: IndexHint | null): void {
    this.stores.get(key)?.store.hint(hint);
  }

  onStudioEvent(key: string, listener: StudioListener): () => void {
    let listeners = this.studio.get(key);
    if (!listeners) { listeners = new Set(); this.studio.set(key, listeners); }
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
      if (!listeners.size) this.studio.delete(key);
    };
  }

  studioEvent(key: string, event: Record<string, unknown>): void {
    for (const listener of [...(this.studio.get(key) ?? [])]) listener(event);
  }
}

/** The one registry of this page. */
export const projectStores = new ProjectStores();
