import type { BoardDto, BoardRequestDto } from "../../api/generated";
import { isBoardConflict, type BoardDraft } from "./boardScene";

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
) {
  let current: BoardDraft = { projectId: initial.projectId, title: initial.title,
    elements: structuredClone(initial.elements), seenDocuments: [...initial.seenDocuments] };
  let signature = JSON.stringify(current);
  let acknowledged = signature;
  let revision = initial.revisionSha256;
  let writing: Promise<void> | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let error: unknown | null = null;
  let closed = false;
  const state = (): BoardSaveState => ({ dirty: signature !== acknowledged,
    saving: writing !== null, error, conflict: isBoardConflict(error), revisionSha256: revision });
  const publish = () => { if (!closed) onState(state()); };
  const clearTimer = () => { if (timer !== undefined) clearTimeout(timer); timer = undefined; };

  function flush(): Promise<void> {
    clearTimer();
    if (writing) return writing;
    if (error !== null) return Promise.reject(error);
    if (signature === acknowledged) return Promise.resolve();
    writing = Promise.resolve().then(async () => {
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
        } catch (cause) {
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
