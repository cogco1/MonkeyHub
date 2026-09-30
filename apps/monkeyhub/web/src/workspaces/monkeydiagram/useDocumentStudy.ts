import { useEffect, useRef, useState } from "react";
import { useStudio } from "../../api/ProjectRuntimeContext";
import { asStudioApiError } from "../../api/client";
import type { StudySourceRequestDto, StudyViewDto } from "../../api/generated";
import { createStudyDraft, studyDraftFromView, studyMatchesSource, studyResearchInput,
  type StudyDraft } from "./documentStudy";

/** A Study draft belongs to one exact page. Canvas gestures never enter the ink controller. */
export function useDocumentStudy(projectId: string, source: StudySourceRequestDto | null) {
  const studio = useStudio();
  const scope = JSON.stringify([projectId, source]);
  const generation = useRef(0);
  const [state, setState] = useState({ scope, history: [createStudyDraft("pending")], cursor: 0,
    view: null as StudyViewDto | null, ready: false, busy: false, error: "",
    acknowledged: "", choices: [] as StudyViewDto[] });
  const current = useRef(state); current.current = state;
  const writing = useRef<Promise<StudyViewDto> | null>(null);
  const sameScope = state.scope === scope;
  const ready = sameScope && state.ready;
  const draft = sameScope ? state.history[state.cursor] : createStudyDraft("pending");
  const dirty = ready && JSON.stringify(draft) !== state.acknowledged;

  const load = async () => {
    if (!source) return;
    const token = ++generation.current;
    setState(value => ({ ...value, scope, ready: false, error: "",
      ...(value.scope === scope ? {} : { history: [createStudyDraft("pending")], cursor: 0, view: null, acknowledged: "" }) }));
    try {
      const choices = await studio.studies();
      const matches = choices.filter(view => studyMatchesSource(view, source));
      if (matches.length > 1) throw new Error("此图页有多个 Study，请通过来源修订解决歧义后重开。");
      const view = matches[0] ?? null;
      const identity = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(scope));
      const id = "page-" + [...new Uint8Array(identity)].map(v => v.toString(16).padStart(2, "0")).join("").slice(0, 32);
      const next = view ? studyDraftFromView(view) : createStudyDraft(id);
      if (generation.current !== token) return;
      setState({ scope, history: [next], cursor: 0, view, choices, ready: true, busy: false,
        error: "", acknowledged: JSON.stringify(next) });
    } catch (error) {
      if (generation.current === token) setState(value => ({ ...value, ready: false, busy: false,
        error: asStudioApiError(error).detail }));
    }
  };
  useEffect(() => {
    writing.current = null;
    if (source) void load();
    else setState(value => ({ ...value, ready: false }));
    return () => { generation.current++; };
  }, [scope, studio]);

  const change = (next: StudyDraft) => setState(value => {
    if (value.scope !== scope || value.busy || !value.ready || JSON.stringify(next) === JSON.stringify(value.history[value.cursor])) return value;
    return { ...value, history: [...value.history.slice(0, value.cursor + 1), next], cursor: value.cursor + 1 };
  });
  const save = (replacement?: StudyDraft): Promise<StudyViewDto> => {
    if (writing.current) return writing.current;
    if (!source || current.current.scope !== scope || !current.current.ready) return Promise.reject(new Error("Study source is not ready"));
    const snapshot = current.current;
    const input = replacement ?? snapshot.history[snapshot.cursor];
    if (JSON.stringify(input) === snapshot.acknowledged && snapshot.view) return Promise.resolve(snapshot.view);
    const token = generation.current;
    setState(value => ({ ...value, busy: true, error: "",
      ...(replacement ? { history: [...value.history.slice(0, value.cursor + 1), replacement], cursor: value.cursor + 1 } : {}) }));
    const pending = studio.saveStudy({ projectId, studyId: input.studyId, source,
      expectedPreviousRef: snapshot.view?.ledgerRef ?? null,
      evidence: input.evidence.map(row => ({ evidenceId: row.evidenceId, kind: row.kind,
        points: row.geometry.points, status: row.status, confidence: row.confidence, origin: row.origin })),
      research: studyResearchInput(input.research),
    }).then(view => {
      const normalized = studyDraftFromView(view);
      if (generation.current === token) setState(value => ({ ...value, view, busy: false,
        history: value.history.map((item, index) => index === value.cursor ? normalized : item),
        acknowledged: JSON.stringify(normalized), choices: [...value.choices.filter(row => row.studyId !== view.studyId), view] }));
      return view;
    }).catch(error => {
      if (generation.current === token) setState(value => ({ ...value, busy: false, error: asStudioApiError(error).detail }));
      throw error;
    }).finally(() => { if (writing.current === pending) writing.current = null; });
    writing.current = pending;
    return pending;
  };
  const compare = async (target: { studyId: string; ledgerRef: string }) => {
    const token = generation.current;
    try {
      const view = await save();
      if (generation.current !== token) return;
      const definition = { studies: [
        { studyId: view.studyId, ledgerRef: view.ledgerRef },
        { studyId: target.studyId, ledgerRef: target.ledgerRef }] };
      const next = studyDraftFromView(view);
      if (next.research.comparisons.length >= 6) throw new Error("最多保留六组比较；请先移除不再需要的比较。");
      next.research.comparisons.push(definition);
      await save(next);
    } catch (error) { if (generation.current === token) setState(value => ({ ...value, busy: false, error: asStudioApiError(error).detail })); }
  };
  const propose = async (action: "trace" | "reason") => {
    const token = generation.current;
    try {
      const view = await save();
      if (generation.current !== token) return;
      setState(value => ({ ...value, busy: true, error: "" }));
      const proposed = await studio.proposeStudy({ projectId, studyId: view.studyId,
        expectedPreviousRef: view.ledgerRef, action });
      if (generation.current !== token) return;
      const next = studyDraftFromView(proposed);
      setState(value => ({ ...value, busy: false, history: [next], cursor: 0, view: proposed,
        acknowledged: JSON.stringify(next), choices: [...value.choices.filter(row => row.studyId !== proposed.studyId), proposed] }));
    } catch (error) { if (generation.current === token) setState(value => ({ ...value, busy: false, error: asStudioApiError(error).detail })); }
  };
  return { ...state, ready, view: sameScope ? state.view : null, error: sameScope ? state.error : "", draft, dirty, change, save, reload: load, compare, propose,
    canUndo: ready && !state.busy && state.cursor > 0,
    canRedo: ready && !state.busy && state.cursor < state.history.length - 1,
    undo: () => setState(value => ({ ...value, cursor: Math.max(0, value.cursor - 1) })),
    redo: () => setState(value => ({ ...value, cursor: Math.min(value.history.length - 1, value.cursor + 1) })),
  };
}
