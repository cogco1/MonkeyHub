/**
 * Before / after (#284): one candidate beside the exact model it was made from,
 * in the project's own panel. Read-only throughout: it reads the candidate's
 * retained change, the artifact list, the object comparison and the model bytes,
 * and writes nothing - no Continue, no acceptance, no editing base, no HEAD.
 * Modeling stays mounted behind it, so Return finds it as it was.
 *
 * The original is the one the retained records name (comparisonPair.ts); a
 * candidate that names none, or several, says so here and still returns.
 * Loaded on demand with the two viewports it opens.
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { CompareDto } from "../../../api/project-runtime/generated";
import { asStudioApiError, type StudioApiError } from "../../../api/project-runtime/client";
import { useStudio } from "../../../api/project-runtime/ProjectRuntimeContext";
import type { MessageKey } from "../../../i18n/messages.en";
import { viewableArtifacts } from "../../../features/artifacts/artifactSelection";
import { MenuCommand, MenuSeparator, SurfaceMenus } from "../../../features/chrome/SurfaceChrome";
import { useT } from "../../../i18n/useT";
import { createInteractionSession } from "../interactionSession";
import { ThreeDmViewport, type CameraState, type ViewportController, type ViewportPick, type ViewportStatus } from "./ThreeDmViewport";
import {
  commonBounds, comparisonFor, comparisonPhase, createCameraLink, framingPane, originalChangedNames, PANES, resolveComparisonPair,
  type CameraStep, type ComparisonPair, type ComparisonPane, type ComparisonPhase, type ComparisonRefusal,
} from "./comparisonPair";
import "./modelComparison.css";

type Resolution =
  | { status: "resolving" }
  | { status: "refused"; refusal: ComparisonRefusal }
  | { status: "failed"; error: StudioApiError }
  | { status: "ready"; pair: ComparisonPair };

type ObjectCompare = { status: "pending" } | { status: "ready"; value: CompareDto } | { status: "unavailable"; error: StudioApiError | null };

const noop = () => undefined;

export default function ModelComparison({ projectId, candidateRunId, active, nameOf, backLabel, focusOnOpen, onReturn, onPhase }: {
  projectId: string;
  candidateRunId: string;
  /** The project is on screen; a hidden comparison offers no menus and no hover. */
  active: boolean;
  /** A run's name on the Design Tree, when the tree has it; naming only, never used to choose the original. */
  nameOf(runId: string): string | null;
  backLabel: string;
  /** Opened by a person's click inside this project, so the heading may take focus. */
  focusOnOpen: boolean;
  onReturn(): void;
  onPhase?(phase: ComparisonPhase): void;
}) {
  const t = useT();
  const studio = useStudio();
  const headingId = useId();
  const section = useRef<HTMLElement | null>(null);
  const heading = useRef<HTMLHeadingElement | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [resolution, setResolution] = useState<Resolution>({ status: "resolving" });
  const [objects, setObjects] = useState<ObjectCompare>({ status: "pending" });
  const [files, setFiles] = useState<Partial<Record<ComparisonPane, readonly File[]>>>({});
  const [status, setStatus] = useState<Record<ComparisonPane, ViewportStatus>>({ before: "idle", after: "idle" });
  const [loadFailed, setLoadFailed] = useState<Partial<Record<ComparisonPane, boolean>>>({});
  const [linked, setLinked] = useState(true);
  const [outlineOn, setOutlineOn] = useState(true);
  const [outlineNote, setOutlineNote] = useState<string | null>(null);
  const before = useRef<ViewportController | null>(null);
  const after = useRef<ViewportController | null>(null);
  const views = useMemo(() => ({ before, after }), []);
  const interactions = useRef({ before: { current: createInteractionSession() }, after: { current: createInteractionSession() } });
  const link = useRef(createCameraLink());
  const fitting = useRef(false);
  const pair = resolution.status === "ready" ? resolution.pair : null;
  // Each view as it stands: a model that could not be fetched is that view's error.
  const panes: Record<ComparisonPane, ViewportStatus> = {
    before: loadFailed.before ? "error" : status.before, after: loadFailed.after ? "error" : status.after,
  };
  const phase = comparisonPhase(resolution.status, panes);
  const onPhaseRef = useRef(onPhase);
  onPhaseRef.current = onPhase;
  useEffect(() => { onPhaseRef.current?.(phase); }, [phase]);

  useEffect(() => { if (focusOnOpen) heading.current?.focus({ preventScroll: true }); }, [focusOnOpen]);

  // The exact pair: the candidate's retained change names its original's state; nothing else chooses it.
  useEffect(() => {
    let live = true;
    setResolution({ status: "resolving" });
    setObjects({ status: "pending" });
    setFiles({}); setLoadFailed({}); setOutlineNote(null);
    setStatus({ before: "idle", after: "idle" });
    link.current = createCameraLink();
    link.current.setLinked(linked);
    void Promise.all([studio.runtime(candidateRunId), studio.artifacts()]).then(([runtime, list]) => {
      if (!live) return;
      const result = resolveComparisonPair(projectId, candidateRunId, runtime, { projectId: list.projectId, viewable: viewableArtifacts(list.artifacts) });
      setResolution("pair" in result ? { status: "ready", pair: result.pair } : { status: "refused", refusal: result.refusal });
    }, (cause) => { if (live) setResolution({ status: "failed", error: asStudioApiError(cause) }); });
    return () => { live = false; };
    // The link's on/off is the person's: carried into a retry, never a reason to resolve again.
  }, [studio, projectId, candidateRunId, attempt]);

  // What changed, object by object, as the server counts it for exactly this pair.
  useEffect(() => {
    if (!pair) return;
    let live = true;
    void studio.compare(pair.candidate.runId, pair.base.runId).then((value) => {
      if (!live) return;
      const exact = comparisonFor(pair, value);
      setObjects(exact ? { status: "ready", value: exact } : { status: "unavailable", error: null });
    }, (cause) => { if (live) setObjects({ status: "unavailable", error: asStudioApiError(cause) }); });
    return () => { live = false; };
  }, [studio, pair]);

  // Each side's certified bytes, into its own read-only view.
  useEffect(() => {
    if (!pair) return;
    let live = true;
    const abort = new AbortController();
    for (const pane of PANES) {
      const side = pane === "before" ? pair.base : pair.candidate;
      void Promise.all(side.models.map((model) => studio.artifactFile(model.sha256, model.fileName, undefined, abort.signal)))
        .then(async (opened) => {
          if (!live) return;
          setFiles((value) => ({ ...value, [pane]: opened }));
          await views[pane].current?.openFiles(opened, t(pane === "before" ? "modelCompare.before" : "modelCompare.after"), { isCurrent: () => live });
        })
        .catch(() => { if (live) setLoadFailed((value) => ({ ...value, [pane]: true })); });
    }
    return () => { live = false; abort.abort(); };
    // The label is the source line's word; a language switch does not reopen the models.
  }, [studio, pair, views]);

  const bothReady = status.before === "ready" && status.after === "ready";
  const apply = useCallback((step: CameraStep | null) => {
    if (step) for (const pane of step.panes) views[pane].current?.applyCamera(step.camera);
  }, [views]);
  // One view for both: the two models' own extents together, framed in the narrower pane.
  const align = useCallback(() => {
    const bounds = commonBounds(PANES.map((pane) => views[pane].current?.bounds() ?? null));
    const pane = framingPane({ before: before.current?.viewportSize() ?? null, after: after.current?.viewportSize() ?? null });
    const shared = bounds && views[pane].current?.framing(bounds);
    if (shared) apply(link.current.ready(shared));
  }, [apply, views]);
  const aligned = useRef<ComparisonPair | null>(null);
  useEffect(() => {
    if (!pair || !bothReady || aligned.current === pair) return;
    aligned.current = pair;
    align();
  }, [pair, bothReady, align]);

  const moved = useCallback((pane: ComparisonPane, camera: CameraState) => {
    if (!fitting.current) apply(link.current.moved(pane, camera));
  }, [apply]);
  const toggleLinked = () => { const next = !linked; setLinked(next); apply(link.current.setLinked(next)); };
  const fitBoth = () => {
    fitting.current = true;
    try { before.current?.fitView(); after.current?.fitView(); } finally { fitting.current = false; }
    align();
  };

  // The original's outline over the candidate: the objects it changed or removed when the server named them.
  useEffect(() => {
    if (!pair || status.after !== "ready" || !files.before || objects.status === "pending") return;
    const view = after.current;
    if (!view) return;
    let live = true;
    const colour = (section.current && getComputedStyle(section.current).getPropertyValue("--model-compare-original").trim()) || "#d9480f";
    const names = objects.status === "ready" ? originalChangedNames(objects.value) : null;
    if (names !== null && names.length === 0) {
      void view.originalOutline(null);
      setOutlineNote(t("modelCompare.outlineNone"));
      return;
    }
    const whole = () => view.originalOutline({ files: files.before!, objectNames: null, colour }).then(() => {
      if (live) setOutlineNote(t("modelCompare.outlineWhole"));
    });
    void (names === null ? whole() : view.originalOutline({ files: files.before, objectNames: names, colour }).then((count) => {
      if (!live) return;
      if (count === 0) return whole();
      setOutlineNote(t("modelCompare.outlineChanged", { count }));
    })).catch(() => { if (live) setOutlineNote(t("modelCompare.outlineFailed")); });
    return () => { live = false; };
  }, [pair, status.after, files.before, objects, t]);
  useEffect(() => { after.current?.setOutlineVisible(outlineOn); }, [outlineOn, status.after]);

  // One object, marked in both views where the comparison names it on that side.
  const statusOf = useMemo(() => new Map(objects.status === "ready" ? objects.value.objects.map((object) => [object.name, object.status]) : []), [objects]);
  const mark = useCallback((name: string | null, from?: ComparisonPane) => {
    const state = name === null ? undefined : statusOf.get(name);
    for (const pane of PANES) {
      if (pane === from) continue;
      const present = state !== undefined && (pane === "before" ? state !== "added" : state !== "removed");
      views[pane].current?.highlight(present ? { objectNames: [name!] } : null);
    }
  }, [statusOf, views]);
  const picked = useCallback((pane: ComparisonPane, pick: ViewportPick | null) => mark(pick?.objectName ?? null, pane), [mark]);

  const candidateName = nameOf(candidateRunId);
  const baseName = pair ? nameOf(pair.base.runId) : null;
  const title = t("modelCompare.heading", { name: candidateName ?? t("modelCompare.untitled") });
  const counts = objects.status === "ready" ? ([
    ["changed", objects.value.changed], ["added", objects.value.added], ["removed", objects.value.removed], ["unchanged", objects.value.unchanged],
  ] as const).filter(([kind, count]) => count > 0 || kind === "changed").map(([kind, count]) => t(`modelCompare.count.${kind}` as MessageKey, { count })).join(" · ") : null;
  const failure = resolution.status === "refused" ? t(`modelCompare.refusal.${resolution.refusal}` as MessageKey)
    : resolution.status === "failed" ? t("modelCompare.failed") : null;

  return <section ref={section} className="model-compare" aria-labelledby={headingId} data-phase={phase}>
    <SurfaceMenus label={t("modelCompare.menu")} active={active}>
      <MenuCommand onClick={onReturn}><span aria-hidden="true">←</span> {backLabel}</MenuCommand>
      {pair && <>
        <MenuSeparator />
        <MenuCommand aria-pressed={linked} data-action="linked" onClick={toggleLinked}>{t("modelCompare.linked")}</MenuCommand>
        <MenuCommand aria-pressed={outlineOn} data-action="outline" onClick={() => setOutlineOn((value) => !value)}>{t("modelCompare.outline")}</MenuCommand>
        <MenuCommand disabled={!bothReady} onClick={fitBoth}>{t("modelCompare.fit")}</MenuCommand>
      </>}
    </SurfaceMenus>
    <header className="model-compare__head">
      <h2 id={headingId} ref={heading} tabIndex={-1}>{title}</h2>
      {objects.status === "ready" && objects.value.why && <p className="model-compare__why">{t("modelCompare.request", { why: objects.value.why })}</p>}
      <p className="model-compare__note">{t("modelCompare.readOnly")}</p>
    </header>
    {resolution.status === "resolving" && <p className="model-compare__state" role="status">{t("modelCompare.loading")}</p>}
    {failure && <div className="model-compare__state model-compare__refusal" role="alert">
      <p>{failure}</p>
      <div className="model-compare__actions">
        <button type="button" className="btn btn--small btn--primary" onClick={onReturn}>{backLabel}</button>
        {resolution.status === "failed" && <button type="button" className="btn btn--small" onClick={() => setAttempt((value) => value + 1)}>{t("modelCompare.retry")}</button>}
      </div>
    </div>}
    {pair && <div className="model-compare__panes">
      {PANES.map((pane) => {
        const name = pane === "before" ? baseName : candidateName;
        const caption = pane === "before" ? (name ? t("modelCompare.beforeNamed", { name }) : t("modelCompare.before"))
          : (name ? t("modelCompare.afterNamed", { name }) : t("modelCompare.after"));
        return <figure key={pane} className="model-compare__pane" data-pane={pane} data-status={panes[pane]}>
          <figcaption>{pane === "after" && outlineOn && <span className="model-compare__swatch" aria-hidden="true" />}{caption}</figcaption>
          <div className="model-compare__view">
            <ThreeDmViewport ref={views[pane]} interaction={interactions.current[pane]} hoverEnabled={active} readOnly
              idle={<p>{loadFailed[pane] ? t("modelCompare.modelFailed") : t("modelCompare.loadingModels")}</p>}
              onInspection={noop} onRequestFile={noop} onSource={noop}
              onStatus={(next) => setStatus((value) => value[pane] === next ? value : { ...value, [pane]: next })}
              onPick={(pick) => picked(pane, pick)} onCameraChange={(camera) => moved(pane, camera)} />
          </div>
        </figure>;
      })}
    </div>}
    {pair && outlineOn && outlineNote && <p className="model-compare__note">{outlineNote}</p>}
    <details className="model-compare__details">
      <summary>{t("modelCompare.details")}</summary>
      {pair && <p className="model-compare__summary">{counts !== null ? t("modelCompare.summary", { counts })
        : objects.status === "pending" ? t("modelCompare.summaryPending") : t("modelCompare.summaryUnavailable")}</p>}
      <dl>
        <div><dt>{t("modelCompare.detail.candidateRun")}</dt><dd>{candidateRunId}</dd></div>
        {pair && <>
          <div><dt>{t("modelCompare.detail.baseRun")}</dt><dd>{pair.base.runId}</dd></div>
          <div><dt>{t("modelCompare.detail.candidateState")}</dt><dd>{pair.candidate.stateDigest}</dd></div>
          <div><dt>{t("modelCompare.detail.baseState")}</dt><dd>{pair.base.stateDigest}</dd></div>
          <div><dt>{t("modelCompare.detail.files")}</dt><dd>{[...pair.base.models, ...pair.candidate.models].map((model) => `${model.runId} · ${model.fileName} · ${model.sha256}`).join("\n")}</dd></div>
        </>}
        {objects.status === "ready" && <div><dt>{t("modelCompare.detail.tolerance")}</dt><dd>{objects.value.tolerance}</dd></div>}
        {resolution.status === "failed" && <div><dt>{t("modelCompare.detail.error")}</dt><dd>{resolution.error.code}: {resolution.error.detail}</dd></div>}
        {resolution.status === "refused" && <div><dt>{t("modelCompare.detail.error")}</dt><dd>{resolution.refusal}</dd></div>}
        {objects.status === "unavailable" && objects.error && <div><dt>{t("modelCompare.detail.error")}</dt><dd>{objects.error.code}: {objects.error.detail}</dd></div>}
      </dl>
      <p>{t("modelCompare.detail.binding")}</p>
      {objects.status === "ready" && objects.value.objects.length > 0 && <>
        <p>{t("modelCompare.detail.hint")}</p>
        <div className="model-compare__table">
          <table>
            <thead><tr><th scope="col">{t("modelCompare.detail.object")}</th><th scope="col">{t("modelCompare.detail.status")}</th><th scope="col">{t("modelCompare.detail.component")}</th></tr></thead>
            <tbody>{objects.value.objects.map((object) => <tr key={`${object.seatId}:${object.name}`} data-status={object.status}>
              <td><button type="button" className="model-compare__object" onClick={() => mark(object.name)}>{object.name}</button></td>
              <td>{statusWords.has(object.status) ? t(`modelCompare.status.${object.status}` as MessageKey) : object.status}</td>
              <td>{object.componentId ?? "—"}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </>}
    </details>
  </section>;
}

const statusWords = new Set(["changed", "added", "removed", "unchanged"]);
