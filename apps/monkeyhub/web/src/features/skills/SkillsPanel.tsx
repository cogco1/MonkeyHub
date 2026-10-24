/**
 * The project's skills (插件), and the one place a person grants a version what it asks for.
 *
 * A skill's code runs in the person's own environment with only what was
 * granted here: one run, or this exact version until revoked. The list shows
 * every skill's current version and whether it may run; granting opens that
 * version's code and the access it declares first, and what is posted is the
 * version and hash of exactly the code that was shown. Deny sends nothing.
 *
 * Below it, the skill library: the packages that come with the Hub and the
 * person's own, which one import adds to the project as a new version. That
 * version waits for a grant like any other.
 */

import { useEffect, useMemo, useState } from "react";

import { useConnection } from "../../api/project-runtime/ProjectRuntimeContext";
import {
  canApprove, createSkillsApi, packageKey, packageOffer, shortHash, skillFiles, skillStatus,
  type SkillDetail, type SkillIndex, type SkillPackage, type SkillRow, type SkillRunNeed, type SkillsApi, type SkillGrant,
} from "../../api/project-runtime/skills";
import { useT, type TFunction } from "../../i18n/useT";
import { AccessList, neededText } from "./SkillAccess";
import { initialSkillsState, REVIEW_CHOICES, skillsActions, type SkillReview, type SkillsActions, type SkillsState } from "./skillApproval";
import "./skills.css";

const STATUS_KEYS = {
  approved: "skills.status.approved",
  once: "skills.status.once",
  unapproved: "skills.status.unapproved",
  notRunnable: "skills.status.notRunnable",
} as const;

const GRANTED_KEYS = { once: "skills.granted.once", version: "skills.granted.version" } as const;

const SOURCE_KEYS = { bundled: "skills.library.source.bundled", user: "skills.library.source.user" } as const;

const size = (bytes: number) => bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KiB` : `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;

/** The panel for the project whose runtime provider encloses it. */
export function ProjectSkills({ close }: { close(): void }) {
  const connection = useConnection();
  const api = useMemo(() => createSkillsApi(connection), [connection]);
  return <SkillsPanel api={api} close={close} />;
}

export function SkillsPanel({ api, close }: { api: SkillsApi; close(): void }) {
  const [state, setState] = useState<SkillsState>(initialSkillsState);
  const actions = useMemo(() => skillsActions(api, setState), [api]);
  useEffect(() => { void actions.load(); void actions.loadLibrary(); }, [actions]);
  return <SkillsView state={state} actions={actions} close={close} />;
}

export function SkillsView({ state, actions, close }: { state: SkillsState; actions: SkillsActions; close(): void }) {
  const t = useT();
  return <section className="skills" aria-labelledby="skills-heading">
    <div className="chat-dialog__heading">
      <h2 id="skills-heading">{t("skills.title")}</h2>
      <button type="button" className="btn" onClick={close}>{t("skills.close")}</button>
    </div>
    {state.review
      ? <ReviewView review={state.review} projectId={state.index?.projectId ?? null} actions={actions} t={t} />
      : <ListView state={state} actions={actions} t={t} />}
  </section>;
}

function ListView({ state, actions, t }: { state: SkillsState; actions: SkillsActions; t: TFunction }) {
  const { index, grants, needs, loading, loadError, granted } = state;
  return <>
    <p className="chat-muted">{t("skills.intro")}</p>
    {granted && <p className="skills__notice" role="status">{t(GRANTED_KEYS[granted.scope], { name: granted.name, version: granted.version })}</p>}
    {loading && !index && <p className="chat-muted" role="status">{t("skills.loading")}</p>}
    {loadError && <div className="skills__error" role="alert">
      <p>{t("skills.loadFailed")} {loadError.detail}</p>
      <button type="button" className="btn" disabled={loading} onClick={() => void actions.load()}>{t("skills.retry")}</button>
    </div>}
    {index && <SkillList index={index} grants={grants} needs={needs} onApprove={(row) => void actions.review(row)} t={t} />}
    <Library state={state} actions={actions} t={t} />
  </>;
}

