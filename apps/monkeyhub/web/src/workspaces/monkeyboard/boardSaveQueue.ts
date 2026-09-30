import type { BoardDto, BoardRequestDto } from "../../api/generated";
import { imageSource, isBoardConflict, type BoardDraft } from "./boardScene";

const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const same = (left: unknown, right: unknown): boolean => {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((value, index) => same(value, right[index]));
  if (!object(left) || !object(right)) return false;
  const keys = Object.keys(left);
  return keys.length === Object.keys(right).length && keys.every((key) => Object.hasOwn(right, key) && same(left[key], right[key]));
};
const overlapping = () => Object.assign(new Error("The same board content was changed in both versions. Local marks are still available."), { code: "BOARD_CONFLICT" });

/** Merge independent edits; an exact page source and a drawn path are indivisible. */
function mergeValue(base: unknown, local: unknown, remote: unknown, key = ""): unknown {
  if (same(local, remote) || same(remote, base)) return structuredClone(local);
  if (same(local, base)) return structuredClone(remote);
  if (key === "sourceDocument" || key === "annotation" || !object(base) || !object(local) || !object(remote)) throw overlapping();
  return Object.fromEntries([...new Set([...Object.keys(base), ...Object.keys(local), ...Object.keys(remote)])]
    .map((name) => [name, mergeValue(base[name], local[name], remote[name], name)])
    .filter(([, value]) => value !== undefined));
}

function mergeDraft(base: BoardDraft, local: BoardDraft, remote: BoardDraft): BoardDraft {
  const rows = (draft: BoardDraft) => new Map(draft.elements.map((element) => [element.id, element]));
  const before = rows(base), here = rows(local), there = rows(remote);
  const common = new Set([...before.keys()].filter((id) => here.has(id) && there.has(id)));
  const order = (draft: BoardDraft) => draft.elements.map((element) => element.id).filter((id) => common.has(id));
  const localOrder = order(local), remoteOrder = order(remote), baseOrder = order(base);
  if (!same(localOrder, baseOrder) && !same(remoteOrder, baseOrder) && !same(localOrder, remoteOrder)) throw overlapping();
  const primary = same(localOrder, baseOrder) ? remote : local;
  const secondary = primary === remote ? local : remote;
  const ids = [...new Set([...primary.elements, ...secondary.elements].map((element) => element.id))];
  const content = (element: Record<string, unknown> | undefined) => element && Object.fromEntries(
    Object.entries(element).filter(([key]) => !["version", "versionNonce", "updated"].includes(key)));
  const elements = ids.flatMap((id) => {
    const b = before.get(id), l = here.get(id), r = there.get(id);
    const localContent = content(l), remoteContent = content(r), originalRemoteContent = content(r);
    // Receiving the same page twice allocates different preview cache ids, not different drawings.
    if (l && r && imageSource(l) && same(imageSource(l), imageSource(r)) && l.fileId !== b?.fileId && remoteContent) remoteContent.fileId = l.fileId;
    const merged = mergeValue(content(b), localContent, remoteContent, ["image", "frame"].includes(String(l?.type ?? r?.type)) ? "" : "annotation");
    if (!object(merged)) return [];
    if (same(merged, originalRemoteContent) && r) return [structuredClone(r)];
    if (same(merged, localContent) && l) return [structuredClone(l)];
    return [{ ...merged, version: Math.max(Number(l?.version ?? 0), Number(r?.version ?? 0)) + 1,
      versionNonce: r?.versionNonce ?? l?.versionNonce ?? 0, updated: Math.max(Number(l?.updated ?? 0), Number(r?.updated ?? 0)) }];
  });
  return { projectId: local.projectId, title: mergeValue(base.title, local.title, remote.title) as string,
    elements, seenDocuments: [...new Set([...local.seenDocuments, ...remote.seenDocuments])] };
}

interface BoardRefresh {
  readLatest: () => Promise<BoardDto>;
  onRebase: (draft: BoardDraft) => void;
}

export interface BoardSaveState {
  dirty: boolean;
  saving: boolean;
  error: unknown | null;
  conflict: boolean;
  /** The revision every acknowledged save is chained to; what a board sketch cites. */
  revisionSha256: string | null;
}

