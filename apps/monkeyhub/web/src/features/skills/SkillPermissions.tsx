/**
 * Settings › What the AI may do (S-1): the Hub-wide choice of how a chat's AI
 * runs a skill, the skills that cannot be used right now (which, what is wrong,
 * who can fix it, in plain words), then everything a person allowed skills to
 * do on this computer, for which skills, how and when, and Withdraw on what
 * still holds. The grants
 * are the Hub's own, so the page shows without a project and while a project is
 * busy. Only this page withdraws a grant; a chat's question gives one.
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";

import { useT, type TFunction } from "../../i18n/useT";
import { usePreferences } from "../settings/preferences";
import { AccessList } from "./SkillAccess";
import {
  createSkillGrantsApi, grantsActions, grantStatus, initialGrantsState,
  type GrantsActions, type GrantsState, type SkillGrant, type SkillGrantsApi, type SkillProblem, type ToolGrant,
} from "./skillGrants";
import {
  createHubSettingsApi, initialRunSettingState, runSettingActions, SKILL_AGENT_RUN_CHOICES,
  type HubSettingsApi, type RunSettingActions, type RunSettingState,
} from "./skillRunSetting";
import "./skills.css";
import { AutoRetry } from "../../app/AutoRetry";

const STATUS_KEYS = { active: "skills.grants.status.active", used: "skills.grants.status.used", revoked: "skills.grants.status.revoked" } as const;
const PROBLEM_KEYS = {
  entry_syntax: "skills.problems.kind.entry_syntax", missing_file: "skills.problems.kind.missing_file",
  name_differs: "skills.problems.kind.name_differs", unreadable: "skills.problems.kind.unreadable",
} as const;
const TOOL_KEYS = { run: "skills.tools.run", network: "skills.tools.network", hub: "skills.tools.hub" } as const;
const WHO_KEYS = { user: "skills.problems.who.user", bundled: "skills.problems.who.bundled" } as const;
const METHOD_KEYS = { card: "skills.grants.method.card", settings: "skills.grants.method.settings", migrated: "skills.grants.method.migrated" } as const;

/** The page as Settings shows it, reading the Hub at this window's origin unless given an API. */
export function SkillGrants({ api, settings }: { api?: SkillGrantsApi; settings?: HubSettingsApi }) {
  const grantsApi = useMemo(() => api ?? createSkillGrantsApi(window.location.origin), [api]);
  const [state, setState] = useState<GrantsState>(initialGrantsState);
  const actions = useMemo(() => grantsActions(grantsApi, setState), [grantsApi]);
  useEffect(() => { void actions.load(); }, [actions]);
  return <SkillGrantsView state={state} actions={actions} runChoice={<SkillRunChoice settings={settings} />} />;
}

/** The Hub-wide choice, read each time it is shown and saved on each change. */
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
      <AutoRetry failure={loadError} place="skills_agent_runs" className="btn" retry={() => void actions.load()}>{t("skills.retry")}</AutoRetry>
    </div>}
    {saveError && <p className="skills__error" role="alert">{t("skills.agentRuns.saveFailed")} {saveError.detail}</p>}
  </fieldset>;
}

export function SkillGrantsView({ state, actions, runChoice }: {
  state: GrantsState; actions: GrantsActions;
  /** The Hub-wide choice, shown under the title, above the grants. */
  runChoice?: ReactNode;
}) {
  const t = useT();
  const { language } = usePreferences();
  const { grants, problems, toolGrants, loading, loadError } = state;
  const when = (value: string) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value
      : date.toLocaleString(language, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };
  return <div className="skills skill-grants">
    <h2>{t("skills.grants.title")}</h2>
    {runChoice}
    {problems && problems.length > 0 && <SkillProblems problems={problems} t={t} />}
    <p className="settings-intro">{t("skills.grants.intro")}</p>
    {loading && !grants && <p className="chat-muted wait-late" role="status">{t("skills.grants.loading")}</p>}
    {loadError && <div className="skills__error" role="alert">
      <p>{t("skills.grants.loadFailed")} {loadError.detail}</p>
      <AutoRetry failure={loadError} place="skills_grants" className="btn" disabled={loading} retry={() => void actions.load()}>{t("skills.retry")}</AutoRetry>
    </div>}
    {grants && grants.length === 0 && (!toolGrants || toolGrants.length === 0) && <p className="chat-muted">{t("skills.grants.empty")}</p>}
    {toolGrants && toolGrants.length > 0 && <section className="skills__tools" aria-labelledby="skills-tools-heading">
      <h3 id="skills-tools-heading">{t("skills.tools.title")}</h3>
      <p className="chat-muted">{t("skills.tools.intro")}</p>
      <ul className="skills__list">
        {toolGrants.map((grant) => <ToolGrantRow key={grant.grantId} grant={grant} state={state} actions={actions} when={when} t={t} />)}
      </ul>
    </section>}
    {grants && grants.length > 0 && <ul className="skills__list">
      {grants.map((grant) => <GrantRow key={grant.grantId} grant={grant} state={state} actions={actions} when={when} t={t} />)}
    </ul>}
  </div>;
}