function Library({ state, actions, t }: { state: SkillsState; actions: SkillsActions; t: TFunction }) {
  const { library, libraryLoading, libraryError, imported } = state;
  return <section className="skills__library" aria-labelledby="skills-library-heading">
    <h3 id="skills-library-heading">{t("skills.library.title")}</h3>
    <p className="chat-muted">{t("skills.library.intro")}</p>
    {imported && <p className="skills__notice" role="status">{imported.added
      ? t("skills.library.imported", { name: imported.name, version: imported.version })
      : t("skills.library.unchanged", { name: imported.name, version: imported.version })}</p>}
    {libraryLoading && !library && <p className="chat-muted" role="status">{t("skills.library.loading")}</p>}
    {libraryError && <div className="skills__error" role="alert">
      <p>{t("skills.library.loadFailed")} {libraryError.detail}</p>
      <button type="button" className="btn" disabled={libraryLoading} onClick={() => void actions.loadLibrary()}>{t("skills.retry")}</button>
    </div>}
    {library && library.packages.length === 0 && <p className="chat-muted">{t("skills.library.empty", { folder: library.sources.user })}</p>}
    {library && library.packages.length > 0 && <ul className="skills__list">
      {library.packages.map((row) => <PackageRow key={packageKey(row)} row={row} projectId={library.projectId} state={state} actions={actions} t={t} />)}
    </ul>}
  </section>;
}

function PackageRow({ row, projectId, state, actions, t }: {
  row: SkillPackage; projectId: string; state: SkillsState; actions: SkillsActions; t: TFunction;
}) {
  const offer = packageOffer(row);
  const key = packageKey(row);
  const name = row.skillName ?? row.name;
  const error = state.importError?.key === key ? state.importError.error : null;
  return <li className="skills__row skills__package" data-offer={offer}>
    <div className="skills__head">
      <strong className="skills__name">{name}</strong>
      <span className="skills__source">{t(SOURCE_KEYS[row.source])}</span>
      {offer === "upToDate" && <span className="skills__status" data-status="upToDate">{t("skills.library.upToDate")}</span>}
    </div>
    {row.description && <p className="skills__description">{row.description}</p>}
    {/* A package that cannot be read says what is wrong with its folder, and offers nothing. */}
    {row.error && <p className="skills__error">{t("skills.library.broken", { detail: row.error.detail })}</p>}
    {offer !== "broken" && <dl className="skills__facts">
      <dt>{t("skills.library.folder")}</dt><dd><code title={row.path}>{row.name}</code></dd>
      <dt>{t("skills.library.inProject")}</dt>
      <dd>{row.projectVersion == null ? t("skills.library.notInProject") : t("skills.version", { version: row.projectVersion })}</dd>
      <dt>{t("skills.library.mayDo")}</dt><dd><AccessList access={row.access} t={t} /></dd>
    </dl>}
    {error && <p className="skills__error" role="alert">{error.detail}</p>}
    {offer !== "broken" && <div className="skills__foot skills__package-foot">
      <button type="button" className="btn" disabled={offer === "upToDate" || state.importing !== null}
        aria-label={t(offer === "import" ? "skills.library.importAria" : "skills.library.updateAria", { name })}
        onClick={() => void actions.importPackage(projectId, row)}>
        {state.importing === key ? t("skills.library.importing") : t(offer === "import" ? "skills.library.import" : "skills.library.update")}</button>
    </div>}
  </li>;
}