/** One single-user CAS queue. A response acknowledges its sent snapshot, never the current canvas. */
export function createBoardSaveQueue(
  initial: BoardDto,
  write: (body: BoardRequestDto) => Promise<BoardDto>,
  onState: (state: BoardSaveState) => void,
  delayMs = 700,
  refresh?: BoardRefresh,
) {
  let current: BoardDraft = { projectId: initial.projectId, title: initial.title,
    elements: structuredClone(initial.elements), seenDocuments: [...initial.seenDocuments] };
  let signature = JSON.stringify(current);
  let acknowledged = signature;
  let base = structuredClone(current);
  let revision = initial.revisionSha256;
  let writing: Promise<void> | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let error: unknown | null = null;
  let closed = false;
  const state = (): BoardSaveState => ({ dirty: signature !== acknowledged,
    saving: writing !== null, error, conflict: isBoardConflict(error), revisionSha256: revision });
  const publish = () => { if (!closed) onState(state()); };
  const clearTimer = () => { if (timer !== undefined) clearTimeout(timer); timer = undefined; };

  function rebase(remote: BoardDto) {
    if (remote.projectId !== initial.projectId || remote.revisionSha256 === null) {
      throw Object.assign(new Error("The refreshed board belongs to another project or has no revision."), { code: "BOARD_BINDING_MISMATCH" });
    }
    const nextBase: BoardDraft = { projectId: remote.projectId, title: remote.title,
      elements: remote.elements, seenDocuments: remote.seenDocuments };
    const next = mergeDraft(base, current, nextBase);
    base = structuredClone(nextBase);
    current = same(next, nextBase) ? structuredClone(nextBase) : next;
    revision = remote.revisionSha256;
    acknowledged = JSON.stringify(base);
    signature = JSON.stringify(current);
    error = null;
    if (!closed) refresh?.onRebase(structuredClone(current));
    publish();
  }

  function flush(): Promise<void> {
    clearTimer();
    if (writing) return writing;
    if (error !== null) return Promise.reject(error);
    if (signature === acknowledged) return Promise.resolve();
    writing = Promise.resolve().then(async () => {
      let rebases = 0;
      while (signature !== acknowledged && error === null) {
        const sent = current;
        const sentSignature = signature;
        try {
          const saved = await write({ ...sent, baseRevisionSha256: revision });
          if (saved.projectId !== initial.projectId || saved.revisionSha256 === null) {
            throw new Error("The saved board did not return its project and revision.");
          }
          revision = saved.revisionSha256;
          acknowledged = sentSignature;
          base = structuredClone(sent);
        } catch (cause) {
          if (object(cause) && cause.code === "BOARD_STALE" && refresh && rebases++ < 3) {
            try { rebase(await refresh.readLatest()); continue; }
            catch (recoveryError) { error = recoveryError; throw recoveryError; }
          }
          error = cause;
          throw cause;
        }
      }
    }).finally(() => {
      writing = null;
      publish();
    });
    publish();
    return writing;
  }

  return {
    getState: state,
    /** External delivery may update a quiet board before it has another local edit to save. */
    async refresh() {
      if (closed || writing || !refresh) return;
      const previousRevision = revision;
      const remote = await refresh.readLatest();
      if (closed || writing || revision !== previousRevision || remote.revisionSha256 === revision) return;
      try { rebase(remote); }
      catch (cause) { error = cause; publish(); throw cause; }
      clearTimer();
      if (signature !== acknowledged) timer = setTimeout(() => { void flush().catch(() => {}); }, delayMs);
    },
    change(next: BoardDraft) {
      if (closed) return;
      const nextSignature = JSON.stringify(next);
      if (nextSignature === signature) return;
      current = structuredClone(next);
      signature = nextSignature;
      clearTimer();
      publish();
      if (error === null && !writing) timer = setTimeout(() => { void flush().catch(() => {}); }, delayMs);
    },
    flush,
    retry(): Promise<void> {
      if (isBoardConflict(error)) return Promise.reject(error);
      error = null;
      return flush();
    },
    /** Flush the final local edit, but stop timers and all UI notifications after unmount. */
    dispose() {
      closed = true;
      clearTimer();
      void flush().catch(() => {});
    },
  };
}

export type BoardSaveQueue = ReturnType<typeof createBoardSaveQueue>;
