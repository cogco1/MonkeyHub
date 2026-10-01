/**
 * The component information card (#549): one closable side panel in Modeling, docked where the
 * Versions inspector docks, showing what the Board's datasets say about the picked component.
 *
 * The Board is read here, not on a click: once Modeling shows a model, and again whenever the
 * project moves, and a Board revision already read is never parsed twice. A click only looks the
 * picked component up in what was read. Nothing here writes, asks a model or leaves the machine.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { useProjectRevision, useStudio } from "../../api/project-runtime/ProjectRuntimeContext";
import type { BoardDto } from "../../api/project-runtime/generated";
import { useT } from "../../i18n/useT";
import { readBoardDatasets } from "./boardDatasets";
import { componentCardText, fieldSource, fieldValue, stateSentence, statusBadge, type BoardInfo, type CardBlock, type CardField,
  type CardWords, type ComponentCard } from "./componentInfo";
import "./ComponentInfoCard.css";

/**
 * The datasets on this project's Board, read while `active` (Modeling showing a model) and parsed once
 * per Board revision. In the Hub the project's store says when anything moved, so Modeling shown again
 * on an unchanged project asks nothing (#366); outside it, being shown again reads again.
 */
export function useComponentInfoBoard(projectId: string | null, active: boolean): BoardInfo {
  const studio = useStudio();
  const revision = useProjectRevision();
  const [info, setInfo] = useState<BoardInfo>({ status: "idle" });
  const read = useRef<{ board: BoardDto; projectId: string; revision: string | null } | null>(null);
  useEffect(() => {
    if (!active || !projectId) return;
    const kept = read.current;
    if (kept && kept.projectId === projectId && revision !== null && kept.revision === revision) return;
    let live = true;
    if (kept?.projectId !== projectId) setInfo({ status: "loading" });
    studio.board().then((board) => {
      if (!live) return;
      if (board.projectId !== projectId) throw new Error("The board belongs to another project.");
      // An unchanged board answers with the very object kept (#363), and the same revision is the same scene.
      const last = read.current;
      read.current = { board, projectId, revision };
      if (last && last.projectId === projectId && (last.board === board || (board.revisionSha256 !== null
        && last.board.revisionSha256 === board.revisionSha256))) {
        setInfo((current) => current.status === "ready" ? current
          : { status: "ready", projectId, revisionSha256: board.revisionSha256, ...readBoardDatasets(board.elements) });
        return;
      }
      setInfo({ status: "ready", projectId, revisionSha256: board.revisionSha256, ...readBoardDatasets(board.elements) });
    }).catch((error: unknown) => {
      if (!live) return;
      read.current = null;
      setInfo({ status: "error", error });
    });
    return () => { live = false; };
  }, [studio, projectId, active, revision]);
  if (!projectId) return { status: "idle" };
  // What was read for another project is not this one's, even for the render before the read.
  return info.status === "ready" && info.projectId !== projectId ? { status: "loading" } : info;
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // A page without clipboard permission still copies through a selection.
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try { return document.execCommand("copy"); } finally { area.remove(); }
  }
}

function CopyButton({ text, label, done, className = "btn btn--small" }: { text: () => string; label: string; done: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);
  return <button type="button" className={className} onClick={() => { void copyText(text()).then(setCopied); }}>
    {copied ? done : label}</button>;
}

function Field({ field, t }: { field: CardField; t: CardWords }) {
  const source = fieldSource(field);
  const badge = statusBadge(field);
  return <div className="component-info__field" data-status={field.status}>
    <dt>{field.label}</dt>
    <dd>
      <span className="component-info__value">{fieldValue(field)}</span>
      {badge && <span className="component-info__status" data-status={field.status}>{badge}</span>}
      {field.note && <span className="component-info__note">{field.note}</span>}
      {/* The desktop shell opens no address outside the Hub: a link is text to copy. */}
      {field.url && <span className="component-info__url">
        <span className="component-info__link">{field.url}</span>
        <CopyButton text={() => field.url!} label={t("componentInfo.copyLink")} done={t("componentInfo.copied")}
          className="btn btn--small component-info__link-copy" />
      </span>}
      {source && <span className="component-info__source">{t("componentInfo.source", { source })}</span>}
    </dd>
  </div>;
}

function Fields({ fields, t }: { fields: readonly CardField[]; t: CardWords }) {
  if (fields.length === 0) return null;
  return <dl className="component-info__fields">{fields.map((field, index) => <Field key={index} field={field} t={t} />)}</dl>;
}

function Block({ block, heading, t }: { block: CardBlock; heading: boolean; t: CardWords }) {
  const Tag = block.role === "section" ? "section" : "div";
  return <Tag className="component-info__block" data-dataset={block.datasetId} data-role={block.role}>
    {heading && <h3 className="component-info__block-title">{block.title}</h3>}
    {block.note && <p className="component-info__block-note">{block.note}</p>}
    {block.entry ? <>
      <Fields fields={block.entry.fields} t={t} />
      {block.entry.notes && <p className="component-info__note-line">{block.entry.notes}</p>}
    </> : <p className="component-info__missing" data-missing>{block.missing || t("componentInfo.missing", { title: block.title })}</p>}
    {block.group && <div className="component-info__group" data-group={block.group.id} data-shared={block.group.shared || undefined}>
      <div className="component-info__group-head">
        <strong>{block.group.title}</strong>
        {block.group.shared && <span className="component-info__shared">{block.group.sharedBy !== null
          ? t("componentInfo.sharedBy", { count: block.group.sharedBy }) : t("componentInfo.shared")}</span>}
      </div>
      <Fields fields={block.group.fields} t={t} />
      {block.group.allocation && <>
        <p className="component-info__note-line">{t("componentInfo.allocation", { basis: block.group.allocation.basis })}</p>
        <Fields fields={block.group.allocation.fields} t={t} />
      </>}
      {block.group.note && <p className="component-info__note-line" data-group-note>{block.group.note}</p>}
    </div>}
  </Tag>;
}