/** Which skills cannot be used and why, in a sentence each, then who can fix it; the technical line sits under it. */
function SkillProblems({ problems, t }: { problems: readonly SkillProblem[]; t: TFunction }) {
  return <section className="skills__problems" role="alert" aria-labelledby="skills-problems-heading">
    <h3 id="skills-problems-heading">{t("skills.problems.title", { count: problems.length })}</h3>
    <p className="chat-muted">{t("skills.problems.intro")}</p>
    <ul className="skills__list">
      {problems.map((problem) => <li key={`${problem.source}/${problem.name}`} className="skills__row" data-problem={problem.kind}>
        <p>{t(PROBLEM_KEYS[problem.kind], { name: problem.name, file: problem.file ?? "", line: problem.line ?? 0 })}</p>
        <p>{t(WHO_KEYS[problem.source], { folder: problem.folder })}</p>
        <p className="chat-muted"><code>{problem.detail}</code></p>
      </li>)}
    </ul>
  </section>;
}

/** The one Withdraw control both lists use: what it withdraws is named for assistive technology. */
function WithdrawButton({ grantId, scope, revoking, onWithdraw, t }: {
  grantId: string; scope: string; revoking: string | null; onWithdraw(grantId: string): void; t: TFunction;
}) {
  return <div className="skills__foot skill-grants__foot">
    <button type="button" className="btn" disabled={revoking !== null} aria-label={t("skills.grants.revokeAria", { scope })}
      onClick={() => onWithdraw(grantId)}>
      {revoking === grantId ? t("skills.grants.revoking") : t("skills.grants.revoke")}</button>
  </div>;
}

function ToolGrantRow({ grant, state, actions, when, t }: {
  grant: ToolGrant; state: GrantsState; actions: GrantsActions; when(value: string): string; t: TFunction;
}) {
  const { revoking, revokeError } = state;
  const status = grant.revokedAt ? "revoked" : "active";
  const error = revokeError?.grantId === grant.grantId ? revokeError.error : null;
  const what = t(TOOL_KEYS[grant.action], { project: grant.projectId });
  return <li className="skills__row" data-status={status}>
    <div className="skills__head">
      <strong className="skills__name">{what}</strong>
      <span className="skills__status" data-status={status}>{t(STATUS_KEYS[status])}</span>
    </div>
    <dl className="skills__facts">
      <dt>{t("skills.grants.at")}</dt><dd>{when(grant.grantedAt)}</dd>
      {grant.revokedAt && <><dt>{t("skills.grants.revokedAt")}</dt><dd>{when(grant.revokedAt)}</dd></>}
    </dl>
    {error && <p className="skills__error" role="alert">{error.detail}</p>}
    {status === "active" && <WithdrawButton grantId={grant.grantId} scope={what} revoking={revoking} onWithdraw={(id) => void actions.revokeTool(id)} t={t} />}
  </li>;
}

function scopeText(grant: SkillGrant, t: TFunction): string {
  if (!grant.skill) return t("skills.grants.for.every");
  const name = grant.skill.name;
  return grant.once ? t("skills.grants.for.once", { name }) : t("skills.grants.for.skill", { name });
}

function GrantRow({ grant, state, actions, when, t }: {
  grant: SkillGrant; state: GrantsState; actions: GrantsActions; when(value: string): string; t: TFunction;
}) {
  const { revoking, revokeError } = state;
  const status = grantStatus(grant);
  const error = revokeError?.grantId === grant.grantId ? revokeError.error : null;
  const scope = scopeText(grant, t);
  return <li className="skills__row" data-status={status}>
    <div className="skills__head">
      <strong className="skills__name">{scope}</strong>
      <span className="skills__status" data-status={status}>{t(STATUS_KEYS[status])}</span>
    </div>
    <dl className="skills__facts">
      <dt>{t("skills.grants.allows")}</dt><dd><AccessList access={grant.access} t={t} declaredOnly /></dd>
      <dt>{t("skills.grants.by")}</dt><dd>{t(METHOD_KEYS[grant.method])}</dd>
      <dt>{t("skills.grants.at")}</dt><dd>{when(grant.grantedAt)}</dd>
      {grant.revokedAt && <><dt>{t("skills.grants.revokedAt")}</dt><dd>{when(grant.revokedAt)}</dd></>}
    </dl>
    {error && <p className="skills__error" role="alert">{error.detail}</p>}
    {status === "active" && <WithdrawButton grantId={grant.grantId} scope={scope} revoking={revoking} onWithdraw={(id) => void actions.revoke(id)} t={t} />}
  </li>;
}
