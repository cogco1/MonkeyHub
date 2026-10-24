/**
 * Settings › Skill permissions: the Hub-wide choice of how a chat's AI runs a
 * skill, then every grant of the selected project, what each allows, who gave
 * it and when, whether it still holds, and Revoke on the active ones. Only this
 * page and the Skills panel send these requests.
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";

import { useConnection } from "../../api/project-runtime/ProjectRuntimeContext";
import { createSkillsApi, shortHash, type SkillGrant, type SkillsApi } from "../../api/project-runtime/skills";
import { useT, type TFunction } from "../../i18n/useT";
import { usePreferences } from "../settings/preferences";
import { AccessList } from "./SkillAccess";
import { grantsActions, initialGrantsState, type GrantsActions, type GrantsState } from "./skillGrants";
import {
  createHubSettingsApi, initialRunSettingState, runSettingActions, SKILL_AGENT_RUN_CHOICES,
  type HubSettingsApi, type RunSettingActions, type RunSettingState,
} from "./skillRunSetting";
import "./skills.css";

const SCOPE_KEYS = { once: "skills.scope.once", version: "skills.scope.version" } as const;
const STATUS_KEYS = { active: "skills.grants.status.active", used: "skills.grants.status.used", revoked: "skills.grants.status.revoked" } as const;

/** The page for the project whose runtime provider encloses it. */
export function ProjectSkillGrants({ projectName }: { projectName: string | null }) {
  const connection = useConnection();
  const api = useMemo(() => createSkillsApi(connection), [connection]);
  return <SkillGrantsPanel api={api} projectName={projectName} />;
}

export function SkillGrantsPanel({ api, settings, projectName }: { api: SkillsApi; settings?: HubSettingsApi; projectName: string | null }) {
  const [state, setState] = useState<GrantsState>(initialGrantsState);
  const actions = useMemo(() => grantsActions(api, setState), [api]);
  useEffect(() => { void actions.load(); }, [actions]);
  return <SkillGrantsView state={state} actions={actions} projectName={projectName} runChoice={<SkillRunChoice settings={settings} />} />;
}

/** The Hub-wide choice, read each time it is shown and saved on each change; it shows with or without a project. */
export function SkillRunChoice({ settings }: { settings?: HubSettingsApi }) {
  const api = useMemo(() => settings ?? createHubSettingsApi(window.location.origin), [settings]);
  const [state, setState] = useState<RunSettingState>(initialRunSettingState);
  const actions = useMemo(() => runSettingActions(api, setState), [api]);
  useEffect(() => { void actions.load(); }, [actions]);
  return <SkillRunChoiceView state={state} actions={actions} />;
}

export function SkillRunChoiceView({ state, actions }: { state: RunSettingState; actions: RunSettingActions }) {
  const t = useT();
  const { shown, saved, loading, saving, loadError, saveError } = state;
  return <fieldset className="skills__agent-runs" disabled={loading && saved === null}>
    <legend>{t("skills.agentRuns.title")}</legend>
    <p className="chat-muted">{t("skills.agentRuns.intro")}</p>
    {SKILL_AGENT_RUN_CHOICES.map(({ value, label, hint }) => <label key={value} className="skills__agent-run">
      <input type="radio" name="skill-agent-runs" value={value} checked={shown === value} onChange={() => void actions.choose(value)} />
      <span><span className="skills__agent-run-label">{t(label)}</span><span className="skills__agent-run-hint">{t(hint)}</span></span>
    </label>)}
    {saving && <p className="chat-muted" role="status">{t("skills.agentRuns.saving")}</p>}
    {loadError && <div className="skills__error" role="alert">
      <p>{t("skills.agentRuns.loadFailed")} {loadError.detail}</p>
      <button type="button" className="btn" onClick={() => void actions.load()}>{t("skills.retry")}</button>
    </div>}
    {saveError && <p className="skills__error" role="alert">{t("skills.agentRuns.saveFailed")} {saveError.detail}</p>}
  </fieldset>;
}