function Technical({ card, t }: { card: ComponentCard; t: CardWords }) {
  return <details className="component-info__technical">
    <summary>{t("componentInfo.technical")}</summary>
    <dl className="component-info__fields component-info__fields--technical">
      <div className="component-info__field"><dt>{t("componentInfo.componentId")}</dt><dd className="mono">{card.componentId}</dd></div>
      {card.elementId && <div className="component-info__field"><dt>{t("componentInfo.elementId")}</dt><dd className="mono">{card.elementId}</dd></div>}
      <div className="component-info__field"><dt>{t("componentInfo.shown")}</dt>
        <dd className="mono">{card.shown ? <>{card.shown.projectId}<br />{card.shown.runId}<br />{card.shown.stateDigest}</> : t("componentInfo.shownNone")}</dd></div>
      {card.boardRevision && <div className="component-info__field"><dt>{t("componentInfo.boardRevision")}</dt><dd className="mono">{card.boardRevision}</dd></div>}
    </dl>
    {card.used.length > 0 && <>
      <h4>{t("componentInfo.datasets")}</h4>
      <ul className="component-info__list">{card.used.map((item) => <li key={item.id}>
        <strong>{item.title}</strong> <span className="mono">{item.id}</span> · {item.preparedAt}
        {item.appliesTo.versionLabel && <> · {item.appliesTo.versionLabel}</>}
      </li>)}</ul>
    </>}
    {card.sources.length > 0 && <>
      <h4>{t("componentInfo.sources")}</h4>
      <ul className="component-info__list">{card.sources.map((source, index) => <li key={`${source.datasetTitle}:${source.id}:${index}`}>
        {source.label}{source.date && <> · {source.date}</>}{source.kind && <span className="quiet"> · {source.kind}</span>}
        <span className="quiet"> · {source.datasetTitle}</span>
      </li>)}</ul>
    </>}
    {card.mismatched.length > 0 && <>
      <h4>{t("componentInfo.mismatchedDatasets")}</h4>
      <ul className="component-info__list" data-mismatched>{card.mismatched.map((item) => <li key={item.elementId}>
        <strong>{item.title}</strong> <span className="mono">{item.id}</span>
        {item.appliesTo.versionLabel && <> · {item.appliesTo.versionLabel}</>}
        <br /><span className="mono quiet">{item.appliesTo.projectId} · {item.appliesTo.runId} · {item.appliesTo.stateDigest}</span>
      </li>)}</ul>
    </>}
    {card.invalid.length > 0 && <p className="component-info__note-line">{t("componentInfo.invalidCards", { count: card.invalid.length })}</p>}
  </details>;
}

/** What the panel shows: a card, a pick still being identified, or an object no component answers for. */
export type ComponentInfoSubject =
  | { readonly kind: "pending" }
  | { readonly kind: "unresolved"; readonly label: string | null }
  | { readonly kind: "card"; readonly card: ComponentCard };

export function ComponentInfoPanel({ subject, onClose }: { subject: ComponentInfoSubject; onClose(): void }) {
  const t = useT();
  const card = subject.kind === "card" ? subject.card : null;
  const sentence = card ? stateSentence(card, t) : null;
  const text = useCallback(() => card ? componentCardText(card, t) : "", [card, t]);
  const name = card ? card.name : subject.kind === "unresolved" && subject.label ? subject.label : t("componentInfo.title");
  return <aside id="stage-component-info" className="stage-inspector component-info" role="region" aria-label={t("componentInfo.title")}
    data-state={card?.state ?? subject.kind}>
    <div className="component-info__head">
      <div className="component-info__heading">
        <span className="component-info__kicker">{t("componentInfo.title")}</span>
        <strong className="component-info__name" title={name}>{name}</strong>
      </div>
      <div className="component-info__actions">
        {card && <CopyButton text={text} label={t("componentInfo.copy")} done={t("componentInfo.copied")} />}
        <button type="button" className="btn btn--small" onClick={onClose}>{t("componentInfo.close")}</button>
      </div>
    </div>
    <div className="component-info__body">
      {subject.kind === "pending" && <p className="component-info__state" role="status">{t("componentInfo.state.pending")}</p>}
      {subject.kind === "unresolved" && <p className="component-info__state" role="status">{t("componentInfo.state.unresolved")}</p>}
      {card && sentence && <p className="component-info__state" role="status" data-card-state={card.state}>{sentence}</p>}
      {card?.state === "ready" && <>
        {card.summary.map((block) => <Block key={block.datasetId} block={block} heading={card.summary.length > 1} t={t} />)}
        {card.sections.map((block) => <Block key={block.datasetId} block={block} heading t={t} />)}
      </>}
      {card && <Technical card={card} t={t} />}
    </div>
  </aside>;
}