function SkillList({ index, grants, needs, onApprove, t }: {
  index: SkillIndex; grants: readonly SkillGrant[] | null; needs: Readonly<Record<string, readonly SkillRunNeed[]>>;
  onApprove(row: SkillRow): void; t: TFunction;
}) {
  if (index.skills.length === 0) return <p className="chat-muted">{t("skills.empty")}</p>;
  const unreported = index.skills.every((row) => skillStatus(row, grants) === "unreported");
  return <>
    {unreported && <p className="chat-muted">{t("skills.unreported")}</p>}
    <ul className="skills__list">
      {index.skills.map((row) => {
        const status = skillStatus(row, grants);
        const needed = needs[row.id];
        return <li key={row.id} className="skills__row" data-status={status}>
          <div className="skills__head">
            <strong className="skills__name">{row.name}</strong>
            <span className="skills__version">{t("skills.version", { version: row.version })}</span>
            {status !== "unreported" && <span className="skills__status" data-status={status}
              title={status === "notRunnable" ? t("skills.status.notRunnableHint") : undefined}>{t(STATUS_KEYS[status])}</span>}
          </div>
          {row.description && <p className="skills__description">{row.description}</p>}
          {needed && <p className="skills__needed">{neededText(needed, t)}</p>}
          <div className="skills__foot">
            {row.codeSha256
              ? <span className="skills__hash">{t("skills.hash")} <code title={row.codeSha256}>{shortHash(row.codeSha256)}</code></span>
              : row.codeSha256 === null ? <span className="skills__hash">{t("skills.noCode")}</span> : null}
            {canApprove(row, grants) && <button type="button" className="chat-activity__open skills__approve"
              aria-label={t("skills.approveAria", { name: row.name, version: row.version })} onClick={() => onApprove(row)}>{t("skills.approve")}</button>}
          </div>
        </li>;
      })}
    </ul>
  </>;
}

function ReviewView({ review, projectId, actions, t }: { review: SkillReview; projectId: string | null; actions: SkillsActions; t: TFunction }) {
  const { row, detail, busy, error } = review;
  const blocked = busy || !detail?.codeSha256 || projectId === null;
  return <div className="skills__review">
    <h3>{t("skills.confirm.title", { name: detail?.name ?? row.name, version: detail?.version ?? row.version })}</h3>
    {!detail && busy && <p className="chat-muted" role="status">{t("skills.confirm.reading")}</p>}
    {detail && <ReviewDetail detail={detail} t={t} />}
    {/* What it asks to use, directly above the answers. */}
    {detail && <><h4>{t("skills.access.heading")}</h4><AccessList access={detail.access} t={t} /></>}
    {error && <p className="skills__error" role="alert">{error.detail}</p>}
    {detail && <p className="chat-muted skills__scopes">{t("skills.confirm.scopes")}</p>}
    <div className="chat-dialog__actions skills__choices">
      {REVIEW_CHOICES.map(({ choice, label }) => <button key={choice} type="button" data-choice={choice}
        className={choice === "once" ? "btn btn--primary" : "btn"} disabled={choice === "deny" ? busy && detail !== null : blocked}
        onClick={() => void actions.answer(choice, projectId, detail)}>{choice === "deny" && !detail ? t("skills.confirm.back") : t(label)}</button>)}
    </div>
    {busy && detail && <p className="chat-muted" role="status">{t("skills.confirm.submitting")}</p>}
  </div>;
}

function ReviewDetail({ detail, t }: { detail: SkillDetail; t: TFunction }) {
  const files = skillFiles(detail);
  return <>
    <p className="chat-muted">{t("skills.confirm.intro")}</p>
    <dl className="skills__facts">
      <dt>{t("skills.confirm.name")}</dt><dd>{detail.name}</dd>
      <dt>{t("skills.confirm.version")}</dt><dd>{detail.version}</dd>
      <dt>{t("skills.confirm.hash")}</dt><dd>{detail.codeSha256 ? <code>{detail.codeSha256}</code> : t("skills.confirm.noHash")}</dd>
      <dt>{t("skills.confirm.entry")}</dt><dd>{detail.entry ? <code>{detail.entry}</code> : t("skills.confirm.noEntry")}</dd>
    </dl>
    <h4>{t("skills.confirm.files", { n: files.length })}</h4>
    {files.length === 0 ? <p className="chat-muted">{t("skills.confirm.noFiles")}</p>
      : <ul className="skills__files">
        {files.map((file) => <li key={file.path}>
          <details>
            <summary><code>{file.path}</code>{file.entry && <span className="skills__entry">{t("skills.confirm.entryMark")}</span>}
              <span className="skills__size">{size(file.bytes)}</span></summary>
            <pre className="skills__code" tabIndex={0}>{file.text}</pre>
          </details>
        </li>)}
      </ul>}
  </>;
}
