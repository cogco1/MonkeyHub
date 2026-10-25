/**
 * The Hub-wide choice of how a chat's AI runs a skill (`skillAgentRuns` in the
 * Hub's user settings), and the only things the page does with it: read it,
 * and save a new choice. "ask" (the default) puts a version no one always
 * allowed to the person in the chat; "granted" runs only always-allowed
 * versions and asks nothing; "never" is "granted", and the AI starts no skill
 * that declares access at all.
 *
 * Hand-typed until the generated SDK carries the field. The Hub leaves it out
 * until a person sets it, and unset reads as "ask"; only a person's choice
 * writes it, "ask" included. A save reads what the Hub holds and writes it back
 * with only this field changed, one write at a time, each carrying the newest
 * choice, as the projects list's pin does. Kept apart from the view so each
 * step can be exercised without a DOM.
 */

import { createClient } from "../../api/generated/client";
import { getUserSettingsApiSettingsUserGet, putUserSettingsApiSettingsUserPut, type HubError, type UserSettingsDto } from "../../api/generated";
import type { MessageKey } from "../../i18n/messages.en";

export type SkillAgentRuns = "ask" | "granted" | "never";

/** The saved user settings, with the field the generated SDK does not carry yet; absent or null is unset. */
export type UserSettings = UserSettingsDto & { skillAgentRuns?: SkillAgentRuns | null };

export const DEFAULT_SKILL_AGENT_RUNS: SkillAgentRuns = "ask";

/** The three choices, in the order they are offered, each with its one-line explanation. */
export const SKILL_AGENT_RUN_CHOICES = [
  { value: "ask", label: "skills.agentRuns.ask", hint: "skills.agentRuns.askHint" },
  { value: "granted", label: "skills.agentRuns.granted", hint: "skills.agentRuns.grantedHint" },
  { value: "never", label: "skills.agentRuns.never", hint: "skills.agentRuns.neverHint" },
] as const satisfies readonly { value: SkillAgentRuns; label: MessageKey; hint: MessageKey }[];

export interface HubSettingsApi {
  read(): Promise<UserSettings>;
  /** PUT replaces every saved preference, so it is sent the whole object. */
  write(settings: UserSettings): Promise<UserSettings>;
}

/** A refusal as `{code, detail}`; a validation answer's list of problems is joined into one detail. */
function failure(result: { response?: Response; error?: unknown }): HubError {
  const error = result.error as { code?: unknown; detail?: unknown } | undefined;
  const detail = typeof error?.detail === "string" ? error.detail : Array.isArray(error?.detail)
    ? error.detail.map((item: { loc?: unknown[]; msg?: string }) => `${item.loc?.join(".") ?? ""}: ${item.msg ?? "Invalid value"}`).join("; ")
    : `HTTP ${result.response?.status ?? 0}`;
  return { code: typeof error?.code === "string" ? error.code : `HTTP_${result.response?.status ?? 0}`, detail };
}

async function settingsOf(request: Promise<unknown>): Promise<UserSettings> {
  const result = await request as { response?: Response; data?: UserSettings; error?: unknown };
  if (!result.response?.ok || result.error !== undefined || result.data === undefined) throw failure(result);
  return result.data;
}

/** The Hub's own settings routes, at the Hub that served this page unless told otherwise. */
export function createHubSettingsApi(baseUrl: string): HubSettingsApi {
  const client = createClient({ baseUrl });
  return {
    read: () => settingsOf(getUserSettingsApiSettingsUserGet({ client })),
    write: (body) => settingsOf(putUserSettingsApiSettingsUserPut({ client, body })),
  };
}

export const asHubError = (cause: unknown): HubError =>
  cause && typeof cause === "object" && typeof (cause as HubError).code === "string" && typeof (cause as HubError).detail === "string"
    ? { code: (cause as HubError).code, detail: (cause as HubError).detail }
    : { code: "NETWORK_ERROR", detail: cause instanceof Error ? cause.message : "The Hub did not answer." };