export function SkillGrantsView({ state, actions, projectName, runChoice }: {
  state: GrantsState; actions: GrantsActions; projectName: string | null;
  /** The Hub-wide choice, shown under the title, above the project's grants. */
  runChoice?: ReactNode;
}) {
  const t = useT();
  const { language } = usePreferences();
  const { projectId, grants, names, loading, loadError } = state;
  const when = (value: string) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value
      : date.toLocaleString(language, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };
  return <div className="skills skill-grants">
    <h2>{t("skills.grants.title")}</h2>
    {runChoice}
    {(projectName ?? projectId) && <p className="settings-intro">{t("skills.grants.intro", { project: projectName ?? projectId ?? "" })}</p>}
    {loading && !grants && <p className="chat-muted" role="status">{t("skills.grants.loading")}</p>}
    {loadError && <div className="skills__error" role="alert">
      <p>{t("skills.grants.loadFailed")} {loadError.detail}</p>
      <button type="button" className="btn" disabled={loading} onClick={() => void actions.load()}>{t("skills.retry")}</button>
    </div>}
    {grants && grants.length === 0 && <p className="chat-muted">{t("skills.grants.empty")}</p>}
    {grants && grants.length > 0 && <ul className="skills__list">
      {grants.map((grant) => <GrantRow key={grant.approvalId} grant={grant} name={names[grant.skillId] ?? grant.skillId}
        state={state} actions={actions} when={when} t={t} />)}
    </ul>}
  </div>;
}

function GrantRow({ grant, name, state, actions, when, t }: {
  grant: SkillGrant; name: string; state: GrantsState; actions: GrantsActions; when(value: string): string; t: TFunction;
}) {
  const { projectId, revoking, revokeError } = state;
  const by = grant.approvedBy;
  const error = revokeError?.approvalId === grant.approvalId ? revokeError.error : null;
  // A status or scope this page does not know is shown as the runtime wrote it.
  const status = STATUS_KEYS[grant.status as keyof typeof STATUS_KEYS], scope = SCOPE_KEYS[grant.scope as keyof typeof SCOPE_KEYS];
  return <li className="skills__row" data-status={grant.status}>
    <div className="skills__head">
      <strong className="skills__name">{name}</strong>
      <span className="skills__version">{t("skills.version", { version: grant.version })}</span>
      <span className="skills__status" data-status={grant.status}>{status ? t(status) : grant.status}</span>
    </div>
    <dl className="skills__facts">
      <dt>{t("skills.grants.hash")}</dt><dd><code title={grant.codeSha256}>{shortHash(grant.codeSha256)}</code></dd>
      <dt>{t("skills.grants.scope")}</dt><dd>{scope ? t(scope) : grant.scope}</dd>
      <dt>{t("skills.grants.allows")}</dt><dd><AccessList access={grant.access} t={t} /></dd>
      <dt>{t("skills.grants.by")}</dt><dd>{[by.actorId, by.origin].filter(Boolean).join(" · ")}{by.authenticated === false ? t("skills.grants.unauthenticated") : ""}</dd>
      <dt>{t("skills.grants.at")}</dt><dd>{when(grant.approvedAt)}</dd>
      {grant.revokedAt && <><dt>{t("skills.grants.revokedAt")}</dt><dd>{when(grant.revokedAt)}</dd></>}
    </dl>
    {error && <p className="skills__error" role="alert">{error.detail}</p>}
    {grant.status === "active" && <div className="skills__foot skill-grants__foot">
      <button type="button" className="btn" disabled={revoking !== null || projectId === null}
        aria-label={t("skills.grants.revokeAria", { name, version: grant.version })}
        onClick={() => { if (projectId !== null) void actions.revoke(projectId, grant.approvalId); }}>
        {revoking === grant.approvalId ? t("skills.grants.revoking") : t("skills.grants.revoke")}</button>
    </div>}
  </li>;
}
