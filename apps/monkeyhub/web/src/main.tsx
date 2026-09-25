import { useT } from "../workspaces/src/i18n/useT";
import { UserPreferencesProvider, usePreferences } from "../workspaces/src/features/settings/preferences";
import { StrictMode, useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { appearanceFromSearch, applyAppearance, DEFAULT_APPEARANCE, resolveAppearance, type AppearancePreferences, type Language } from "../../../shared-web/src/appearance.js";
import { translateMessage } from "../../../shared-web/src/i18n.js";
import { prepareEnglishToChinese, translateEnglishToChinese } from "../../../shared-web/src/browserTranslator.js";
import { createClient } from "./api/generated/client";
import { applicationSettingsApiSettingsAppsGet, chatProvidersApiChatProvidersGet, chatWorkspaceApiChatWorkspaceGet, getUserSettingsApiSettingsUserGet, listAppsApiAppsGet, putUserSettingsApiSettingsUserPut, startAppApiAppsAppIdStartPost, stopAppApiAppsAppIdStopPost, updateApplicationSettingsApiSettingsAppsPut, type AppStatus, type ApplicationSettingsDto, type ChatProvider, type ChatWorkspace, type UserSettingsDto } from "./api/generated";
import "./styles.css";
import { FabPage } from "./FabPage";
import { ChatShell } from "./ChatShell";

type AppId = AppStatus["appId"];
type Issue = { code: string; detail: string };
type FieldIssue = Issue & { field: "workspace-dir" | "studio-port" | "render-timeout" };
const hubClient = createClient({ baseUrl: window.location.origin });
const initialLaunch: ApplicationSettingsDto = { projectDir: null, referenceRun: null, cadExport: "occt", studioPort: 8789, monitorPort: 8788 };
type ChatDefaults = { chatProvider: UserSettingsDto["chatProvider"]; chatModel: string | null };
const NO_CHAT_DEFAULTS: ChatDefaults = { chatProvider: null, chatModel: null };
type RenderDefaults = Pick<UserSettingsDto, "renderProvider" | "renderModel" | "renderTimeoutS">;
const renderDefaults = (settings?: UserSettingsDto): RenderDefaults => ({ renderProvider: settings?.renderProvider ?? "off",
  renderModel: settings?.renderModel ?? null, renderTimeoutS: settings?.renderTimeoutS ?? null });
import { hubCopyCatalog as copy } from "./i18n/catalogs";
type CopyKey = keyof typeof copy.en;
const knownErrors: Record<string, string> = {
  PROJECT_REQUIRED: "请先选择包含 project.json 的完整项目目录。", APPS_RUNNING: "请先停止正在运行的工作区，再保存启动设置。",
  PORT_CONFLICT: "Hub、Studio 和 Monitor 需要使用不同端口。", PORT_IN_USE: "所选端口已被占用，请选择其他端口。",
  STUDIO_WEB_MISSING: "缺少 Studio 网页文件，请重新准备完整版本包。", SOURCE_VERSION_UNKNOWN: "无法确认此目录的源版本，请使用完整版本包。",
  SERVICE_IDENTITY_MISMATCH: "响应的服务与本次启动或所选项目不一致。", START_TIMEOUT: "工作区未能在等待时间内就绪，请查看详细信息。",
  PROCESS_EXITED: "工作区进程已退出，请查看详细信息。", START_FAILED: "工作区启动失败，请查看详细信息。", APP_UNAVAILABLE: "此版本尚未提供该工作区。",
  HUB_STOPPING: "Hub 正在等待工作区结束，请稍候。", SETTINGS_UNAVAILABLE: "本地设置暂时无法读取或保存。",
  APP_SETTINGS_INVALID: "保存的启动设置无效，请检查项目目录和端口。", LOCAL_IO_FAILED: "无法访问本地设置或运行记录目录。",
  FAB_UNAVAILABLE: "此运行环境未包含 MonkeyFab，请使用整合安装包。", APP_HOSTED_BY_HUB: "MonkeyFab 使用 Hub 页面，无需单独停止。",
  WORKSPACE_DIR_INVALID: "新项目所在文件夹需要填写完整路径，例如 D:\\Projects。", STUDIO_PORT_INVALID: "项目服务端口需为 1024–65535 之间的整数。",
  RENDER_TIMEOUT_INVALID: "渲染请求超时需在 1–300 秒之间。",
  VALIDATION_ERROR: "请检查设置格式，项目目录必须为绝对路径，端口需在 1024–65535 之间。", NETWORK_ERROR: "无法连接本地 Hub 服务，请确认它仍在运行。",
};
function issueOf(cause: unknown): Issue {
  if (cause && typeof cause === "object" && "code" in cause && "detail" in cause) return cause as Issue;
  return { code: "NETWORK_ERROR", detail: cause instanceof Error ? cause.message : "The Hub request did not complete." };
}
async function responseData<T>(request: Promise<unknown>): Promise<T> {
  const result = await request as { response?: Response; data?: T; error?: unknown };
  if (!result.response?.ok || result.error !== undefined || result.data === undefined) {
    const error = result.error as { code?: unknown; detail?: unknown } | undefined;
    const detail = typeof error?.detail === "string" ? error.detail : Array.isArray(error?.detail)
      ? error.detail.map((item: { loc?: unknown[]; msg?: string }) => `${item.loc?.join(".") ?? ""}: ${item.msg ?? "Invalid value"}`).join("; ")
      : `HTTP ${result.response?.status ?? 0}`;
    throw { code: typeof error?.code === "string" ? error.code : result.response?.status === 422 ? "VALIDATION_ERROR" : "HTTP_ERROR", detail } satisfies Issue;
  }
  return result.data;
}
function ErrorMessage({ issue, language }: { issue: Issue | null; language: Language }) {
  const [translated, setTranslated] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false; setTranslated(null);
    if (issue && language === "zh-CN") void translateEnglishToChinese(issue.detail).then((value) => { if (!cancelled) setTranslated(value); });
    return () => { cancelled = true; };
  }, [issue?.detail, language]);
  if (!issue) return null;
  return <div className="error-message" role="alert"><p>{language === "zh-CN" ? knownErrors[issue.code] ?? translated ?? issue.detail : issue.detail}</p><details><summary>{copy[language].details}</summary><code>{issue.code}</code><p>{translated ?? issue.detail}</p></details></div>;
}

