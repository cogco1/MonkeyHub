import { StrictMode, useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { applyAppearance, applicationUrl, DEFAULT_APPEARANCE, resolveAppearance, type AppearancePreferences, type Language } from "../../../shared-web/src/appearance.js";
import { translateMessage } from "../../../shared-web/src/i18n.js";
import { prepareEnglishToChinese, translateEnglishToChinese } from "../../../shared-web/src/browserTranslator.js";
import { createClient } from "./api/generated/client";
import { applicationSettingsApiSettingsAppsGet, getUserSettingsApiSettingsUserGet, listAppsApiAppsGet, putUserSettingsApiSettingsUserPut, startAppApiAppsAppIdStartPost, stopAppApiAppsAppIdStopPost, updateApplicationSettingsApiSettingsAppsPut, type AppStatus, type ApplicationSettingsDto, type UserSettingsDto } from "./api/generated";
import "./styles.css";

type AppId = AppStatus["appId"];
type Issue = { code: string; detail: string };
const hubClient = createClient({ baseUrl: window.location.origin });
const initialLaunch: ApplicationSettingsDto = { projectDir: null, referenceRun: null, cadExport: "occt", studioPort: 8789, monitorPort: 8788 };
const copy = {
  "zh-CN": {
    apps: "应用", settings: "设置", refresh: "刷新", connected: "Hub 已连接", connecting: "正在连接", disconnected: "无法读取应用状态",
    closeNote: "关闭此网页不会停止应用。请使用应用的停止按钮，或从托盘退出 Hub。",
    arch: "三维建模与空间修改", diagram: "图纸、图片与批注", monitor: "调用用量与费用估算", board: "白板排图、圈注与会议投屏",
    shared: "MonkeyArch、MonkeyDiagram 与 MonkeyBoard 共用一组服务，停止任一项会同时停止这三个应用。",
    stopped: "未启动", starting: "正在启动", running: "运行中", stopping: "正在停止", error: "启动失败", unavailable: "尚未实现",
    open: "打开应用", start: "启动", stop: "停止", stopShared: "停止共享服务", details: "详细信息", noProject: "尚未选择项目",
    project: "项目目录", projectHelp: "MonkeyArch / MonkeyDiagram / MonkeyBoard 需要已有项目。MonkeyMonitor 可直接启动。", projectPlaceholder: "已有项目的完整路径，可留空",
    reference: "参考运行（可选）", launch: "启动设置", advanced: "更多启动选项", cad: "模型导出", occt: "OCCT", rhino: "Rhino（兼容）", off: "关闭导出",
    studioPort: "Studio 端口", monitorPort: "Monitor 端口", saveLaunch: "保存启动设置", saved: "已保存", unsaved: "有未保存修改", saving: "正在保存…",
    launchHelp: "项目与端口修改在下次启动时生效；运行中的应用需要先停止。", appearance: "显示设置", language: "语言", theme: "主题",
    system: "跟随系统", dark: "深色", light: "浅色", size: "字号", compact: "紧凑", normal: "标准", large: "较大", saveAppearance: "保存显示设置",
    appearanceHelp: "从 Hub 打开应用时继承当前显示偏好；已打开的应用不会自动切换。", working: "处理中…",
  },
  en: {
    apps: "Applications", settings: "Settings", refresh: "Refresh", connected: "Hub connected", connecting: "Connecting", disconnected: "Cannot read application status",
    closeNote: "Closing this page does not stop applications. Use their Stop buttons, or quit Hub from the system tray.",
    arch: "3D modelling and spatial changes", diagram: "Drawings, images and annotations", monitor: "Call usage and cost estimates", board: "Drawing board, markup and meeting presentation",
    shared: "MonkeyArch, MonkeyDiagram and MonkeyBoard share one service. Stopping any one stops all three.",
    stopped: "Stopped", starting: "Starting", running: "Running", stopping: "Stopping", error: "Failed", unavailable: "Not implemented",
    open: "Open application", start: "Start", stop: "Stop", stopShared: "Stop shared service", details: "Details", noProject: "No project selected",
    project: "Project directory", projectHelp: "MonkeyArch / MonkeyDiagram / MonkeyBoard need an existing project. MonkeyMonitor can start without one.", projectPlaceholder: "Full path to an existing project, optional",
    reference: "Reference run (optional)", launch: "Launch settings", advanced: "More launch options", cad: "Model export", occt: "OCCT", rhino: "Rhino (compatibility)", off: "Export off",
    studioPort: "Studio port", monitorPort: "Monitor port", saveLaunch: "Save launch settings", saved: "Saved", unsaved: "Unsaved changes", saving: "Saving…",
    launchHelp: "Project and port changes apply on the next start. Stop running applications before saving them.", appearance: "Display settings", language: "Language", theme: "Theme",
    system: "System", dark: "Dark", light: "Light", size: "Text size", compact: "Compact", normal: "Standard", large: "Larger", saveAppearance: "Save display settings",
    appearanceHelp: "Applications opened from Hub inherit these display choices. Already open applications do not change automatically.", working: "Working…",
  },
} as const;
type CopyKey = keyof typeof copy.en;
const knownErrors: Record<string, string> = {
  PROJECT_REQUIRED: "请先选择包含 project.json 的完整项目目录。", APPS_RUNNING: "请先停止正在运行的应用，再保存启动设置。",
  PORT_CONFLICT: "Hub、Studio 和 Monitor 需要使用不同端口。", PORT_IN_USE: "所选端口已被占用，请选择其他端口。",
  STUDIO_WEB_MISSING: "缺少 Studio 网页文件，请重新准备完整版本包。", SOURCE_VERSION_UNKNOWN: "无法确认此目录的源版本，请使用完整版本包。",
  SERVICE_IDENTITY_MISMATCH: "响应的服务与本次启动或所选项目不一致。", START_TIMEOUT: "应用未能在等待时间内就绪，请查看详细信息。",
  PROCESS_EXITED: "应用进程已退出，请查看详细信息。", START_FAILED: "应用启动失败，请查看详细信息。", APP_UNAVAILABLE: "这个应用尚未实现。",
  HUB_STOPPING: "Hub 正在等待应用结束，请稍候。", SETTINGS_UNAVAILABLE: "本地设置暂时无法读取或保存。",
  APP_SETTINGS_INVALID: "保存的启动设置无效，请检查项目目录和端口。", LOCAL_IO_FAILED: "无法访问本地设置或运行记录目录。",
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
  const [preferences, setPreferences] = useState<AppearancePreferences>({ ...DEFAULT_APPEARANCE });
  const [savedAppearance, setSavedAppearance] = useState<AppearancePreferences | null>(null);
  const [userSettings, setUserSettings] = useState<UserSettingsDto | null>(null);
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
  const appearanceDirty = JSON.stringify(preferences) !== JSON.stringify(savedAppearance);
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
  const readSettings = useCallback(async () => {
    if (readingSettings.current) return;
    readingSettings.current = true;
    const appearanceRevision = appearanceEdits.current; const launchRevision = launchEdits.current;
    const results = await Promise.allSettled([
      responseData<UserSettingsDto>(getUserSettingsApiSettingsUserGet({ client: hubClient })),
      responseData<ApplicationSettingsDto>(applicationSettingsApiSettingsAppsGet({ client: hubClient })),
    ]);
    const [appearance, launch] = results;
    if (appearance.status === "fulfilled") {
      setUserSettings(appearance.value); const resolved = resolveAppearance(appearance.value); setSavedAppearance(resolved);
      if (appearanceEdits.current === appearanceRevision) setPreferences(resolved); setAppearanceIssue(null);
    } else setAppearanceIssue(issueOf(appearance.reason));
    if (launch.status === "fulfilled") {
      setSavedLaunch(launch.value); if (launchEdits.current === launchRevision) setLaunchDraft(launch.value); setLaunchIssue(null);
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
      const saved = await responseData<UserSettingsDto>(putUserSettingsApiSettingsUserPut({ client: hubClient, body: { ...current, ...preferences } }));
      const resolved = resolveAppearance(saved); setUserSettings(saved); setSavedAppearance(resolved);
      if (appearanceEdits.current === revision) setPreferences(resolved);
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
  const descriptions: Record<AppId, CopyKey> = { monkeyarch: "arch", monkeydiagram: "diagram", monkeymonitor: "monitor", monkeyboard: "board" };
  const fallback: AppStatus[] = (["monkeyarch", "monkeydiagram", "monkeymonitor", "monkeyboard"] as const).map((appId) => ({ appId, title: { monkeyarch: "MonkeyArch", monkeydiagram: "MonkeyDiagram", monkeymonitor: "MonkeyMonitor", monkeyboard: "MonkeyBoard" }[appId], serviceId: appId === "monkeymonitor" ? "monitor" : "studio", available: false, state: "stopped" }));
  return <><header className="toolbar"><strong className="wordmark">MonkeyHub</strong><span className={`connection ${connected ? "online" : ""}`} role="status">{connected ? t("connected") : statusIssue ? t("disconnected") : t("connecting")}</span><button className="btn" type="button" onClick={() => { void refreshApps(); if (savedLaunch === null || savedAppearance === null) void readSettings(); }}>{t("refresh")}</button><a className="btn" href="#settings">{t("settings")}</a></header>
    <main className="hub"><div className="section-heading"><h1>{t("apps")}</h1><span>{savedLaunch?.projectDir ?? t("noProject")}</span></div>
      <ErrorMessage issue={statusIssue} language={preferences.language} />
      <div className="apps">{(apps ?? fallback).map((app) => {
        const pending = busyServices.has(app.serviceId) || app.state === "starting" || app.state === "stopping";
        const unavailable = app.state === "unavailable";
        return <article className="app-card" key={app.appId} data-app={app.appId}><div className="app-heading"><h2>{app.title}</h2><span className={`app-state state-${app.state}`}>{t(app.state)}</span></div><p>{t(descriptions[app.appId])}</p><div className="actions">{app.state === "running" && app.url ? <><a className="btn btn--primary" href={applicationUrl(app.url, preferences)} target="_blank" rel="noopener noreferrer">{t("open")}</a><button className="btn" type="button" disabled={!connected || pending} onClick={() => void act(app)}>{pending ? t("working") : t(app.serviceId === "studio" ? "stopShared" : "stop")}</button></> : <button className="btn" type="button" disabled={!connected || unavailable || !app.available || pending} onClick={() => void act(app)}>{unavailable ? t("unavailable") : pending ? t(app.state === "stopping" ? "stopping" : "starting") : t("start")}</button>}</div><ErrorMessage issue={actionIssues[app.appId] ?? app.error ?? null} language={preferences.language} /></article>;
      })}</div><p className="shared-service-note">{t("shared")}</p>
      <section id="settings" className="settings"><div className="settings-section"><h2>{t("launch")}</h2><p className="help">{t("projectHelp")}</p><label htmlFor="project-dir">{t("project")}<input id="project-dir" value={launchDraft.projectDir ?? ""} placeholder={t("projectPlaceholder")} onChange={(event) => changeLaunch({ projectDir: event.target.value || null })} /></label><details className="advanced"><summary>{t("advanced")}</summary><div className="form-grid"><label>{t("reference")}<input id="reference-run" value={launchDraft.referenceRun ?? ""} onChange={(event) => changeLaunch({ referenceRun: event.target.value || null })} /></label><label>{t("cad")}<select id="cad-export" value={launchDraft.cadExport} onChange={(event) => changeLaunch({ cadExport: event.target.value as ApplicationSettingsDto["cadExport"] })}><option value="occt">{t("occt")}</option><option value="rhino">{t("rhino")}</option><option value="off">{t("off")}</option></select></label><label>{t("studioPort")}<input id="studio-port" type="number" min="1024" max="65535" value={launchDraft.studioPort} onChange={(event) => changeLaunch({ studioPort: Number(event.target.value) })} /></label><label>{t("monitorPort")}<input id="monitor-port" type="number" min="1024" max="65535" value={launchDraft.monitorPort} onChange={(event) => changeLaunch({ monitorPort: Number(event.target.value) })} /></label></div><p className="help">{t("launchHelp")}</p></details><ErrorMessage issue={launchIssue} language={preferences.language} /><div className="save-row"><button id="save-launch" className="btn" type="button" disabled={savingLaunch || !launchDirty} onClick={() => void saveLaunch()}>{savingLaunch ? t("saving") : t("saveLaunch")}</button><span role="status">{savedLaunch !== null ? launchDirty ? t("unsaved") : t("saved") : ""}</span></div></div>
        <div className="settings-section"><h2>{t("appearance")}</h2><div className="appearance-fields"><label>{t("language")}<select id="language" value={preferences.language} onChange={(event) => changeAppearance({ language: event.target.value as Language })}><option value="zh-CN">简体中文</option><option value="en">English</option></select></label><label>{t("theme")}<select id="theme" value={preferences.theme} onChange={(event) => changeAppearance({ theme: event.target.value as AppearancePreferences["theme"] })}><option value="system">{t("system")}</option><option value="dark">{t("dark")}</option><option value="light">{t("light")}</option></select></label><label>{t("size")}<select id="font-scale" value={preferences.fontScale} onChange={(event) => changeAppearance({ fontScale: Number(event.target.value) as AppearancePreferences["fontScale"] })}><option value="0.9">{t("compact")}</option><option value="1">{t("normal")}</option><option value="1.1">{t("large")}</option></select></label></div><p className="help">{t("appearanceHelp")}</p><ErrorMessage issue={appearanceIssue} language={preferences.language} /><div className="save-row"><button id="save-appearance" className="btn" type="button" disabled={savingAppearance || userSettings === null || !appearanceDirty} onClick={() => void saveAppearance()}>{savingAppearance ? t("saving") : t("saveAppearance")}</button><span role="status">{savedAppearance !== null ? appearanceDirty ? t("unsaved") : t("saved") : ""}</span></div></div>
      </section><footer>{t("closeNote")}</footer>
    </main></>;
}
const root = document.getElementById("root");
if (!root) throw new Error("MonkeyHub root is missing.");
createRoot(root).render(<StrictMode><App /></StrictMode>);
