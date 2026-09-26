import type { PublicationDto, PublicationRequestDto } from "../../api/generated";

/** Publication CAS saves acknowledge the sent snapshot, never newer typing. */
export function createPublicationSaveQueue(initial: PublicationDto,
  write: (body: PublicationRequestDto) => Promise<PublicationDto>,
  notify: (draft: PublicationDto, dirty: boolean, saving: boolean, error: unknown) => void,
  delay = 700,
) {
  let current = structuredClone(initial), acknowledged = content(initial), error: unknown = null;
  let writing: Promise<PublicationDto> | null = null, timer: ReturnType<typeof setTimeout> | undefined;
  let closed = false;
  let importing = false;
  let pendingImport: ((base: PublicationDto) => Promise<PublicationDto>) | null = null;
  function content(value: PublicationDto) { return JSON.stringify([value.title, value.spec, value.pages]); }
  const dirty = () => content(current) !== acknowledged;
  const publish = () => { if (!closed) notify(current, dirty(), writing !== null, error); };
  const clear = () => { clearTimeout(timer); timer = undefined; };
  function flush(): Promise<PublicationDto> {
    clear();
    if (writing) return writing;
    if (error) return Promise.reject(error);
    if (!dirty()) return Promise.resolve(current);
    writing = Promise.resolve().then(async () => {
      while (dirty()) {
        const sent = current, signature = content(sent);
        const saved = await write({ projectId: sent.projectId, baseRevisionSha256: sent.revisionSha256,
          title: sent.title, spec: sent.spec, pages: sent.pages });
        if (saved.projectId !== initial.projectId || !saved.revisionSha256) throw new Error("The saved publication did not return its project and revision.");
        acknowledged = signature;
        current = content(current) === signature ? saved : { ...current, revisionSha256: saved.revisionSha256, sources: saved.sources };
        publish();
      }
      return current;
    }).catch((cause) => { error = cause; throw cause; }).finally(() => { writing = null; publish(); });
    publish();
    return writing;
  }
  async function append(importPages: (base: PublicationDto) => Promise<PublicationDto>) {
    pendingImport = importPages;
    await flush();
    importing = true;
    writing = Promise.resolve().then(async () => {
      const saved = await importPages(current);
      if (saved.projectId !== initial.projectId || !saved.revisionSha256) throw new Error("The imported publication did not return its project and revision.");
      current = saved; acknowledged = content(saved); error = null; pendingImport = null; return current;
    }).catch((cause) => { error = cause; throw cause; }).finally(() => { importing = false; writing = null; publish(); });
    publish();
    return writing;
  }
  return {
    current: () => current,
    pending: () => dirty() || writing !== null || pendingImport !== null,
    importing: () => importing,
    change(next: PublicationDto) {
      if (closed || importing) return;
      current = structuredClone({ ...next, revisionSha256: current.revisionSha256 });
      clear(); publish();
      if (!error && !writing) timer = setTimeout(() => { void flush().catch(() => {}); }, delay);
    },
    accept(saved: PublicationDto) {
      if (dirty() || writing || pendingImport) return false;
      if (saved.projectId !== initial.projectId) throw new Error("The publication belongs to another project.");
      current = structuredClone(saved); acknowledged = content(saved); error = null; publish(); return true;
    },
    flush,
    append,
    retry() { error = null; return pendingImport ? append(pendingImport) : flush(); },
    resume(nextWrite: typeof write, nextNotify: typeof notify) {
      write = nextWrite; notify = nextNotify; closed = false; publish();
    },
    discard() { closed = true; clear(); },
    dispose() { closed = true; clear(); void flush().catch(() => {}); },
  };
}