// Settings save themselves: a choice at once, typed text after this pause.
const TYPING_PAUSE_MS = 500;
/**
 * One settings document's autosave. `schedule` after an edit, `flush` before
 * the settings are left; one request at a time, and an edit made during a
 * request is saved by the next one. `save` always reads the newest draft.
 */
function useAutosave(save: () => Promise<void>) {
  const latestSave = useRef(save);
  latestSave.current = save;
  const job = useRef<{ timer?: number; running: Promise<void> | null; again: boolean }>({ running: null, again: false });
  const [pending, setPending] = useState(false);
  const run = useCallback((): Promise<void> => {
    const current = job.current;
    if (current.running) { current.again = true; return current.running; }
    current.running = (async () => {
      try { do { current.again = false; await latestSave.current(); } while (current.again); }
      finally { current.running = null; if (current.timer === undefined) setPending(false); }
    })();
    return current.running;
  }, []);
  const schedule = useCallback((delay: number) => {
    const current = job.current;
    if (current.timer !== undefined) window.clearTimeout(current.timer);
    setPending(true);
    current.timer = window.setTimeout(() => { current.timer = undefined; void run(); }, delay);
  }, [run]);
  /** Saves an edit still waiting for its pause at once; resolves when nothing is left to save. */
  const flush = useCallback((): Promise<void> => {
    const current = job.current;
    if (current.timer === undefined) return current.running ?? Promise.resolve();
    window.clearTimeout(current.timer); current.timer = undefined;
    return run();
  }, [run]);
  return { pending, schedule, flush };
}
const isAbsolutePath = (value: string) => /^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+|\/)/.test(value);
/** What the Hub would refuse in launch settings, found before asking it: such a value stays in its field, unsaved. */
function launchProblem(draft: ApplicationSettingsDto): FieldIssue | null {
  if (draft.workspaceDir && !isAbsolutePath(draft.workspaceDir)) return { field: "workspace-dir", code: "WORKSPACE_DIR_INVALID", detail: "The folder for new projects must be a full path, such as D:\\Projects." };
  const port = draft.studioPort ?? initialLaunch.studioPort!;
  if (!Number.isInteger(port) || port < 1024 || port > 65535) return { field: "studio-port", code: "STUDIO_PORT_INVALID", detail: "The project runtime port must be a whole number from 1024 to 65535." };
  if (port === draft.monitorPort || String(port) === window.location.port) return { field: "studio-port", code: "PORT_CONFLICT", detail: "Hub, Studio and Monitor must use different ports." };
  return null;
}
function renderProblem(draft: RenderDefaults): FieldIssue | null {
  const timeout = draft.renderTimeoutS;
  return timeout !== null && timeout !== undefined && !(timeout >= 1 && timeout <= 300)
    ? { field: "render-timeout", code: "RENDER_TIMEOUT_INVALID", detail: "The render request timeout must be from 1 to 300 seconds." } : null;
}

