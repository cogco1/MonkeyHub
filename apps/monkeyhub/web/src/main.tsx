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
const hubClient = createClient({ baseUrl: window.location.origin });
const initialLaunch: ApplicationSettingsDto = { projectDir: null, referenceRun: null, cadExport: "occt", studioPort: 8789, monitorPort: 8788 };
type ChatDefaults = { chatProvider: UserSettingsDto["chatProvider"]; chatModel: string | null };
const NO_CHAT_DEFAULTS: ChatDefaults = { chatProvider: null, chatModel: null };
const copy = {
  "zh-CN": {
    apps: "工作区", settings: "设置", refresh: "刷新", connected: "Hub 已连接", connecting: "正在连接", disconnected: "无法读取工作区状态",
    closeNote: "关闭此网页不会停止工作区。请使用停止按钮，或从托盘退出 MonkeyHub。",
    archTitle: "建模", diagramTitle: "图纸", monitorTitle: "用量", boardTitle: "展示", fabTitle: "制作",
    arch: "三维建模与空间修改", diagram: "图纸、图片与批注", monitor: "调用用量与费用估算", board: "白板排图、圈注与会议投屏", fab: "打印模型缩放、拆件与文件发送",
    shared: "建模、图纸与展示共用项目和运行服务，停止任一项会同时停止这三个工作区。",
    stopped: "未启动", starting: "正在启动", running: "运行中", stopping: "正在停止", error: "启动失败", unavailable: "未随此版本提供", ready: "可用",
    open: "进入工作区", start: "启动", stop: "停止", stopShared: "停止共享服务", details: "详细信息", noProject: "尚未选择项目",
    project: "项目目录", projectHelp: "选择一个已有项目，用于建模、图纸与展示。查看用量和制作无需项目。", projectPlaceholder: "已有项目的完整路径，可留空",
    reference: "参考运行（可选）", launch: "启动设置", advanced: "更多启动选项", cad: "模型导出", occt: "OCCT", rhino: "Rhino（兼容）", off: "关闭导出",
    studioPort: "Studio 端口", monitorPort: "Monitor 端口", saveLaunch: "保存启动设置", saved: "已保存", unsaved: "有未保存修改", saving: "正在保存…",
    launchHelp: "项目与端口修改在下次启动时生效；运行中的工作区需要先停止。", appearance: "显示设置", language: "语言", theme: "主题",
    system: "跟随系统", dark: "深色", light: "浅色", size: "字号", compact: "紧凑", normal: "标准", large: "较大", saveAppearance: "保存显示设置",
    appearanceHelp: "从 Hub 进入工作区时继承当前显示偏好；已打开的工作区不会自动切换。", working: "处理中…",
    chatDefaults: "新对话默认连接", chatProvider: "默认 CLI", chatModel: "默认模型", cliDefault: "CLI 默认模型", cliUnset: "未设置（使用 Codex CLI）", chatDefaultsHelp: "只用于新建的对话。已建立的对话保留它自己的连接、模型和原生会话，不会因为这里的修改而改变。",
    recheck: "重新检测", checking: "正在检测…", notInstalled: "未安装", signedIn: "已登录", signedOut: "未登录", signInUnknown: "登录状态未知",
    notConfigured: "未配置", customModel: "自定义模型 ID…", modelsFrom: "模型来源",
    catalogReady: "模型目录来自这个 CLI 自己的列表。", catalogNoList: "这个 CLI 不提供模型目录；可以手填模型 ID。",
    catalogSignIn: "登录这个 CLI 后才能读取它的模型目录。", catalogChecking: "正在向这个 CLI 读取可用模型…",
    workspace: "工作区", workspaceDir: "新项目所在文件夹", saveSettings: "保存显示与连接", saveWorkspace: "保存工作区与启动",
    workspaceHelp: "「新建项目」会在这个文件夹里创建项目。留空则使用当前项目所在的文件夹。",
  },
  en: {
    apps: "Workspaces", settings: "Settings", refresh: "Refresh", connected: "Hub connected", connecting: "Connecting", disconnected: "Cannot read workspace status",
    closeNote: "Closing this page does not stop workspaces. Use their Stop buttons, or quit MonkeyHub from the system tray.",
    archTitle: "Modeling", diagramTitle: "Drawings", monitorTitle: "Usage", boardTitle: "Presentation", fabTitle: "Fabrication",
    arch: "3D modelling and spatial changes", diagram: "Drawings, images and annotations", monitor: "Call usage and cost estimates", board: "Drawing board, markup and meeting presentation", fab: "Print model scaling, splitting and file upload",
    shared: "Modeling, drawings and presentation share one project and service. Stopping any one stops all three workspaces.",
    stopped: "Stopped", starting: "Starting", running: "Running", stopping: "Stopping", error: "Failed", unavailable: "Not included in this version", ready: "Ready",
    open: "Enter workspace", start: "Start", stop: "Stop", stopShared: "Stop shared service", details: "Details", noProject: "No project selected",
    project: "Project directory", projectHelp: "Choose one existing project for modeling, drawings and presentation. Usage and fabrication do not require a project.", projectPlaceholder: "Full path to an existing project, optional",
    reference: "Reference run (optional)", launch: "Launch settings", advanced: "More launch options", cad: "Model export", occt: "OCCT", rhino: "Rhino (compatibility)", off: "Export off",
    studioPort: "Studio port", monitorPort: "Monitor port", saveLaunch: "Save launch settings", saved: "Saved", unsaved: "Unsaved changes", saving: "Saving…",
    launchHelp: "Project and port changes apply on the next start. Stop running workspaces before saving them.", appearance: "Display settings", language: "Language", theme: "Theme",
    system: "System", dark: "Dark", light: "Light", size: "Text size", compact: "Compact", normal: "Standard", large: "Larger", saveAppearance: "Save display settings",
    appearanceHelp: "Workspaces opened from Hub inherit these display choices. Already open workspaces do not change automatically.", working: "Working…",
    chatDefaults: "New conversation defaults", chatProvider: "Default CLI", chatModel: "Default model", cliDefault: "CLI default model", cliUnset: "Not set (use Codex CLI)", chatDefaultsHelp: "Used when a conversation is created. An existing conversation keeps its own connection, model and native session; changing this never reaches one.",
    recheck: "Check again", checking: "Checking…", notInstalled: "Not installed", signedIn: "Signed in", signedOut: "Not signed in", signInUnknown: "Sign-in state unknown",
    notConfigured: "Not configured", customModel: "Custom model id…", modelsFrom: "Model list",
    catalogReady: "The model list comes from this CLI's own catalogue.", catalogNoList: "This CLI offers no model list; a model id can be entered by hand.",
    catalogSignIn: "Sign in to this CLI to read its model list.", catalogChecking: "Reading the available models from this CLI…",
    workspace: "Workspace", workspaceDir: "Folder for new projects", saveSettings: "Save display and connection", saveWorkspace: "Save workspace and launch",
    workspaceHelp: "New projects are created in this folder. Left empty, the folder of the current project is used.",
  },
} as const;
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

