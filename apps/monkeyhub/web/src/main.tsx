import { useT } from "../workspaces/src/i18n/useT";
import { UserPreferencesProvider, usePreferences } from "../workspaces/src/features/settings/preferences";
import { StrictMode, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { appearanceFromSearch, applyAppearance, DEFAULT_APPEARANCE, resolveAppearance, type AppearancePreferences, type Language } from "../../../shared-web/src/appearance.js";
import { translateMessage } from "../../../shared-web/src/i18n.js";
import { prepareEnglishToChinese, translateEnglishToChinese } from "../../../shared-web/src/browserTranslator.js";
import { createClient } from "./api/generated/client";
import { applicationSettingsApiSettingsAppsGet, chatProviderLoginApiChatProvidersProviderIdLoginPost, chatProvidersApiChatProvidersGet, checkGeminiApiCredentialsGeminiCheckPost, clearCredentialApiCredentialsCredentialIdDelete, listCredentialsApiCredentialsGet, openProviderLinkApiLinksLinkIdOpenPost, saveCredentialApiCredentialsCredentialIdPut, chatWorkspaceApiChatWorkspaceGet, getUserSettingsApiSettingsUserGet, listAppsApiAppsGet, putUserSettingsApiSettingsUserPut, startAppApiAppsAppIdStartPost, stopAppApiAppsAppIdStopPost, updateApplicationSettingsApiSettingsAppsPut, type AppStatus, type ApplicationSettingsDto, type ChatProvider, type ChatWorkspace, type CredentialCheck, type CredentialStatus, type UserSettingsDto } from "./api/generated";
import "./styles.css";
import { FabPage } from "./FabPage";
import { ChatShell, type HubSettings } from "./ChatShell";
import { AttentionHost } from "./notifications/AttentionHost";

type AppId = AppStatus["appId"];
type Issue = { code: string; detail: string };
type FieldIssue = Issue & { field: "workspace-dir" | "studio-port" | "render-timeout" | "coding-plan-url" };
const hubClient = createClient({ baseUrl: window.location.origin });
const initialLaunch: ApplicationSettingsDto = { projectDir: null, referenceRun: null, cadExport: "occt", studioPort: 8789, monitorPort: 8788 };
type ChatDefaults = { chatProvider: UserSettingsDto["chatProvider"]; chatModel: string | null; codingPlanBaseUrl: string | null };
const NO_CHAT_DEFAULTS: ChatDefaults = { chatProvider: null, chatModel: null, codingPlanBaseUrl: null };
const chatDefaults = (settings: UserSettingsDto): ChatDefaults => ({ chatProvider: settings.chatProvider ?? null,
  chatModel: settings.chatModel ?? null, codingPlanBaseUrl: settings.codingPlanBaseUrl ?? null });
type RenderDefaults = Pick<UserSettingsDto, "renderProvider" | "renderModel" | "renderTimeoutS">;
const renderDefaults = (settings?: UserSettingsDto): RenderDefaults => ({ renderProvider: settings?.renderProvider ?? "off",
  renderModel: settings?.renderModel ?? null, renderTimeoutS: settings?.renderTimeoutS ?? null });
import { hubCopyCatalog as copy } from "./i18n/catalogs";
type CopyKey = keyof typeof copy.en;
// #328: the settings pages and the interface styles a person can try. Their words stay
// here while the i18n catalogs are held by another lane.
const UI_STYLES = ["classic", "quiet", "titleblock", "night"] as const;
const SETTINGS_WORDS = {
  "zh-CN": {
    display: "显示", chat: "对话", render: "AI 渲染", workspace: "工作区",
    connections: "连接", connectionsHint: "重新读取已安装的命令行工具和它们的模型列表。",
    uiStyle: "界面风格", uiStyleHint: "随时切换对比。只改变界面的画法，不改变项目内容。",
    styles: { classic: "经典", quiet: "静默仪表", titleblock: "图签", night: "夜航" },
    notes: { classic: "原来的样子", quiet: "统一栏高、细线分区、等宽数字", titleblock: "图纸图签的暖灰与方角", night: "深色高对比，只适合演示" },
  },
  en: {
    display: "Display", chat: "Conversations", render: "AI Render", workspace: "Workspace",
    connections: "Connections", connectionsHint: "Read the installed CLIs and their model lists again.",
    uiStyle: "Interface style", uiStyleHint: "Switch any time to compare. It changes how the interface is drawn, never the project.",
    styles: { classic: "Classic", quiet: "Quiet instrument", titleblock: "Title block", night: "Night flight" },
    notes: { classic: "The original look", quiet: "One bar height, ruled sections, even figures", titleblock: "Title-block greys and square corners", night: "Dark and high-contrast, for demos only" },
  },
} as const;
// #334: where each key and sign-in goes. Local while the catalogs are held by another lane.
type CredentialId = CredentialStatus["id"];
type LinkId = "gemini-keys" | "zhipu-keys" | "moonshot-keys" | "deepseek-keys" | "bailian-keys";
const PLAN_PRESETS: readonly { id: string; label: Record<Language, string>; url: string; link: LinkId }[] = [
  // Endpoints from each vendor's own Claude Code guide (checked 2026-09-26); anything else is "Other".
  { id: "zhipu", label: { "zh-CN": "智谱 GLM Coding Plan", en: "Zhipu GLM Coding Plan" }, url: "https://open.bigmodel.cn/api/anthropic", link: "zhipu-keys" },
  { id: "moonshot", label: { "zh-CN": "Kimi（月之暗面）", en: "Kimi (Moonshot)" }, url: "https://api.moonshot.cn/anthropic", link: "moonshot-keys" },
  { id: "deepseek", label: { "zh-CN": "DeepSeek", en: "DeepSeek" }, url: "https://api.deepseek.com/anthropic", link: "deepseek-keys" },
  { id: "bailian", label: { "zh-CN": "阿里云百炼 Coding Plan", en: "Alibaba Bailian Coding Plan" }, url: "https://coding.dashscope.aliyuncs.com/apps/anthropic", link: "bailian-keys" },
];
const KEY_WORDS = {
  "zh-CN": {
    renderIntro: "选 Gemini，填好受支持的图像模型和 API 密钥，下次打开项目 Runtime 就能渲染，不用重启 Hub。供应商、模型和超时也从下次打开起生效。",
    geminiKey: "Gemini API 密钥", getKey: "获取密钥", check: "检查", clear: "清除",
    keyHint: "粘贴后按回车或离开输入框即保存；保存后不再显示。",
    notSet: "未设置。", reading: "正在读取…", fromStore: "已保存在本机的 Windows 凭据管理器，只属于当前账户。",
    fromEnvironment: (name: string) => `正在使用环境变量 ${name} 里的密钥；去掉这个变量后，这里保存的密钥才生效。`,
    fromClaude: "正在使用 Claude CLI 自己配置里的端点和令牌（环境变量或 ~/.claude/settings.json）。",
    noStore: (name: string) => `这台电脑没有可用的凭据存储；请用环境变量 ${name} 提供。`,
    keySaved: "已保存。", keyCleared: "已清除。", replace: "粘贴新密钥以替换",
    checks: { checking: "正在检查…", accepted: "Google 接受了这个密钥。", rejected: "Google 拒绝了这个密钥：请确认它是在 AI Studio 创建的，且没有被停用或限制。", unreachable: "连不上 Google：请检查网络或代理。", missing: "还没有可检查的密钥。" },
    signIn: "登录", signInAgain: "重新登录", notInstalled: "未安装", signedIn: "已登录", signedOut: "未登录", unknown: "登录状态未知",
    signInHint: "登录在单独的窗口里进行，完成后回到这里会自动重新检测。", signInOpened: "登录窗口已打开，完成后回到这里。",
    signInSection: "登录", planSection: "Coding Plan",
    planIntro: "让 Claude Code 连接兼容 Anthropic 接口的服务，比如各家的 Coding Plan。填好端点和令牌后，新对话就能选 Coding Plan。",
    planProvider: "服务商", planCustom: "其他（手动填写）", planUrl: "端点地址", planToken: "令牌", getToken: "获取令牌",
  },
  en: {
    renderIntro: "Choose Gemini, name a supported image model and enter an API key: the next project Runtime you open can render, with no Hub restart. Provider, model and timeout also apply from the next Runtime.",
    geminiKey: "Gemini API key", getKey: "Get a key", check: "Check", clear: "Clear",
    keyHint: "Paste it, then press Enter or leave the field to save. It is not shown again.",
    notSet: "Not set.", reading: "Reading…", fromStore: "Saved in this computer's Windows Credential Manager, for this account only.",
    fromEnvironment: (name: string) => `Using the key in the environment variable ${name}; a key saved here applies once that variable is gone.`,
    fromClaude: "Using the endpoint and token in the Claude CLI's own configuration (environment or ~/.claude/settings.json).",
    noStore: (name: string) => `This computer has no credential store to use; provide the key in the environment variable ${name}.`,
    keySaved: "Saved.", keyCleared: "Cleared.", replace: "Paste a new key to replace it",
    checks: { checking: "Checking…", accepted: "Google accepted this key.", rejected: "Google refused this key: check that it was created in AI Studio and is not disabled or restricted.", unreachable: "Google could not be reached: check the network or proxy.", missing: "There is no key to check yet." },
    signIn: "Sign in", signInAgain: "Sign in again", notInstalled: "Not installed", signedIn: "Signed in", signedOut: "Signed out", unknown: "Sign-in state unknown",
    signInHint: "Sign-in runs in a window of its own; coming back here checks again.", signInOpened: "The sign-in window is open. Come back here when it is done.",
    signInSection: "Sign-in", planSection: "Coding Plan",
    planIntro: "Connect Claude Code to an Anthropic-compatible service, such as a provider's Coding Plan. With an endpoint and a token here, new conversations can use Coding Plan.",
    planProvider: "Provider", planCustom: "Other (enter it yourself)", planUrl: "Endpoint", planToken: "Token", getToken: "Get a token",
  },
} as const;
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
  CREDENTIAL_INVALID: "密钥只能是 8–1024 个可见 ASCII 字符，不含空格、引号或换行。", CREDENTIAL_REQUEST_INVALID: "密钥请求无效。",
  CREDENTIAL_STORE_UNAVAILABLE: "这台电脑没有可用的凭据存储，请通过环境变量提供密钥。", CREDENTIAL_STORE_FAILED: "凭据管理器没有保存或移除这个密钥，请重试。",
  CHAT_PROVIDER_MISSING: "这台电脑上没有安装对应的命令行工具。", CHAT_LOGIN_UNSUPPORTED: "请在终端里运行对应命令行工具的登录。",
  LINK_UNAVAILABLE: "无法从这里打开浏览器。", CODING_PLAN_URL_INVALID: "端点需要是完整的 http(s) 地址，不含查询参数、片段或用户名。",
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
function planProblem(draft: ChatDefaults): FieldIssue | null {
  const url = draft.codingPlanBaseUrl;
  return url && !/^https?:\/\/[^\s?#@]+$/.test(url)
    ? { field: "coding-plan-url", code: "CODING_PLAN_URL_INVALID", detail: "The Coding Plan endpoint is a full http(s) address, without a query, a fragment or a user name." } : null;
}
/** #334: one provider key. It goes in on Enter or when the field is left, and never comes back. */
function CredentialField({ id, inputId, title, status, language, link, linkLabel, placeholder, variable, onChanged, children }: {
  id: CredentialId; inputId: string; title: string; status: CredentialStatus | undefined; language: Language;
  link?: LinkId; linkLabel?: string; placeholder: string; variable: string; onChanged: (status: CredentialStatus) => void; children?: ReactNode;
}) {
  const words = KEY_WORDS[language];
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [issue, setIssue] = useState<Issue | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const save = async () => {
    if (!draft.trim() || busy) return;
    setBusy(true); setIssue(null); setNote(null);
    try {
      onChanged(await responseData<CredentialStatus>(saveCredentialApiCredentialsCredentialIdPut({ client: hubClient, path: { credential_id: id }, body: { key: draft } })));
      setDraft(""); setNote(words.keySaved);
    } catch (cause) { setIssue(issueOf(cause)); }
    finally { setBusy(false); }
  };
  const clear = async () => {
    setBusy(true); setIssue(null); setNote(null);
    try { onChanged(await responseData<CredentialStatus>(clearCredentialApiCredentialsCredentialIdDelete({ client: hubClient, path: { credential_id: id } }))); setNote(words.keyCleared); }
    catch (cause) { setIssue(issueOf(cause)); }
    finally { setBusy(false); }
  };
  const openLink = async () => {
    if (!link) return;
    try { await responseData(openProviderLinkApiLinksLinkIdOpenPost({ client: hubClient, path: { link_id: link } })); }
    catch (cause) { setIssue(issueOf(cause)); }
  };
  const where = !status ? words.reading : status.source === "environment" ? words.fromEnvironment(status.variable ?? variable)
    : status.source === "saved" ? words.fromStore : status.source === "claude-config" ? words.fromClaude
    : status.storeAvailable === false ? words.noStore(variable) : words.notSet;
  return <div className="settings-row settings-row--wide settings-key">
    <div className="settings-row__text"><label htmlFor={inputId}>{title}</label><p className="settings-row__hint" id={`${inputId}-state`}>{where}</p></div>
    <div className="settings-key__controls">
      <input id={inputId} type="password" autoComplete="off" spellCheck={false} value={draft} disabled={busy || status?.storeAvailable === false}
        placeholder={status?.saved ? words.replace : placeholder} aria-describedby={`${inputId}-state ${inputId}-hint`}
        onChange={(event) => { setDraft(event.target.value); setNote(null); }} onBlur={() => void save()}
        onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void save(); } }} />
      {children}
      {status?.saved && <button type="button" className="btn" disabled={busy} onClick={() => void clear()}>{words.clear}</button>}
      {link && <button type="button" className="btn btn--link" onClick={() => void openLink()}>{linkLabel ?? words.getKey} ↗</button>}
    </div>
    <p className="settings-row__hint" id={`${inputId}-hint`} role="status">{note ?? words.keyHint}</p>
    <ErrorMessage issue={issue} language={language} />
  </div>;
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
  // #334: which keys are in use and where from; never the keys.
  const [credentialRows, setCredentialRows] = useState<Partial<Record<CredentialId, CredentialStatus>>>({});
  const [geminiCheck, setGeminiCheck] = useState<CredentialCheck["result"] | "checking" | null>(null);
  const [signInOpened, setSignInOpened] = useState(false);
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
      responseData<CredentialStatus[]>(listCredentialsApiCredentialsGet({ client: hubClient })),
    ]);
    const [appearance, launch, connections, workspaceRead, keys] = results;
    if (keys.status === "fulfilled") setCredentialRows(Object.fromEntries(keys.value.map((row) => [row.id, row])));
    if (appearance.status === "fulfilled") {
      setUserSettings(appearance.value); const resolved = resolveAppearance(appearance.value); setSavedAppearance(resolved);
      const savedChat = chatDefaults(appearance.value);
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
  // #334: once a sign-in window has opened, coming back to the Hub checks the CLIs again.
  useEffect(() => {
    if (!signInOpened) return;
    const back = () => { setSignInOpened(false); void readSettings(true); };
    window.addEventListener("focus", back);
    return () => window.removeEventListener("focus", back);
  }, [signInOpened, readSettings]);
  // The same requests the Save buttons made, now made by each edit.
  const appearanceSave = useAutosave(async () => {
    if (userSettings === null || !appearanceDirty || renderProblem(renderDraft) || planProblem(chatDraft)) return;
    const revision = appearanceEdits.current; setAppearanceIssue(null);
    try {
      const current = await responseData<UserSettingsDto>(getUserSettingsApiSettingsUserGet({ client: hubClient }));
      const saved = await responseData<UserSettingsDto>(putUserSettingsApiSettingsUserPut({ client: hubClient, body: { ...current, ...preferences, ...chatDraft, ...renderDraft } }));
      const resolved = resolveAppearance(saved); setUserSettings(saved); setSavedAppearance(resolved);
      const savedChat = chatDefaults(saved);
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
  // #300: every Hub view carries the notices; a view framed in another Hub page leaves them to that page.
  const attention = <AttentionHost language={preferences.language} />;
  if (fabView) return <><FabPage preferences={preferences} client={hubClient} readResult={responseData} />{attention}</>;
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
  const planInvalid = !appearanceSave.pending && appearanceDirty ? planProblem(chatDraft) : null;
  const keyWords = KEY_WORDS[preferences.language];
  const keyChanged = (row: CredentialStatus) => { setCredentialRows((current) => ({ ...current, [row.id]: row })); setGeminiCheck(null); void readSettings(true); };
  const checkGemini = async () => {
    setGeminiCheck("checking");
    try { setGeminiCheck((await responseData<CredentialCheck>(checkGeminiApiCredentialsGeminiCheckPost({ client: hubClient }))).result); }
    catch { setGeminiCheck("unreachable"); }
  };
  const signIn = async (provider: "codex" | "claude") => {
    try { await responseData(chatProviderLoginApiChatProvidersProviderIdLoginPost({ client: hubClient, path: { provider_id: provider } })); setSignInOpened(true); }
    catch (cause) { setAppearanceIssue(issueOf(cause)); }
  };
  const planPreset = PLAN_PRESETS.find((preset) => preset.url === chatDraft.codingPlanBaseUrl);
  const cliRow = (provider: "codex" | "claude", name: string) => {
    const row = chatProviders.find((item) => item.id === provider);
    const state = !chatProviders.length ? keyWords.reading : !row?.installed ? keyWords.notInstalled
      : row.signedIn === true ? keyWords.signedIn : row.signedIn === false ? keyWords.signedOut : keyWords.unknown;
    return <div className="settings-row" key={provider}><div className="settings-row__text"><span className="settings-row__title">{name}</span>
      <p className="settings-row__hint" id={`sign-in-${provider}`}>{state}</p></div>
      <button type="button" className="btn" aria-describedby={`sign-in-${provider}`} disabled={!row?.installed} onClick={() => void signIn(provider)}>
        {row?.signedIn ? keyWords.signInAgain : keyWords.signIn}</button></div>;
  };
  // #328: Settings in pages, each a list of rows: what a setting is on the left, its control on the right.
  const words = SETTINGS_WORDS[preferences.language];
  const uiStyle = preferences.uiStyle ?? "classic";
  const settings: HubSettings = {
    updateLabel: t("softwareUpdate"),
    notice: <><ErrorMessage issue={statusIssue} language={preferences.language} /><ErrorMessage issue={appearanceIssue} language={preferences.language} /></>,
    status: <span id="settings-save-state" role="status" data-state={settingsSaving ? "saving" : appearanceDirty || launchDirty ? "unsaved" : "saved"}>
      {settingsSaving ? t("saving") : appearanceDirty || launchDirty ? t("unsaved") : savedAppearance !== null ? t("saved") : ""}</span>,
    pages: [{ id: "display", label: words.display, icon: "display", body: <section id="settings" ref={settingsRef}>
      <h2>{t("appearance")}</h2>
      <div className="settings-row"><label htmlFor="language">{t("language")}</label>
        <select id="language" value={preferences.language} onChange={(event) => changeAppearance({ language: event.target.value as Language })}><option value="zh-CN">简体中文</option><option value="en">English</option></select></div>
      <div className="settings-row"><label htmlFor="theme">{t("theme")}</label>
        <select id="theme" value={preferences.theme} onChange={(event) => changeAppearance({ theme: event.target.value as AppearancePreferences["theme"] })}><option value="system">{t("system")}</option><option value="dark">{t("dark")}</option><option value="light">{t("light")}</option></select></div>
      <div className="settings-row"><label htmlFor="font-scale">{t("size")}</label>
        <select id="font-scale" value={preferences.fontScale} onChange={(event) => changeAppearance({ fontScale: Number(event.target.value) as AppearancePreferences["fontScale"] })}><option value="0.9">{t("compact")}</option><option value="1">{t("normal")}</option><option value="1.1">{t("large")}</option></select></div>
      <div className="settings-row settings-row--wide" role="radiogroup" aria-labelledby="ui-style-label" aria-describedby="ui-style-hint">
        <div className="settings-row__text"><span className="settings-row__title" id="ui-style-label">{words.uiStyle}</span><p className="settings-row__hint" id="ui-style-hint">{words.uiStyleHint}</p></div>
        <div className="style-cards">{UI_STYLES.map((style) => <label key={style} className="style-card" data-style={style}>
          <input type="radio" name="ui-style" value={style} checked={uiStyle === style} onChange={() => changeAppearance({ uiStyle: style })} />
          {/* A small drawing of the shell in that style: sidebar, header, rows and a result sheet. */}
          <span className="style-card__preview" aria-hidden="true"><i className="style-card__side" /><i className="style-card__head" /><i className="style-card__row" /><i className="style-card__row style-card__row--current" /><i className="style-card__sheet" /></span>
          <span className="style-card__name">{words.styles[style]}</span><small className="style-card__note">{words.notes[style]}</small>
        </label>)}</div>
      </div>
    </section> }, { id: "chat", label: words.chat, icon: "chat", body: <>
      <h2>{t("chatDefaults")}</h2>
      <p className="settings-intro">{t("chatDefaultsHelp")}</p>
      <div className="settings-row"><div className="settings-row__text"><label htmlFor="default-chat-provider">{t("chatProvider")}</label>
        <p className="settings-row__hint" id="connection-state">{selectedConnection
          ? <>{selectedConnection.label} · {connectionWords(selectedConnection)}{catalogWords(selectedConnection) ? ` · ${catalogWords(selectedConnection)}` : ""}</>
          : t("checking")}</p></div>
        <select id="default-chat-provider" aria-describedby="connection-state" value={chatDraft.chatProvider ?? ""} onChange={(event) => { editDefaults(0); setDefaultModelCustom(null); setChatDraft((current) => ({ ...current, chatProvider: (event.target.value || null) as ChatDefaults["chatProvider"], chatModel: null })); }}>
          <option value="">{t("cliUnset")}</option>
          {(chatProviders.length ? chatProviders : [{ id: "codex", label: "Codex CLI", available: true, detail: "" }] as readonly ChatProvider[]).map((item) => <option key={item.id} value={item.id} disabled={!item.available}>{item.label}{connectionState(item)}</option>)}
        </select></div>
      <div className="settings-row"><label htmlFor="default-chat-model">{t("chatModel")}</label>
        <select id="default-chat-model" value={defaultModelCustom !== null ? "__custom__" : chatDraft.chatModel ?? ""} onChange={(event) => {
          if (event.target.value === "__custom__") { setDefaultModelCustom(chatDraft.chatModel ?? ""); return; }
          editDefaults(0); setDefaultModelCustom(null); setChatDraft((current) => ({ ...current, chatModel: event.target.value || null }));
        }}>
          <option value="">{t("cliDefault")}</option>
          {defaultModelOptions.map((item) => <option key={item} value={item}>{item}</option>)}
          <option value="__custom__">{t("customModel")}</option>
        </select></div>
      {defaultModelCustom !== null && <div className="settings-row"><label htmlFor="default-chat-model-custom">{t("customModel")}</label>
        <input id="default-chat-model-custom" autoFocus value={defaultModelCustom}
          onChange={(event) => { editDefaults(TYPING_PAUSE_MS); setDefaultModelCustom(event.target.value); setChatDraft((current) => ({ ...current, chatModel: event.target.value.trim() || null })); }} /></div>}
      <h3 className="settings-subhead">{keyWords.signInSection}</h3>
      <p className="settings-intro">{signInOpened ? keyWords.signInOpened : keyWords.signInHint}</p>
      {cliRow("codex", "Codex")}
      {cliRow("claude", "Claude Code")}
      <h3 className="settings-subhead">{keyWords.planSection}</h3>
      <p className="settings-intro">{keyWords.planIntro}</p>
      <div className="settings-row"><label htmlFor="coding-plan-provider">{keyWords.planProvider}</label>
        <select id="coding-plan-provider" value={planPreset?.id ?? "custom"} onChange={(event) => {
          const preset = PLAN_PRESETS.find((item) => item.id === event.target.value);
          if (!preset) return;
          editDefaults(0); setChatDraft((current) => ({ ...current, codingPlanBaseUrl: preset.url }));
        }}>
          {PLAN_PRESETS.map((preset) => <option key={preset.id} value={preset.id}>{preset.label[preferences.language]}</option>)}
          <option value="custom">{keyWords.planCustom}</option>
        </select></div>
      <div className="settings-row"><label htmlFor="coding-plan-url">{keyWords.planUrl}</label>
        <input id="coding-plan-url" type="url" value={chatDraft.codingPlanBaseUrl ?? ""} placeholder="https://…/anthropic" spellCheck={false}
          aria-invalid={planInvalid ? true : undefined} onChange={(event) => {
            editDefaults(TYPING_PAUSE_MS); setChatDraft((current) => ({ ...current, codingPlanBaseUrl: event.target.value.trim() || null }));
          }} /></div>
      <ErrorMessage issue={planInvalid} language={preferences.language} />
      <CredentialField id="coding-plan" inputId="coding-plan-token" title={keyWords.planToken} status={credentialRows["coding-plan"]}
        language={preferences.language} link={planPreset?.link} linkLabel={keyWords.getToken} placeholder="sk-…"
        variable="ANTHROPIC_AUTH_TOKEN" onChanged={keyChanged} />
      <div className="settings-row"><div className="settings-row__text"><span className="settings-row__title">{words.connections}</span><p className="settings-row__hint">{words.connectionsHint}</p></div>
        <button id="recheck-connections" className="btn" type="button" disabled={!connected} onClick={() => void readSettings(true)}>{t("recheck")}</button></div>
    </> }, { id: "render", label: words.render, icon: "render", body: <>
      <h2>{t("renderSettings")}</h2>
      <p className="settings-intro">{keyWords.renderIntro}</p>
      <div className="settings-row"><label htmlFor="render-provider">{t("renderProvider")}</label>
        <select id="render-provider" value={renderDraft.renderProvider ?? "off"} onChange={(event) => {
          editDefaults(0); setRenderDraft((value) => ({ ...value, renderProvider: event.target.value as RenderDefaults["renderProvider"] }));
        }}><option value="off">{t("renderOff")}</option><option value="gemini">Gemini</option></select></div>
      <div className="settings-row"><label htmlFor="render-model">{t("renderModel")}</label>
        <input id="render-model" value={renderDraft.renderModel ?? ""} placeholder="gemini-3.1-flash-image" onChange={(event) => {
          editDefaults(TYPING_PAUSE_MS); setRenderDraft((value) => ({ ...value, renderModel: event.target.value.trim() || null }));
        }} /></div>
      <div className="settings-row"><label htmlFor="render-timeout">{t("renderTimeout")}</label>
        <input id="render-timeout" type="number" min="1" max="300" value={renderDraft.renderTimeoutS ?? ""} placeholder={t("runtimeDefault")}
          aria-invalid={renderInvalid ? true : undefined} onChange={(event) => {
          editDefaults(TYPING_PAUSE_MS); setRenderDraft((value) => ({ ...value, renderTimeoutS: event.target.value === "" ? null : Number(event.target.value) }));
        }} /></div>
      <CredentialField id="gemini" inputId="gemini-key" title={keyWords.geminiKey} status={credentialRows.gemini}
        language={preferences.language} link="gemini-keys" placeholder="AIza…" variable="MONKEYHUB_RENDER_API_KEY" onChanged={keyChanged}>
        {credentialRows.gemini?.configured && <button type="button" className="btn" disabled={geminiCheck === "checking"} onClick={() => void checkGemini()}>{keyWords.check}</button>}
      </CredentialField>
      {geminiCheck && <p className="settings-row__hint settings-key__check" data-result={geminiCheck} role="status">{keyWords.checks[geminiCheck]}</p>}
      <ErrorMessage issue={renderInvalid} language={preferences.language} />
    </> }, { id: "workspace", label: words.workspace, icon: "folder", body: <>
      <h2>{t("workspace")}</h2>
      <div className="settings-row settings-row--wide"><div className="settings-row__text"><label htmlFor="workspace-dir">{t("workspaceDir")}</label><p className="settings-row__hint">{t("workspaceHelp")}</p></div>
        <input id="workspace-dir" value={launchDraft.workspaceDir ?? ""} placeholder={workspace?.workspaceDir ?? ""}
          aria-invalid={launchInvalid?.field === "workspace-dir" ? true : undefined}
          onChange={(event) => changeLaunch({ workspaceDir: event.target.value || null })} /></div>
      <details className="advanced"><summary>{t("advanced")}</summary>
        <WorkspaceDiagnosticsSettings />
        <div className="chat-service-settings">{(apps ?? []).filter((app) => app.appId === "monkeyarch").map((app) => <div key={app.appId}><span>Project Runtime · {t(app.state)}</span><button className="btn" disabled={!connected || busyServices.has(app.serviceId) || app.state === "starting" || app.state === "stopping"} onClick={() => void act(app)}>{t(app.state === "running" ? "stop" : "start")}</button><ErrorMessage issue={actionIssues[app.appId] ?? app.error ?? null} language={preferences.language} /></div>)}</div>
        <div className="settings-row"><label htmlFor="reference-run">{t("reference")}</label><input id="reference-run" value={launchDraft.referenceRun ?? ""} onChange={(event) => changeLaunch({ referenceRun: event.target.value || null })} /></div>
        <div className="settings-row"><label htmlFor="cad-export">{t("cad")}</label><select id="cad-export" value={launchDraft.cadExport} onChange={(event) => changeLaunch({ cadExport: event.target.value as ApplicationSettingsDto["cadExport"] }, 0)}><option value="occt">{t("occt")}</option><option value="rhino">{t("rhino")}</option><option value="off">{t("off")}</option></select></div>
        <div className="settings-row"><label htmlFor="studio-port">{t("studioPort")}</label><input id="studio-port" type="number" min="1024" max="65535" value={launchDraft.studioPort}
          aria-invalid={launchInvalid?.field === "studio-port" ? true : undefined} onChange={(event) => changeLaunch({ studioPort: Number(event.target.value) })} /></div>
        <p className="help">{t("launchHelp")}</p>
      </details>
      <ErrorMessage issue={launchIssue ?? launchInvalid} language={preferences.language} />
    </> }],
  };
  return <UserPreferencesProvider appearance={preferences}><ChatShell preferences={preferences} configuredProject={savedLaunch?.projectDir ?? null} settings={settings}
    settingsDirty={appearanceDirty || launchDirty || settingsSaving || busyServices.size > 0}
    defaults={{ provider: savedChatDefaults.chatProvider ?? "codex", model: savedChatDefaults.chatModel }}
    workspace={workspace} apps={statusIssue ? null : apps} onAppearance={changeAppearance} />{attention}</UserPreferencesProvider>;
}
const root = document.getElementById("root");
if (!root) throw new Error("MonkeyHub root is missing.");
createRoot(root).render(<StrictMode><App /></StrictMode>);