function WorkspaceDiagnosticsSettings() {
  const t = useT();
  const { developerMode, setDeveloperMode, eventStreamVisible, setEventStreamVisible } = usePreferences();
  return <div>
    <label><input type="checkbox" checked={developerMode} onChange={(event) => setDeveloperMode(event.target.checked)} /> {t("settings.fields.developerMode")}</label>
    <p className="help">{t("settings.developerMode.help")}</p>
    {developerMode && <label><input type="checkbox" checked={eventStreamVisible} onChange={(event) => setEventStreamVisible(event.target.checked)} /> {t("settings.fields.eventStreamVisible")}</label>}
  </div>;
}

function App() {
  const view = new URLSearchParams(window.location.search).get("view");
  const fabView = view === "fab";
  const hostedView = fabView;
  const [preferences, setPreferences] = useState<AppearancePreferences>(() => hostedView ? appearanceFromSearch(window.location.search) : { ...DEFAULT_APPEARANCE });
  const [savedAppearance, setSavedAppearance] = useState<AppearancePreferences | null>(null);
  const [userSettings, setUserSettings] = useState<UserSettingsDto | null>(null);
  // What a new conversation starts with, and what is actually saved for it.
  const [chatDraft, setChatDraft] = useState<ChatDefaults>(NO_CHAT_DEFAULTS);
  const [savedChatDefaults, setSavedChatDefaults] = useState<ChatDefaults>(NO_CHAT_DEFAULTS);
  const [renderDraft, setRenderDraft] = useState<RenderDefaults>(() => renderDefaults());
  const [savedRenderDefaults, setSavedRenderDefaults] = useState<RenderDefaults>(() => renderDefaults());
  const [chatProviders, setChatProviders] = useState<readonly ChatProvider[]>([]);
  const [defaultModelCustom, setDefaultModelCustom] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<ChatWorkspace | null>(null);
  const [apps, setApps] = useState<readonly AppStatus[] | null>(null);
  const [statusIssue, setStatusIssue] = useState<Issue | null>(null);
  const [actionIssues, setActionIssues] = useState<Partial<Record<AppId, Issue>>>({});
  const [busyServices, setBusyServices] = useState<ReadonlySet<string>>(new Set());
  const [appearanceIssue, setAppearanceIssue] = useState<Issue | null>(null);
  const [launchIssue, setLaunchIssue] = useState<Issue | null>(null);
  const [launchDraft, setLaunchDraft] = useState<ApplicationSettingsDto>(initialLaunch);
  const [savedLaunch, setSavedLaunch] = useState<ApplicationSettingsDto | null>(null);
  const appearanceEdits = useRef(0); const launchEdits = useRef(0);
  const settingsRef = useRef<HTMLElement>(null);
  const readingApps = useRef(false); const readingSettings = useRef(false);
  const actionLocks = useRef(new Set<string>());
  const t = (key: CopyKey) => translateMessage(copy[preferences.language], key);
  const appearanceDirty = JSON.stringify(preferences) !== JSON.stringify(savedAppearance) ||
    JSON.stringify(chatDraft) !== JSON.stringify(savedChatDefaults) || JSON.stringify(renderDraft) !== JSON.stringify(savedRenderDefaults);
  const launchDirty = JSON.stringify(launchDraft) !== JSON.stringify(savedLaunch);
  const connected = apps !== null && statusIssue === null;
  useEffect(() => applyAppearance(preferences), [preferences]);
  const refreshApps = useCallback(async () => {
    if (readingApps.current) return;
    readingApps.current = true;
    try { setApps(await responseData<AppStatus[]>(listAppsApiAppsGet({ client: hubClient }))); setStatusIssue(null); }
    catch (cause) { setStatusIssue(issueOf(cause)); }
    finally { readingApps.current = false; }
  }, []);
  const readSettings = useCallback(async (refreshConnections = false) => {
    if (readingSettings.current) return;
    readingSettings.current = true;
    const appearanceRevision = appearanceEdits.current; const launchRevision = launchEdits.current;
    const results = await Promise.allSettled([
      responseData<UserSettingsDto>(getUserSettingsApiSettingsUserGet({ client: hubClient })),
      responseData<ApplicationSettingsDto>(applicationSettingsApiSettingsAppsGet({ client: hubClient })),
      responseData<ChatProvider[]>(chatProvidersApiChatProvidersGet({ client: hubClient, query: { refresh: refreshConnections } })),
      responseData<ChatWorkspace>(chatWorkspaceApiChatWorkspaceGet({ client: hubClient })),
    ]);
    const [appearance, launch, connections, workspaceRead] = results;
    if (appearance.status === "fulfilled") {
      setUserSettings(appearance.value); const resolved = resolveAppearance(appearance.value); setSavedAppearance(resolved);
      const savedChat: ChatDefaults = { chatProvider: appearance.value.chatProvider ?? null, chatModel: appearance.value.chatModel ?? null };
      setSavedChatDefaults(savedChat);
      const savedRender = renderDefaults(appearance.value); setSavedRenderDefaults(savedRender);
      // Checking the connections again re-reads what is installed, not what is
      // being edited: an unsaved choice stays where the person left it.
      if (appearanceEdits.current === appearanceRevision && !refreshConnections) { setPreferences(hostedView ? appearanceFromSearch(window.location.search, resolved) : resolved); setChatDraft(savedChat); setRenderDraft(savedRender); }
      setAppearanceIssue(null);
    } else setAppearanceIssue(issueOf(appearance.reason));
    if (connections.status === "fulfilled") setChatProviders(connections.value);
    if (workspaceRead.status === "fulfilled") setWorkspace(workspaceRead.value);
    if (launch.status === "fulfilled") {
      setSavedLaunch(launch.value); if (launchEdits.current === launchRevision && !refreshConnections) setLaunchDraft(launch.value); setLaunchIssue(null);
    } else setLaunchIssue(issueOf(launch.reason));
    readingSettings.current = false;
  }, [hostedView]);
  useEffect(() => {
    void refreshApps(); void readSettings();
    const refreshVisible = () => { if (!document.hidden) void refreshApps(); };
    const timer = window.setInterval(refreshVisible, 1000);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", refreshVisible); };
  }, [refreshApps, readSettings]);
  // The same requests the Save buttons made, now made by each edit.
  const appearanceSave = useAutosave(async () => {
    if (userSettings === null || !appearanceDirty || renderProblem(renderDraft)) return;
    const revision = appearanceEdits.current; setAppearanceIssue(null);
    try {
      const current = await responseData<UserSettingsDto>(getUserSettingsApiSettingsUserGet({ client: hubClient }));
      const saved = await responseData<UserSettingsDto>(putUserSettingsApiSettingsUserPut({ client: hubClient, body: { ...current, ...preferences, ...chatDraft, ...renderDraft } }));
      const resolved = resolveAppearance(saved); setUserSettings(saved); setSavedAppearance(resolved);
      const savedChat: ChatDefaults = { chatProvider: saved.chatProvider ?? null, chatModel: saved.chatModel ?? null };
      setSavedChatDefaults(savedChat);
      const savedRender = renderDefaults(saved); setSavedRenderDefaults(savedRender);
      if (appearanceEdits.current === revision) { setPreferences(resolved); setChatDraft(savedChat); setRenderDraft(savedRender); }
    } catch (cause) { setAppearanceIssue(issueOf(cause)); }
  });
  const launchSave = useAutosave(async () => {
    if (!launchDirty || launchProblem(launchDraft)) return;
    const revision = launchEdits.current; setLaunchIssue(null);
    try {
      const saved = await responseData<ApplicationSettingsDto>(updateApplicationSettingsApiSettingsAppsPut({ client: hubClient, body: launchDraft }));
      setSavedLaunch(saved); if (launchEdits.current === revision) setLaunchDraft(saved);
    } catch (cause) { setLaunchIssue(issueOf(cause)); }
  });
  const changeAppearance = (patch: Partial<AppearancePreferences>) => {
    appearanceEdits.current += 1;
    setAppearanceIssue(null);
    if (patch.language === "zh-CN") void prepareEnglishToChinese();
    setPreferences((current) => ({ ...current, ...patch }));
    appearanceSave.schedule(0);
  };
  const changeLaunch = (patch: Partial<ApplicationSettingsDto>, delay = TYPING_PAUSE_MS) => {
    launchEdits.current += 1; setLaunchIssue(null);
    setLaunchDraft((current) => ({ ...current, ...patch }));
    launchSave.schedule(delay);
  };
  const { flush: flushAppearance } = appearanceSave, { flush: flushLaunch } = launchSave;
  useEffect(() => {
    // Nothing typed is left behind: closing Settings, hiding or leaving the
    // window saves what is still waiting for its pause.
    const flush = () => { void flushAppearance(); void flushLaunch(); };
    const hidden = () => { if (document.hidden) flush(); };
    const dialog = settingsRef.current?.closest("dialog");
    dialog?.addEventListener("close", flush);
    document.addEventListener("visibilitychange", hidden);
    window.addEventListener("pagehide", flush);
    return () => {
      dialog?.removeEventListener("close", flush);
      document.removeEventListener("visibilitychange", hidden);
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, [flushAppearance, flushLaunch]);
  // A launch change refused while the project runtime ran is saved when it stops.
  const runtimeRunning = (apps ?? []).some((app) => app.serviceId === "studio" && app.processId != null);
  const launchRefused = launchIssue?.code === "APPS_RUNNING";
  const wasRunning = useRef(runtimeRunning);
  const { schedule: scheduleLaunch } = launchSave;
  useEffect(() => {
    if (wasRunning.current && !runtimeRunning && launchRefused) scheduleLaunch(0);
    wasRunning.current = runtimeRunning;
  }, [runtimeRunning, launchRefused, scheduleLaunch]);
  const act = async (app: AppStatus) => {
    if (actionLocks.current.has(app.serviceId) || app.state === "unavailable") return;
    actionLocks.current.add(app.serviceId); setBusyServices(new Set(actionLocks.current));
    setActionIssues((current) => {
      const next = { ...current };
      for (const sibling of apps ?? []) if (sibling.serviceId === app.serviceId) delete next[sibling.appId];
      return next;
    });
    try {
      const request = app.state === "running" ? stopAppApiAppsAppIdStopPost : startAppApiAppsAppIdStartPost;
      await responseData<AppStatus>(request({ client: hubClient, path: { app_id: app.appId } }));
      await refreshApps();
    } catch (cause) { setActionIssues((current) => ({ ...current, [app.appId]: issueOf(cause) })); }
    finally { actionLocks.current.delete(app.serviceId); setBusyServices(new Set(actionLocks.current)); }
  };
  if (fabView) return <FabPage preferences={preferences} client={hubClient} readResult={responseData} />;
  // One detection answers both places: what a connection is, and what it lists.
  const connectionWords = (row: ChatProvider) => !row.installed ? t("notInstalled")
    : row.id === "coding-plan" && !row.available ? t("notConfigured")
    : row.modelCatalog === "checking" ? t("checking")
    : row.signedIn === true ? t("signedIn") : row.signedIn === false ? t("signedOut") : t("signInUnknown");
  const connectionState = (row: ChatProvider) => ` · ${connectionWords(row)}`;
  // The same finding, said in this window's language; the connection's own
  // sentence is kept for anything these four cases do not cover.
  const catalogWords = (row: ChatProvider) => row.modelCatalog === "checking" ? t("catalogChecking")
    : row.modelCatalog === "ready" ? t("catalogReady")
    : row.signedIn === false ? t("catalogSignIn")
    : row.installed && (row.modelDetail ?? "").includes("no model list") ? t("catalogNoList")
    : row.modelDetail ?? "";
  const selectedConnection = chatProviders.find((row) => row.id === (chatDraft.chatProvider ?? "codex"));
  const defaultModelOptions = [...new Set([...(selectedConnection?.models ?? []), ...(chatDraft.chatModel ? [chatDraft.chatModel] : [])])];
  // Chat and render defaults are saved in the same preferences document as the display choices.
  const editDefaults = (delay: number) => { appearanceEdits.current += 1; setAppearanceIssue(null); appearanceSave.schedule(delay); };
  const settingsSaving = appearanceSave.pending || launchSave.pending;
  // A value that cannot be saved is named once its pause is over, not while it is typed.
  const launchInvalid = !launchSave.pending && launchDirty ? launchProblem(launchDraft) : null;
  const renderInvalid = !appearanceSave.pending && appearanceDirty ? renderProblem(renderDraft) : null;
  const settings = <>
    <ErrorMessage issue={statusIssue} language={preferences.language} />
    <section id="settings" className="settings" ref={settingsRef}>
      <div className="settings-section">
        <h2>{t("appearance")}</h2>
        <div className="settings-fields">
          <label>{t("language")}<select id="language" value={preferences.language} onChange={(event) => changeAppearance({ language: event.target.value as Language })}><option value="zh-CN">简体中文</option><option value="en">English</option></select></label>
          <label>{t("theme")}<select id="theme" value={preferences.theme} onChange={(event) => changeAppearance({ theme: event.target.value as AppearancePreferences["theme"] })}><option value="system">{t("system")}</option><option value="dark">{t("dark")}</option><option value="light">{t("light")}</option></select></label>
          <label>{t("size")}<select id="font-scale" value={preferences.fontScale} onChange={(event) => changeAppearance({ fontScale: Number(event.target.value) as AppearancePreferences["fontScale"] })}><option value="0.9">{t("compact")}</option><option value="1">{t("normal")}</option><option value="1.1">{t("large")}</option></select></label>
        </div>
      </div>

      <div className="settings-section">
        <h2>{t("chatDefaults")}</h2>
        <div className="settings-fields">
          <label>{t("chatProvider")}<select id="default-chat-provider" value={chatDraft.chatProvider ?? ""} onChange={(event) => { editDefaults(0); setDefaultModelCustom(null); setChatDraft((current) => ({ ...current, chatProvider: (event.target.value || null) as ChatDefaults["chatProvider"], chatModel: null })); }}>
            <option value="">{t("cliUnset")}</option>
            {(chatProviders.length ? chatProviders : [{ id: "codex", label: "Codex CLI", available: true, detail: "" }] as readonly ChatProvider[]).map((item) => <option key={item.id} value={item.id} disabled={!item.available}>{item.label}{connectionState(item)}</option>)}
          </select></label>
          <label>{t("chatModel")}<select id="default-chat-model" value={defaultModelCustom !== null ? "__custom__" : chatDraft.chatModel ?? ""} onChange={(event) => {
            if (event.target.value === "__custom__") { setDefaultModelCustom(chatDraft.chatModel ?? ""); return; }
            editDefaults(0); setDefaultModelCustom(null); setChatDraft((current) => ({ ...current, chatModel: event.target.value || null }));
          }}>
            <option value="">{t("cliDefault")}</option>
            {defaultModelOptions.map((item) => <option key={item} value={item}>{item}</option>)}
            <option value="__custom__">{t("customModel")}</option>
          </select></label>
          {defaultModelCustom !== null && <label htmlFor="default-chat-model-custom">{t("customModel")}
            <input id="default-chat-model-custom" autoFocus value={defaultModelCustom}
              onChange={(event) => { editDefaults(TYPING_PAUSE_MS); setDefaultModelCustom(event.target.value); setChatDraft((current) => ({ ...current, chatModel: event.target.value.trim() || null })); }} /></label>}
        </div>
        <p className="help" id="connection-state">{selectedConnection
          ? <>{selectedConnection.label} · {connectionWords(selectedConnection)}{catalogWords(selectedConnection) ? ` · ${catalogWords(selectedConnection)}` : ""}</>
          : t("checking")}</p>
        <p className="help">{t("chatDefaultsHelp")}</p>
      </div>

      <div className="settings-section">
        <h2>{t("renderSettings")}</h2>
        <div className="settings-fields">
          <label>{t("renderProvider")}<select id="render-provider" value={renderDraft.renderProvider ?? "off"} onChange={(event) => {
            editDefaults(0); setRenderDraft((value) => ({ ...value, renderProvider: event.target.value as RenderDefaults["renderProvider"] }));
          }}><option value="off">{t("renderOff")}</option><option value="gemini">Gemini</option></select></label>
          <label>{t("renderModel")}<input id="render-model" value={renderDraft.renderModel ?? ""} placeholder="gemini-3.1-flash-image" onChange={(event) => {
            editDefaults(TYPING_PAUSE_MS); setRenderDraft((value) => ({ ...value, renderModel: event.target.value.trim() || null }));
          }} /></label>
          <label>{t("renderTimeout")}<input id="render-timeout" type="number" min="1" max="300" value={renderDraft.renderTimeoutS ?? ""} placeholder={t("runtimeDefault")}
            aria-invalid={renderInvalid ? true : undefined} onChange={(event) => {
            editDefaults(TYPING_PAUSE_MS); setRenderDraft((value) => ({ ...value, renderTimeoutS: event.target.value === "" ? null : Number(event.target.value) }));
          }} /></label>
        </div>
        <ErrorMessage issue={renderInvalid} language={preferences.language} />
        <p className="help">{t("renderSettingsHelp")}</p>
      </div>

      <div className="settings-section">
        <h2>{t("workspace")}</h2>
        <label htmlFor="workspace-dir">{t("workspaceDir")}
          <input id="workspace-dir" value={launchDraft.workspaceDir ?? ""} placeholder={workspace?.workspaceDir ?? ""}
            aria-invalid={launchInvalid?.field === "workspace-dir" ? true : undefined}
            onChange={(event) => changeLaunch({ workspaceDir: event.target.value || null })} /></label>
        <p className="help">{t("workspaceHelp")}</p>
        <details className="advanced"><summary>{t("advanced")}</summary>
          <WorkspaceDiagnosticsSettings />
          <div className="chat-service-settings">{(apps ?? []).filter((app) => app.appId === "monkeyarch").map((app) => <div key={app.appId}><span>Project Runtime · {t(app.state)}</span><button className="btn" disabled={!connected || busyServices.has(app.serviceId) || app.state === "starting" || app.state === "stopping"} onClick={() => void act(app)}>{t(app.state === "running" ? "stop" : "start")}</button><ErrorMessage issue={actionIssues[app.appId] ?? app.error ?? null} language={preferences.language} /></div>)}</div>
          <div className="settings-fields">
            <label>{t("reference")}<input id="reference-run" value={launchDraft.referenceRun ?? ""} onChange={(event) => changeLaunch({ referenceRun: event.target.value || null })} /></label>
            <label>{t("cad")}<select id="cad-export" value={launchDraft.cadExport} onChange={(event) => changeLaunch({ cadExport: event.target.value as ApplicationSettingsDto["cadExport"] }, 0)}><option value="occt">{t("occt")}</option><option value="rhino">{t("rhino")}</option><option value="off">{t("off")}</option></select></label>
            <label>{t("studioPort")}<input id="studio-port" type="number" min="1024" max="65535" value={launchDraft.studioPort}
              aria-invalid={launchInvalid?.field === "studio-port" ? true : undefined} onChange={(event) => changeLaunch({ studioPort: Number(event.target.value) })} /></label>
          </div>
          <p className="help">{t("launchHelp")}</p>
        </details>
        <ErrorMessage issue={launchIssue ?? launchInvalid} language={preferences.language} />
      </div>

      <div className="settings-footer">
        <ErrorMessage issue={appearanceIssue} language={preferences.language} />
        <div className="settings-status">
          <span id="settings-save-state" role="status" data-state={settingsSaving ? "saving" : appearanceDirty || launchDirty ? "unsaved" : "saved"}>
            {settingsSaving ? t("saving") : appearanceDirty || launchDirty ? t("unsaved") : savedAppearance !== null ? t("saved") : ""}</span>
          <button id="recheck-connections" className="btn" type="button" disabled={!connected} onClick={() => void readSettings(true)}>{t("recheck")}</button>
        </div>
      </div>
    </section></>;
  return <UserPreferencesProvider appearance={preferences}><ChatShell preferences={preferences} configuredProject={savedLaunch?.projectDir ?? null} settings={settings}
    settingsDirty={appearanceDirty || launchDirty || settingsSaving || busyServices.size > 0}
    defaults={{ provider: savedChatDefaults.chatProvider ?? "codex", model: savedChatDefaults.chatModel }}
    workspace={workspace} apps={statusIssue ? null : apps} /></UserPreferencesProvider>;
}
const root = document.getElementById("root");
if (!root) throw new Error("MonkeyHub root is missing.");
createRoot(root).render(<StrictMode><App /></StrictMode>);