function App() {
  const fabView = new URLSearchParams(window.location.search).get("view") === "fab";
  const [preferences, setPreferences] = useState<AppearancePreferences>(() => fabView ? appearanceFromSearch(window.location.search) : { ...DEFAULT_APPEARANCE });
  const [savedAppearance, setSavedAppearance] = useState<AppearancePreferences | null>(null);
  const [userSettings, setUserSettings] = useState<UserSettingsDto | null>(null);
  // What a new conversation starts with, and what is actually saved for it.
  const [chatDraft, setChatDraft] = useState<ChatDefaults>(NO_CHAT_DEFAULTS);
  const [savedChatDefaults, setSavedChatDefaults] = useState<ChatDefaults>(NO_CHAT_DEFAULTS);
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
  const [savingAppearance, setSavingAppearance] = useState(false);
  const [savingLaunch, setSavingLaunch] = useState(false);
  const appearanceEdits = useRef(0); const launchEdits = useRef(0);
  const readingApps = useRef(false); const readingSettings = useRef(false);
  const actionLocks = useRef(new Set<string>());
  const t = (key: CopyKey) => translateMessage(copy[preferences.language], key);
  const appearanceDirty = JSON.stringify(preferences) !== JSON.stringify(savedAppearance) ||
    JSON.stringify(chatDraft) !== JSON.stringify(savedChatDefaults);
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
      // Checking the connections again re-reads what is installed, not what is
      // being edited: an unsaved choice stays where the person left it.
      if (appearanceEdits.current === appearanceRevision && !refreshConnections) { setPreferences(fabView ? appearanceFromSearch(window.location.search, resolved) : resolved); setChatDraft(savedChat); }
      setAppearanceIssue(null);
    } else setAppearanceIssue(issueOf(appearance.reason));
    if (connections.status === "fulfilled") setChatProviders(connections.value);
    if (workspaceRead.status === "fulfilled") setWorkspace(workspaceRead.value);
    if (launch.status === "fulfilled") {
      setSavedLaunch(launch.value); if (launchEdits.current === launchRevision && !refreshConnections) setLaunchDraft(launch.value); setLaunchIssue(null);
    } else setLaunchIssue(issueOf(launch.reason));
    readingSettings.current = false;
  }, []);
  useEffect(() => {
    void refreshApps(); void readSettings();
    const refreshVisible = () => { if (!document.hidden) void refreshApps(); };
    const timer = window.setInterval(refreshVisible, 1000);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", refreshVisible); };
  }, [refreshApps, readSettings]);
  const changeAppearance = (patch: Partial<AppearancePreferences>) => {
    appearanceEdits.current += 1;
    setAppearanceIssue(null);
    if (patch.language === "zh-CN") void prepareEnglishToChinese();
    setPreferences((current) => ({ ...current, ...patch }));
  };
  const changeLaunch = (patch: Partial<ApplicationSettingsDto>) => {
    launchEdits.current += 1; setLaunchIssue(null);
    setLaunchDraft((current) => ({ ...current, ...patch }));
  };
  const saveAppearance = async () => {
    if (savingAppearance || userSettings === null) return;
    const revision = appearanceEdits.current; setSavingAppearance(true); setAppearanceIssue(null);
    try {
      const current = await responseData<UserSettingsDto>(getUserSettingsApiSettingsUserGet({ client: hubClient }));
      const saved = await responseData<UserSettingsDto>(putUserSettingsApiSettingsUserPut({ client: hubClient, body: { ...current, ...preferences, ...chatDraft } }));
      const resolved = resolveAppearance(saved); setUserSettings(saved); setSavedAppearance(resolved);
      const savedChat: ChatDefaults = { chatProvider: saved.chatProvider ?? null, chatModel: saved.chatModel ?? null };
      setSavedChatDefaults(savedChat);
      if (appearanceEdits.current === revision) { setPreferences(resolved); setChatDraft(savedChat); }
    } catch (cause) { setAppearanceIssue(issueOf(cause)); }
    finally { setSavingAppearance(false); }
  };
  const saveLaunch = async () => {
    if (savingLaunch) return;
    const revision = launchEdits.current; setSavingLaunch(true); setLaunchIssue(null);
    try {
      const saved = await responseData<ApplicationSettingsDto>(updateApplicationSettingsApiSettingsAppsPut({ client: hubClient, body: launchDraft }));
      setSavedLaunch(saved); if (launchEdits.current === revision) setLaunchDraft(saved);
    } catch (cause) { setLaunchIssue(issueOf(cause)); }
    finally { setSavingLaunch(false); }
  };
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
  const settings = <>
    <ErrorMessage issue={statusIssue} language={preferences.language} />
    <section id="settings" className="settings">
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
          <label>{t("chatProvider")}<select id="default-chat-provider" value={chatDraft.chatProvider ?? ""} onChange={(event) => { appearanceEdits.current += 1; setAppearanceIssue(null); setDefaultModelCustom(null); setChatDraft((current) => ({ ...current, chatProvider: (event.target.value || null) as ChatDefaults["chatProvider"], chatModel: null })); }}>
            <option value="">{t("cliUnset")}</option>
            {(chatProviders.length ? chatProviders : [{ id: "codex", label: "Codex CLI", available: true, detail: "" }] as readonly ChatProvider[]).map((item) => <option key={item.id} value={item.id} disabled={!item.available}>{item.label}{connectionState(item)}</option>)}
          </select></label>
          <label>{t("chatModel")}<select id="default-chat-model" value={defaultModelCustom !== null ? "__custom__" : chatDraft.chatModel ?? ""} onChange={(event) => {
            appearanceEdits.current += 1; setAppearanceIssue(null);
            if (event.target.value === "__custom__") { setDefaultModelCustom(chatDraft.chatModel ?? ""); return; }
            setDefaultModelCustom(null); setChatDraft((current) => ({ ...current, chatModel: event.target.value || null }));
          }}>
            <option value="">{t("cliDefault")}</option>
            {defaultModelOptions.map((item) => <option key={item} value={item}>{item}</option>)}
            <option value="__custom__">{t("customModel")}</option>
          </select></label>
          {defaultModelCustom !== null && <label htmlFor="default-chat-model-custom">{t("customModel")}
            <input id="default-chat-model-custom" autoFocus value={defaultModelCustom}
              onChange={(event) => { appearanceEdits.current += 1; setDefaultModelCustom(event.target.value); setChatDraft((current) => ({ ...current, chatModel: event.target.value.trim() || null })); }} /></label>}
        </div>
        <p className="help" id="connection-state">{selectedConnection
          ? <>{selectedConnection.label} · {connectionWords(selectedConnection)}{catalogWords(selectedConnection) ? ` · ${catalogWords(selectedConnection)}` : ""}</>
          : t("checking")}</p>
        <p className="help">{t("chatDefaultsHelp")}</p>
      </div>

      <div className="settings-section">
        <h2>{t("workspace")}</h2>
        <label htmlFor="workspace-dir">{t("workspaceDir")}
          <input id="workspace-dir" value={launchDraft.workspaceDir ?? ""} placeholder={workspace?.workspaceDir ?? ""}
            onChange={(event) => changeLaunch({ workspaceDir: event.target.value || null })} /></label>
        <p className="help">{t("workspaceHelp")}</p>
        <details className="advanced"><summary>{t("advanced")}</summary>
          <div className="chat-service-settings">{(apps ?? []).filter((app) => app.appId === "monkeyarch" || app.appId === "monkeymonitor").map((app) => <div key={app.appId}><span>{app.serviceId === "studio" ? "Studio" : "Monitor"} · {t(app.state)}</span><button className="btn" disabled={!connected || busyServices.has(app.serviceId) || app.state === "starting" || app.state === "stopping"} onClick={() => void act(app)}>{t(app.state === "running" ? "stop" : "start")}</button><ErrorMessage issue={actionIssues[app.appId] ?? app.error ?? null} language={preferences.language} /></div>)}</div>
          <div className="settings-fields">
            <label>{t("reference")}<input id="reference-run" value={launchDraft.referenceRun ?? ""} onChange={(event) => changeLaunch({ referenceRun: event.target.value || null })} /></label>
            <label>{t("cad")}<select id="cad-export" value={launchDraft.cadExport} onChange={(event) => changeLaunch({ cadExport: event.target.value as ApplicationSettingsDto["cadExport"] })}><option value="occt">{t("occt")}</option><option value="rhino">{t("rhino")}</option><option value="off">{t("off")}</option></select></label>
            <label>{t("studioPort")}<input id="studio-port" type="number" min="1024" max="65535" value={launchDraft.studioPort} onChange={(event) => changeLaunch({ studioPort: Number(event.target.value) })} /></label>
            <label>{t("monitorPort")}<input id="monitor-port" type="number" min="1024" max="65535" value={launchDraft.monitorPort} onChange={(event) => changeLaunch({ monitorPort: Number(event.target.value) })} /></label>
          </div>
          <p className="help">{t("launchHelp")}</p>
        </details>
        <ErrorMessage issue={launchIssue} language={preferences.language} />
      </div>

      <div className="settings-footer">
        <ErrorMessage issue={appearanceIssue} language={preferences.language} />
        <div className="save-row">
          <button id="save-appearance" className="btn btn--primary" type="button" disabled={savingAppearance || userSettings === null || !appearanceDirty} onClick={() => void saveAppearance()}>{savingAppearance ? t("saving") : t("saveSettings")}</button>
          <button id="save-launch" className="btn" type="button" disabled={savingLaunch || !launchDirty} onClick={() => void saveLaunch()}>{savingLaunch ? t("saving") : t("saveWorkspace")}</button>
          <button id="recheck-connections" className="btn" type="button" disabled={!connected} onClick={() => void readSettings(true)}>{t("recheck")}</button>
          <span role="status">{appearanceDirty || launchDirty ? t("unsaved") : savedAppearance !== null ? t("saved") : ""}</span>
        </div>
      </div>
    </section></>;
  return <ChatShell preferences={preferences} configuredProject={savedLaunch?.projectDir ?? null} settings={settings}
    defaults={{ provider: savedChatDefaults.chatProvider ?? "codex", model: savedChatDefaults.chatModel }}
    workspace={workspace} apps={statusIssue ? null : apps} />;
}
const root = document.getElementById("root");
if (!root) throw new Error("MonkeyHub root is missing.");
createRoot(root).render(<StrictMode><App /></StrictMode>);