/** What the settings say, "ask" when they leave it unset; a value this page does not know is null. */
export function agentRunsOf(settings: UserSettings): SkillAgentRuns | null {
  const value: unknown = settings.skillAgentRuns ?? DEFAULT_SKILL_AGENT_RUNS;
  return SKILL_AGENT_RUN_CHOICES.some((choice) => choice.value === value) ? value as SkillAgentRuns : null;
}

export interface RunSettingState {
  /** What the Hub holds; null until read, or a value this page does not know. */
  readonly saved: SkillAgentRuns | null;
  /** The choice on screen: the one being saved, otherwise the saved one. */
  readonly shown: SkillAgentRuns | null;
  readonly loading: boolean;
  readonly saving: boolean;
  readonly loadError: HubError | null;
  readonly saveError: HubError | null;
}

export const initialRunSettingState: RunSettingState = {
  saved: null, shown: null, loading: true, saving: false, loadError: null, saveError: null,
};

type SetState = (update: (state: RunSettingState) => RunSettingState) => void;

export interface RunSettingActions {
  load(): Promise<void>;
  /** Save this choice; a failure puts the saved one back on screen and says why. */
  choose(value: SkillAgentRuns): Promise<void>;
}

/** S23: whether a script that declares nothing runs without asking (`lowRiskScriptsRun`); unset is on, only off is written. */
export interface LowRiskState {
  readonly on: boolean | null;
  readonly saving: boolean;
  readonly error: HubError | null;
}

export const initialLowRiskState: LowRiskState = { on: null, saving: false, error: null };

export const lowRiskOf = (settings: UserSettings): boolean => settings.lowRiskScriptsRun !== false;

export function lowRiskActions(api: HubSettingsApi, setState: (update: (state: LowRiskState) => LowRiskState) => void) {
  return {
    async load() {
      try {
        const on = lowRiskOf(await api.read());
        setState((state) => ({ ...state, on, error: null }));
      } catch (cause) {
        setState((state) => ({ ...state, error: asHubError(cause) }));
      }
    },
    async choose(on: boolean) {
      setState((state) => ({ ...state, on, saving: true, error: null }));
      try {
        // Every other preference goes back exactly as the Hub holds it now; on is written as unset.
        const current = await api.read();
        const saved = lowRiskOf(current) === on ? current : await api.write({ ...current, lowRiskScriptsRun: on ? null : false });
        setState((state) => ({ ...state, on: lowRiskOf(saved), saving: false }));
      } catch (cause) {
        setState((state) => ({ ...state, on: !on, saving: false, error: asHubError(cause) }));
      }
    },
  };
}

export function runSettingActions(api: HubSettingsApi, setState: SetState): RunSettingActions {
  const job = { wanted: DEFAULT_SKILL_AGENT_RUNS, running: false };
  return {
    async load() {
      setState((state) => ({ ...state, loading: true, loadError: null }));
      try {
        const saved = agentRunsOf(await api.read());
        // A choice made before this answer stays on screen while it is saved.
        setState((state) => ({ ...state, saved, shown: state.saving ? state.shown : saved, loading: false }));
      } catch (cause) {
        setState((state) => ({ ...state, loading: false, loadError: asHubError(cause) }));
      }
    },
    async choose(value) {
      job.wanted = value;
      setState((state) => ({ ...state, shown: value, saving: true, saveError: null }));
      if (job.running) return;
      job.running = true;
      try {
        for (let written: SkillAgentRuns | null = null; written !== job.wanted;) {
          const wanted: SkillAgentRuns = job.wanted;
          // Every other preference goes back exactly as the Hub holds it now. The choice is
          // written as picked even when it is "ask" and the Hub holds it unset.
          const current = await api.read();
          const saved = current.skillAgentRuns === wanted ? current : await api.write({ ...current, skillAgentRuns: wanted });
          written = wanted;
          setState((state) => ({ ...state, saved: agentRunsOf(saved), loadError: null }));
        }
        setState((state) => ({ ...state, saving: false, shown: state.saved }));
      } catch (cause) {
        setState((state) => ({ ...state, saving: false, shown: state.saved, saveError: asHubError(cause) }));
      } finally {
        job.running = false;
      }
    },
  };
}
