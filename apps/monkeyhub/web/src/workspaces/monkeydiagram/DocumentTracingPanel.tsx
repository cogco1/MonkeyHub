import { useContext, useEffect, useState } from "react";
import { asStudioApiError } from "../../api/project-runtime/client";
import { useT } from "../../i18n/useT";
import { InlineDecision } from "../../notifications/cards/InlineDecision";
import type { DocumentAnnotationsHandle } from "./useDocumentAnnotations";
import { DocumentTracingContext } from "./DocumentTracingContext";
import type { drawnPlanCalibration } from "./drawnCalibration";

/** Corrected ink remains page evidence until this explicit modeling action. */
export function DocumentTracingPanel({ draft, disabled, onSendingChange, drawn = null }: {
  draft: DocumentAnnotationsHandle; disabled: boolean; onSendingChange(value: boolean): void;
  /** The page's scale and direction as its drawing gives them (F093); null when they have to be measured. */
  drawn?: ReturnType<typeof drawnPlanCalibration>;
}) {
  const modeling = useContext(DocumentTracingContext);
  const t = useT();
  const [lineId, setLineId] = useState("");
  const [distance, setDistance] = useState("");
  const [height, setHeight] = useState("3");
  const [baseLevel, setBaseLevel] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [continuing, setContinuing] = useState(false);
  const [recording, setRecording] = useState(false);
  // After Record edits and continue, the generation the edits held back runs once they are recorded.
  const [generateAfterRecord, setGenerateAfterRecord] = useState(false);
  // F093: a drawn plan's scale and direction come from the drawing; measuring a line instead is the person's choice.
  const [measure, setMeasure] = useState(false);
  const fromDrawing = drawn !== null && !measure;
  useEffect(() => {
    if (!fromDrawing || !draft.ready || draft.readOnly || disabled || draft.tracingCalibration) return;
    draft.setTracingCalibration({ origin: drawn.origin, axisPoint: drawn.axisPoint, distance: drawn.distance });
  }, [fromDrawing, drawn, draft, disabled]);
  useEffect(() => {
    if (!generateAfterRecord || !modeling) return;
    setGenerateAfterRecord(false);
    if (!modeling.blockedReason) void generate();
  });
  if (!modeling) return null;
  const lines = draft.annotations.filter(mark => mark.kind === "line" && mark.points.length === 2);
  const shapes = draft.annotations.filter(mark => mark.kind === "polyline" || mark.kind === "line");
  const selected = shapes.filter(mark => chosen.includes(mark.id));
  const locked = disabled || !draft.ready || draft.readOnly;
  const calibrate = () => {
    const line = lines.find(mark => mark.id === lineId);
    const length = Number(distance);
    if (!line || !distance.trim() || !Number.isFinite(length) || length <= 0 ||
      line.points[0].every((value, index) => value === line.points[1][index])) {
      setError(t("document.trace.invalidScale")); return;
    }
    draft.setTracingCalibration({ origin: line.points[0], axisPoint: line.points[1], distance: length });
    setError(null); setSubmitted(false);
  };
  const generate = async () => {
    if (locked || modeling.blockedReason || !draft.tracingCalibration || selected.length === 0) return;
    const pull = selected.some(mark => mark.closed) ? Number(height) : 0;
    if (!Number.isFinite(pull) || (selected.some(mark => mark.closed) && (!height.trim() || pull <= 0))) {
      setError(t("document.trace.invalidHeight")); return;
    }
    setError(null); setSubmitted(false); onSendingChange(true);
    try {
      const ref = await draft.save();
      await modeling.generate({ ...ref, annotationIds: selected.map(mark => mark.id) }, pull,
        baseLevel || (modeling.levels.length === 1 ? modeling.levels[0].levelId : ""));
      setSubmitted(true);
    } catch (cause) { setError(asStudioApiError(cause).detail); }
    finally { onSendingChange(false); }
  };
  const recordEdits = async () => {
    if (!modeling.recordEdits || recording) return;
    setRecording(true); setError(null);
    try { await modeling.recordEdits(); setGenerateAfterRecord(true); }
    catch (cause) { setError(t("stage.record.failed", { reason: asStudioApiError(cause).detail })); }
    finally { setRecording(false); }
  };
  const continueViewed = async () => {
    if (!modeling.continueViewed || continuing) return;
    setContinuing(true); setError(null);
    try { await modeling.continueViewed(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setContinuing(false); }
  };
  return <section className="document-tracing" aria-label={t("document.trace.title")}>
    <strong>{t("document.trace.title")}</strong>
    <p>{t("document.trace.manual")}</p>
    <p>{t("document.trace.drawHint")}</p>
    {fromDrawing ? <p className="document-tracing__drawn">{t("document.trace.fromDrawing", { scale: drawn.scaleLabel })}
      <button type="button" className="btn btn--link" data-command="sketch.to_model.calibrate" onClick={() => setMeasure(true)}>{t("document.trace.measureInstead")}</button></p>
    : <form onSubmit={event => { event.preventDefault(); calibrate(); }}>
      <label>{t("document.trace.baseline")}
        <select value={lineId} disabled={locked} onChange={event => setLineId(event.target.value)}>
          <option value="">{t("document.trace.chooseLine")}</option>
          {lines.map((mark, index) => <option key={mark.id} value={mark.id}>{t("document.trace.line", { n: index + 1 })}</option>)}
        </select>
      </label>
      <label>{t("document.trace.distance")}
        <input type="number" step="any" min="0" value={distance} disabled={locked} onChange={event => setDistance(event.target.value)} />
      </label>
      <button data-command="sketch.to_model.calibrate" type="submit" className="btn" disabled={locked || !lineId}>{t("document.trace.calibrate")}</button>
    </form>}
    <p>{t(draft.tracingCalibration ? "document.trace.calibrated" : "document.trace.noScale",
      { distance: draft.tracingCalibration?.distance ?? "" })}</p>
    <fieldset disabled={locked}><legend>{t("document.trace.shapes")}</legend>
      {shapes.length === 0 && <p>{t("document.trace.noShapes")}</p>}
      {shapes.map((mark, index) => <label className="document-tracing__shape" key={mark.id}>
        <input type="checkbox" checked={chosen.includes(mark.id)} onChange={event => {
          setChosen(current => event.target.checked ? [...current, mark.id] : current.filter(id => id !== mark.id)); setSubmitted(false);
        }} />
        <span>{t(mark.closed ? "document.trace.contour" : "document.trace.curve", { n: index + 1 })}</span>
      </label>)}
    </fieldset>
    <label>{t("document.trace.height")}
      <input type="number" step="any" min="0" value={height} disabled={locked} onChange={event => { setHeight(event.target.value); setSubmitted(false); }} />
    </label>
    {modeling.levels.length > 1 && <label>{t("document.trace.level")}
      <select value={baseLevel} disabled={locked} onChange={event => setBaseLevel(event.target.value)}>
        <option value="">{t("document.trace.chooseLevel")}</option>
        {modeling.levels.map(level => <option key={level.levelId} value={level.levelId}>{level.role} · {level.elevation} m</option>)}
      </select>
    </label>}
    {modeling.blockedReason && <InlineDecision className="document-tracing__decision" live label={modeling.blockedReason}
      title={modeling.blockedReason} choices={[
        modeling.continueViewed && { command: "design.go_to_step.continue", primary: true, disabled: disabled || continuing,
          label: t(continuing ? "document.modelSource.continuing" : "stage.base.continue"), onSelect: () => void continueViewed() },
        modeling.recordEdits && { command: "draft.record.continue", primary: !modeling.continueViewed, disabled: locked || recording,
          label: t(recording ? "stage.record.busy" : "stage.record.continue"), onSelect: () => void recordEdits() },
      ]} />}
    <button data-command="sketch.to_model.send" type="button" className="btn btn--accent" disabled={locked || !!modeling.blockedReason || !draft.tracingCalibration || selected.length === 0}
      onClick={() => void generate()}>{t(disabled ? "document.trace.generating" : "document.trace.generate")}</button>
    {submitted && <p role="status">{t("document.trace.submitted")}</p>}
    {submitted && <button data-command="view.background.open" type="button" className="btn" onClick={modeling.showModel}>{t("document.trace.showModel")}</button>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
