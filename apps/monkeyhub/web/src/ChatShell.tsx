import { useCallback, useEffect, useRef, useState, type CSSProperties, type FormEvent, type ReactNode } from "react";
import { applicationUrl, type AppearancePreferences } from "../../../shared-web/src/appearance.js";
import type { AppStatus, ChatArchiveRequest, ChatCreateRequest, ChatDetail, ChatPostRequest, ChatProject, ChatProvider, ChatSummary, ChatWorkspace, HubError } from "./api/generated";
import { readHostRequest, START_MODELING } from "../../../shared-web/src/hostBridge.js";
import { presentFailure } from "./chatError";
import "./ChatShell.css";

type AppId = AppStatus["appId"];
type Props = {
  preferences: AppearancePreferences;
  settings: ReactNode;
  configuredProject: string | null;
  /** The saved global defaults a *new* conversation starts with. */
  defaults: { provider: ChatCreateRequest["provider"]; model: string | null };
  /** Where a new project is created, as the Hub reports it. */
  workspace: ChatWorkspace | null;
  apps: readonly AppStatus[] | null;
};
type ToolTab = { id: AppId; url: string; revision: number };
type ProjectPreparation = { promise: Promise<AppStatus[]>; apps: AppStatus[] | null };
const VIEW_KEY = "monkeyhub.chat-view.v1";
/** The rail is always on screen; the conversation never shrinks past this. */
const RAIL_WIDTH = 76, RESIZER_WIDTH = 5, CHAT_MIN_WIDTH = 360;
const words = {
  "zh-CN": {
    projects: "项目", add: "添加项目", newChat: "新对话", settings: "Hub 设置", settingsHeading: "Hub 设置（全局）", close: "关闭", cancel: "取消", addProject: "添加项目",
    folder: "项目文件夹", folderHelp: "选择已有的 ArchFlow 项目，粘贴文件夹完整路径。", folderPlaceholder: "例如 D:\\projects\\my-project",
    empty: "从一个想法开始", emptyHint: "描述你想做的事。需要看模型或图纸时，在右侧打开工具。", noProject: "添加一个项目，开始对话",
    placeholder: "想在这个项目里做些什么？", send: "发送", stop: "停止", thinking: "正在处理", interrupted: "已中断", failed: "未完成",
    working: "正在连接项目…", tools: "项目工具", hideTools: "收起工具", model: "建模", diagram: "图纸", board: "画板", fab: "制作", monitor: "用量",
    refresh: "刷新页面", browser: "项目浏览器", modelName: "模型（留空使用 CLI 默认值）", defaultModel: "CLI 默认模型", provider: "执行连接",
    toolEmpty: "在这里查看项目", toolHint: "打开模型、图纸、画板或制作页面，聊天会一直保留。",
    loading: "正在读取…", toolActivity: "执行过程", retry: "重新读取", projectRequired: "先添加或选择一个项目。", expand: "展开项目栏", collapse: "收起项目栏",
    openCandidate: "在右侧打开这个候选", candidateHint: "查看此候选，聊天保留不变。", activityEmpty: "没有更多可显示的内容。",
    resize: "调整右侧面板宽度", noProvider: "尚无可用的 CLI 连接", emptyProjects: "你的项目会出现在这里。", sessionError: "无法读取对话", complete: "已完成",
    rail: "项目工具栏", showTools: "展开工具", projectInfo: "项目信息", projectNone: "尚未选择项目", projectNoneHint: "选择左侧的一个项目后，这里显示它的版本与 Stage。",
    path: "路径", version: "正式版本", versionNumber: (value: number) => `版本 ${value}`, versionUnknown: "未读取到版本", stage: "Stage", stageNone: "尚未确认 Stage",
    candidate: "本次对话的候选", candidateNone: "尚无候选", notEndorsed: "候选，未认可、未出图", connection: "当前连接", connectionHint: "在左下角的 Hub 设置里更改新对话的默认连接。",
    connectionDetails: "连接信息", toolUrl: "工具页面地址", connectionNone: "右侧尚未打开工具页面。",
    modelLabel: "模型", modelCliDefault: "CLI 默认模型", modelCustom: "自定义模型 ID…", modelCustomLabel: "模型 ID",
    modelChecking: "正在检测可用模型…", modelSaving: "正在切换…", modelRunning: "回复结束后可以更换模型。", modelApply: "使用",
    modelSince: "改动只影响这个对话之后的消息。",
    railWorkspaces: "工作区", railSystem: "系统", railProject: "此项目",
    errorDetails: "技术详情", changeModel: "更换模型",
    startingDraft: "帮我在这个项目里做一个体量方案：",
    newProject: "新建项目", newProjectHeading: "新建项目", projectName: "项目名称", projectNamePlaceholder: "例如 harbour-study",
    projectNameHelp: "用字母、数字、连字符或下划线；这个名称就是项目 id。", workspaceLabel: "工作区", createHere: "将创建于",
    workspaceChange: "在左下角的 Hub 设置里更改工作区。", create: "创建并开始对话", addExisting: "添加已有项目",
    creating: "正在创建…", projectAdded: "已添加",
    archivedChats: "已归档对话", activeChats: "返回当前对话", archive: "归档", restore: "恢复", archiveRunning: "回复结束或停止后可以归档。",
    archiveEmpty: "暂无已归档对话。", archivedNotice: "这段对话已归档，消息和候选仍保留。恢复后可以继续对话。", restoreChat: "恢复此对话",
    toolStopped: "按需启动", toolStarting: "启动中", toolRunning: "可用", toolStopping: "停止中", toolError: "连接失败", toolUnavailable: "缺少依赖", toolUnknown: "读取状态中", toolConnect: "连接此项目",
  },
  en: {
    projects: "Projects", add: "Add project", newChat: "New chat", settings: "Hub settings", settingsHeading: "Hub settings (global)", close: "Close", cancel: "Cancel", addProject: "Add project",
    folder: "Project folder", folderHelp: "Paste the full path to an existing ArchFlow project folder.", folderPlaceholder: "For example D:\\projects\\my-project",
    empty: "Start with an idea", emptyHint: "Describe what you want to do. Open project tools on the right when you need them.", noProject: "Add a project to start a conversation",
    placeholder: "What would you like to do in this project?", send: "Send", stop: "Stop", thinking: "Working", interrupted: "Interrupted", failed: "Incomplete",
    working: "Connecting the project…", tools: "Project tools", hideTools: "Hide tools", model: "Modeling", diagram: "Drawings", board: "Board", fab: "Fabrication", monitor: "Usage",
    refresh: "Reload page", browser: "Project browser", modelName: "Model (leave empty for CLI default)", defaultModel: "CLI default model", provider: "Connection",
    toolEmpty: "See the project here", toolHint: "Open the model, drawings, board or fabrication page while keeping your conversation.",
    loading: "Loading…", toolActivity: "Tool activity", retry: "Reload", projectRequired: "Add or choose a project first.", expand: "Show projects", collapse: "Hide projects",
    openCandidate: "Open this candidate on the right", candidateHint: "Look at this candidate; the conversation stays as it is.", activityEmpty: "Nothing further to show.",
    resize: "Resize right panel", noProvider: "No CLI connection is available", emptyProjects: "Your projects will appear here.", sessionError: "Could not read the conversation", complete: "Complete",
    rail: "Project tools", showTools: "Show tools", projectInfo: "Project", projectNone: "No project selected", projectNoneHint: "Choose a project on the left to see its version and Stage here.",
    path: "Folder", version: "Published version", versionNumber: (value: number) => `Version ${value}`, versionUnknown: "Version not read", stage: "Stage", stageNone: "No confirmed Stage",
    candidate: "This conversation's candidate", candidateNone: "No candidate yet", notEndorsed: "Candidate — not endorsed, not issued",
    connection: "Connection", connectionHint: "Change the default connection for new chats in Hub settings, bottom left.",
    connectionDetails: "Connection details", toolUrl: "Tool page address", connectionNone: "No tool page is open on the right.",
    modelLabel: "Model", modelCliDefault: "CLI default model", modelCustom: "Custom model id…", modelCustomLabel: "Model id",
    modelChecking: "Checking which models are available…", modelSaving: "Switching…", modelRunning: "The model can be changed once this reply finishes.", modelApply: "Use",
    modelSince: "This applies to the next messages in this conversation.",
    railWorkspaces: "Workspaces", railSystem: "System", railProject: "This project",
    errorDetails: "Technical details", changeModel: "Change the model",
    startingDraft: "Help me start a massing for this project: ",
    newProject: "New project", newProjectHeading: "New project", projectName: "Project name", projectNamePlaceholder: "for example harbour-study",
    projectNameHelp: "Letters, digits, hyphens or underscores; this name becomes the project id.", workspaceLabel: "Workspace", createHere: "Will be created at",
    workspaceChange: "Change the workspace in Hub settings, bottom left.", create: "Create and start chatting", addExisting: "Add an existing project",
    creating: "Creating…", projectAdded: "Added",
    archivedChats: "Archived chats", activeChats: "Back to current chats", archive: "Archive", restore: "Restore", archiveRunning: "Wait for the reply to finish or stop it before archiving.",
    archiveEmpty: "No archived chats.", archivedNotice: "This chat is archived. Its messages and candidates are kept. Restore it to continue.", restoreChat: "Restore this chat",
    toolStopped: "On demand", toolStarting: "Starting", toolRunning: "Ready", toolStopping: "Stopping", toolError: "Failed", toolUnavailable: "Unavailable", toolUnknown: "Checking", toolConnect: "Connect project",
  },
} as const;
/** The rail's two kinds of entry: places you work in this project, and the
    tools that report on the machine rather than on the design. */
const tools: { id: AppId; label: "model" | "diagram" | "board" | "fab" | "monitor"; icon: string; group: "workspace" | "system" }[] = [
  { id: "monkeyarch", label: "model", icon: "cube", group: "workspace" }, { id: "monkeydiagram", label: "diagram", icon: "file", group: "workspace" },
  { id: "monkeyboard", label: "board", icon: "board", group: "workspace" }, { id: "monkeyfab", label: "fab", icon: "fab", group: "workspace" },
  { id: "monkeymonitor", label: "monitor", icon: "chart", group: "system" },
];
const railGroups = [{ id: "workspace", caption: "railWorkspaces" }, { id: "system", caption: "railSystem" }] as const;

function Icon({ name }: { name: string }) {
  const paths: Record<string, ReactNode> = {
    plus: <path d="M12 5v14M5 12h14" />, panel: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M15 4v16" /></>,
    sidebar: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></>, close: <path d="m6 6 12 12M6 18 18 6" />,
    send: <path d="M12 19V5m-6 6 6-6 6 6" />, stop: <rect x="6" y="6" width="12" height="12" rx="2" />,
    folder: <path d="M3 6h7l2 2h9v11H3Z" />, chat: <path d="M4 4h16v13H9l-5 4Z" />,
    archive: <><path d="M4 8h16v13H4ZM3 3h18v5H3ZM9 12h6" /></>,
    restore: <><path d="M4 10a8 8 0 1 1 2 8M4 4v6h6" /></>,
    cube: <><path d="m12 3 9 5v9l-9 5-9-5V8Zm0 10 9-5M3 8l9 5v9M7.5 5.5l9 5" /></>,
    file: <><path d="M5 3h9l5 5v13H5ZM14 3v6h5M8 13h8M8 17h6" /></>,
    board: <><rect x="3" y="4" width="18" height="15" rx="2" /><path d="M7 8h4v5H7Zm7 0h3M14 12h3M8 22l4-3 4 3" /></>,
    fab: <><path d="M6 8V3h12v5M6 17H3V8h18v9h-3M6 13h12v8H6Z" /><path d="M17 10h1" /></>,
    chart: <><path d="M4 4v16h17M8 16v-4M13 16V7M18 16v-7" /></>,
    refresh: <path d="M20 10a8 8 0 1 0-2 8M20 4v6h-6" />,
    settings: <><path d="M4 7h16M4 17h16" /><circle cx="9" cy="7" r="3" /><circle cx="15" cy="17" r="3" /></>,
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] ?? paths.chat}</svg>;
}

async function request<T>(url: string, body?: unknown, method = body === undefined ? "GET" : "POST"): Promise<T> {
  const response = await fetch(url, { method, headers: body === undefined ? undefined : { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  const value = await response.json();
  // The API answers a failure as {code, detail}; both are carried, since the
  // code names the failure and the detail is what the failing thing said.
  if (!response.ok) throw Object.assign(new Error(typeof value.detail === "string" ? value.detail : `HTTP ${response.status}`), {
    status: response.status, failure: { code: typeof value.code === "string" ? value.code : `HTTP_${response.status}`, detail: typeof value.detail === "string" ? value.detail : JSON.stringify(value) } satisfies HubError,
  });
  return value as T;
}
/** Whatever went wrong, in the shape the rest of this page reads failures in. */
const asFailure = (cause: unknown): HubError => {
  const carried = cause && typeof cause === "object" ? (cause as { failure?: HubError }).failure : undefined;
  if (carried && typeof carried.code === "string") return carried;
  return { code: "HUB_REQUEST_FAILED", detail: cause instanceof Error ? cause.message : String(cause) };
};
const wait = () => new Promise((resolve) => window.setTimeout(resolve, 400));
/** What this page looked like last time: the same conversation and the same frame. */
function readView(): { chatId: string | null; projectDir: string | null; sidebar: boolean | null; panel: boolean | null; panelWidth: number | null } {
  const empty = { chatId: null, projectDir: null, sidebar: null, panel: null, panelWidth: null };
  try {
    const saved = JSON.parse(localStorage.getItem(VIEW_KEY) ?? "null");
    return {
      chatId: typeof saved?.chatId === "string" ? saved.chatId : null,
      projectDir: typeof saved?.projectDir === "string" ? saved.projectDir : null,
      sidebar: typeof saved?.sidebar === "boolean" ? saved.sidebar : null,
      panel: typeof saved?.panel === "boolean" ? saved.panel : null,
      panelWidth: typeof saved?.panelWidth === "number" && Number.isFinite(saved.panelWidth) ? saved.panelWidth : null,
    };
  } catch { return empty; }
}

/** One saved activity: its first line is the summary, the rest the diagnostics. */
const activityLine = (content: string) => content.split("\n")[0] ?? "";
const activityDetail = (content: string) => content.split("\n").slice(1).join("\n");

/** Render text and fenced code as React nodes; never execute model-supplied HTML. */
function MessageText({ text }: { text: string }) {
  return <>{text.split(/(```[\s\S]*?```)/g).filter(Boolean).map((part, index) => part.startsWith("```")
    ? <pre className="chat-code" key={index}><code>{part.replace(/^```[^\n]*\n?/, "").replace(/```$/, "")}</code></pre>
    : <div className="chat-prose" key={index}>{part}</div>)}</>;
}

/**
 * One failure, told twice: a line the reader can act on, and the original text
 * underneath for whoever has to diagnose it. Nothing here is ever applied to a
 * message — only to something that actually failed.
 */
function Failure({ failure, language, labels, connection, onClose, onChangeModel }: {
  failure: HubError; language: "en" | "zh-CN"; labels: { errorDetails: string; changeModel: string; close: string };
  /** What this conversation actually runs on, for a failure that names it. */
  connection?: string | null;
  onClose?: () => void; onChangeModel?: () => void;
}) {
  const shown = presentFailure(failure, language, connection);
  if (!shown) return null;
  return <div className="chat-error" role="alert">
    <div className="chat-error__body">
      <p>{shown.summary}</p>
      {/* Offered only when the failure itself named the model, and it moves to
          the picker this conversation already has. Nothing is switched here. */}
      {shown.modelRejected && onChangeModel && <button type="button" className="chat-error__action" onClick={onChangeModel}>{labels.changeModel}</button>}
      {/* A new failure starts folded rather than inheriting the last one. */}
      <details className="chat-error__details" key={shown.technical}><summary>{labels.errorDetails}</summary><pre lang="en" translate="no">{shown.technical}</pre></details>
    </div>
    {onClose && <button className="chat-icon" aria-label={labels.close} onClick={onClose}><Icon name="close" /></button>}
  </div>;
}

export function ChatShell({ preferences, settings, configuredProject, defaults, workspace, apps }: Props) {
  const t = words[preferences.language];
  const [initial] = useState(readView);
  const [projects, setProjects] = useState<ChatProject[]>([]);
  const [sessions, setSessions] = useState<ChatSummary[]>([]);
  const [archivedView, setArchivedView] = useState(false);
  const [archiveBusy, setArchiveBusy] = useState<string | null>(null);
  const [providers, setProviders] = useState<ChatProvider[]>([]);
  const [projectApps, setProjectApps] = useState<{ projectDir: string | null; apps: AppStatus[] } | null>(null);
  const [projectDir, setProjectDir] = useState<string | null>(initial.projectDir ?? configuredProject);
  const [chatId, setChatId] = useState<string | null>(initial.chatId);
  const [chat, setChat] = useState<ChatDetail | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [error, setError] = useState<HubError | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [sidebar, setSidebar] = useState(() => initial.sidebar ?? window.innerWidth > 900);
  const [panel, setPanel] = useState(() => initial.panel ?? false);
  const [tabs, setTabs] = useState<ToolTab[]>([]);
  const [activeTool, setActiveTool] = useState<AppId | null>(null);
  const [panelWidth, setPanelWidth] = useState(() => initial.panelWidth ?? 620);
  const [toolBusy, setToolBusy] = useState<AppId | null>(null);
  const [folder, setFolder] = useState("");
  const [projectName, setProjectName] = useState("");
  const [projectInfo, setProjectInfo] = useState(false);
  // The model this conversation will use next. An existing chat keeps its own;
  // a new one starts from the saved default until it is sent.
  const [draftModel, setDraftModel] = useState<string | null>(defaults.model);
  const [customModel, setCustomModel] = useState<string | null>(null);
  const [modelBusy, setModelBusy] = useState(false);
  const [permissionBusy, setPermissionBusy] = useState<string | null>(null);
  const [dialogError, setDialogError] = useState<HubError | null>(null);
  const addDialog = useRef<HTMLDialogElement>(null);
  const newDialog = useRef<HTMLDialogElement>(null);
  const settingsDialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const messages = useRef<HTMLDivElement>(null);
  const actionLock = useRef(false);
  const permissionLock = useRef(false);
  const readLock = useRef(false);
  const observedSessions = useRef(new Map<string, ChatSummary["status"]>());
  const completedChats = useRef(new Set<string>());
  const openedCandidates = useRef(new Set<string>());
  const projectPreparations = useRef(new Map<string, ProjectPreparation>());
  const selection = useRef({ chatId, projectDir, archivedView, projects }); selection.current = { chatId, projectDir, archivedView, projects };
  const project = projects.find((item) => item.projectDir === projectDir);
  const draftKey = chatId ?? `new:${projectDir ?? ""}`;
  const draft = drafts[draftKey] ?? "";
  const running = chat?.id === chatId && chat.status === "running";
  const archived = chat?.id === chatId && chat.archived;
  const visibleSessions = sessions.filter((session) => Boolean(session.archived) === archivedView);
  // An existing conversation keeps the connection it was created with; only a
  // new one takes the saved default.
  const connection = chat?.id === chatId ? { provider: chat.provider, model: chat.model ?? null } : defaults;
  const availableProvider = providers.find((item) => item.id === connection.provider);
  // The connection as the Hub named it, short enough to read in a sentence.
  const connectionName = (availableProvider?.label ?? connection.provider ?? "").replace(/\s+CLI$/, "") || null;
  const chosenModel = chat?.id === chatId ? chat.model ?? null : draftModel;
  // What this connection actually lists, plus whatever is already chosen: a
  // hand-entered id stays selectable even while a catalogue is unreadable.
  const modelOptions = [...new Set([...(availableProvider?.models ?? []), ...(chosenModel ? [chosenModel] : [])])];
  const selectedTab = tabs.find((item) => item.id === activeTool);
  // Every candidate this conversation has an entry for, newest first: one of
  // them is usually the one just made, and the earlier ones stay reachable.
  const candidates = [...new Set([...(chat?.id === chatId ? chat.messages ?? [] : [])]
    .reverse().map((message) => message.candidateId).filter((id): id is string => Boolean(id)))].slice(0, 4);

  const refresh = useCallback(async () => {
    if (readLock.current) return;
    readLock.current = true;
    const selectedId = selection.current.chatId;
    const selectedProject = selection.current.projectDir;
    const prepared = projectPreparations.current.get(selectedProject ?? "");
    const preparedStudio = prepared?.apps?.find((item) => item.appId === "monkeyarch");
    const selectedArchived = selection.current.archivedView;
    try {
      const [nextProjects, nextSessions, nextProviders, detail, nextApps] = await Promise.all([
        request<ChatProject[]>("/api/chat/projects"), request<ChatSummary[]>(`/api/chat/sessions${selectedArchived ? "?archived=true" : ""}`), request<ChatProvider[]>("/api/chat/providers"),
        selectedId ? request<ChatDetail>(`/api/chat/sessions/${encodeURIComponent(selectedId)}`).catch((cause: unknown) => {
          if (cause && typeof cause === "object" && "status" in cause && cause.status === 404) { if (selection.current.chatId === selectedId) setChatId(null); return null; }
          throw cause;
        }) : Promise.resolve(null),
        request<AppStatus[]>(`/api/apps${selectedProject ? `?${new URLSearchParams({ projectDir: selectedProject })}` : ""}`),
      ]);
      for (const session of nextSessions) {
        if (observedSessions.current.get(session.id) === "running" && session.status !== "running") completedChats.current.add(session.id);
        observedSessions.current.set(session.id, session.status);
      }
      // A project created after this request began cannot be present in its
      // old response. Keep the new selection until the next discovery read.
      const selectedSinceRead = selectedProject !== selection.current.projectDir
        ? selection.current.projects.find((item) => item.projectDir === selection.current.projectDir) : undefined;
      const knownProjects = selectedSinceRead && !nextProjects.some((item) => item.projectDir === selectedSinceRead.projectDir)
        ? [...nextProjects, selectedSinceRead] : nextProjects;
      setProjects(knownProjects); setProviders(nextProviders);
      if (selection.current.archivedView === selectedArchived) setSessions(nextSessions);
      if (selection.current.projectDir === selectedProject) {
        setProjectApps({ projectDir: selectedProject, apps: nextApps });
        const studio = nextApps.find((item) => item.appId === "monkeyarch");
        if (selectedProject && preparedStudio && projectPreparations.current.get(selectedProject) === prepared &&
          (studio?.state !== "running" || studio.processId !== preparedStudio.processId || studio.url !== preparedStudio.url)) {
          projectPreparations.current.delete(selectedProject);
        }
      }
      if (selection.current.chatId === selectedId) setChat(detail);
      if (!knownProjects.some((item) => item.projectDir === selection.current.projectDir)) setProjectDir(knownProjects.find((item) => item.projectDir === configuredProject)?.projectDir ?? knownProjects[0]?.projectDir ?? null);
      setLoading(false);
    } catch (cause) { setError(asFailure(cause)); setLoading(false); }
    finally { readLock.current = false; }
  }, [configuredProject]);
  useEffect(() => { void refresh(); const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, 1200); return () => window.clearInterval(timer); }, [refresh]);
  useEffect(() => { setChat(null); setError(null); void refresh(); }, [chatId, refresh]);
  useEffect(() => { void refresh(); }, [archivedView, refresh]);
  useEffect(() => { if (!chatId) { setDraftModel(defaults.model); setCustomModel(null); } }, [chatId, defaults.model]);
  useEffect(() => { try { localStorage.setItem(VIEW_KEY, JSON.stringify({ chatId, projectDir, sidebar, panel, panelWidth })); } catch { /* Navigation stays in this page. */ } }, [chatId, projectDir, sidebar, panel, panelWidth]);
  useEffect(() => { if (messages.current && messages.current.scrollHeight - messages.current.scrollTop - messages.current.clientHeight < 220) messages.current.scrollTop = messages.current.scrollHeight; }, [chat?.messages]);
  useEffect(() => { if (input.current) { input.current.style.height = "auto"; input.current.style.height = `${Math.min(input.current.scrollHeight, 180)}px`; } }, [draft]);
  // An embedded page asking to come back to the conversation. It moves the
  // cursor here and offers an example to edit; it never sends anything.
  useEffect(() => {
    const listen = (event: MessageEvent) => {
      const frame = [...document.querySelectorAll("iframe")].find((item) => item.contentWindow === event.source);
      const tab = frame ? tabs.find((item) => item.url === frame.src) : undefined;
      if (!tab || readHostRequest(event, { origin: new URL(tab.url).origin, window: frame!.contentWindow }) !== START_MODELING) return;
      setDrafts((value) => ({ ...value, [draftKey]: value[draftKey]?.trim() ? value[draftKey]! : t.startingDraft }));
      const box = input.current;
      if (box) { box.focus(); requestAnimationFrame(() => box.setSelectionRange(box.value.length, box.value.length)); }
    };
    window.addEventListener("message", listen);
    return () => window.removeEventListener("message", listen);
  }, [tabs, draftKey, t.startingDraft]);

  const selectChat = (item: ChatSummary) => {
    if (item.projectDir !== projectDir) { setTabs([]); setActiveTool(null); setPanel(false); }
    setArchivedView(Boolean(item.archived)); setProjectDir(item.projectDir); setChatId(item.id);
  };
  const selectProject = (item: ChatProject) => {
    const recent = visibleSessions.find((session) => session.projectDir === item.projectDir);
    if (recent) selectChat(recent);
    else { setProjectDir(item.projectDir); setChatId(null); setTabs([]); setPanel(false); }
  };

  const startTool = async (appId: AppId, target?: string) => {
    const query = target ? `?${new URLSearchParams({ projectDir: target })}` : "";
    let status = await request<AppStatus>(`/api/apps/${appId}/start${query}`, {});
    const deadline = Date.now() + 60000;
    while (status.state !== "running" || !status.url) {
      if (status.state === "error" || status.state === "unavailable") {
        throw Object.assign(new Error(status.error?.detail ?? status.state), { failure: status.error });
      }
      if (Date.now() > deadline) throw new Error("The service did not become ready.");
      await wait();
      status = (await request<AppStatus[]>(`/api/apps${query}`)).find((item) => item.appId === appId)!;
    }
    return status.url;
  };

  /** One preparation per managed Studio, shared by navigation, send and all three workspaces. */
  const ensureProject = useCallback(async (target: string, projectId: string, appId: AppId = "monkeyarch") => {
    let preparation = projectPreparations.current.get(target);
    if (!preparation) {
      const query = new URLSearchParams({ projectDir: target });
      const entry: ProjectPreparation = { apps: null, promise: (async () => {
        await request(`/api/project/modeling?${query}`, { projectId });
        const statuses = await request<AppStatus[]>(`/api/apps?${query}`);
        if (!statuses.some((item) => item.appId === "monkeyarch" && item.state === "running" && item.url)) throw new Error("The project service did not become ready.");
        return statuses;
      })() };
      preparation = entry;
      projectPreparations.current.set(target, entry);
    }
    try {
      const statuses = await preparation.promise;
      preparation.apps = statuses;
      if (selection.current.projectDir === target) setProjectApps({ projectDir: target, apps: statuses });
      const url = statuses.find((item) => item.appId === appId && item.state === "running")?.url;
      if (!url) throw new Error("The project workspace is unavailable.");
      return url;
    } catch (cause) {
      if (projectPreparations.current.get(target) === preparation) projectPreparations.current.delete(target);
      throw cause;
    }
  }, []);
  useEffect(() => {
    if (!project) return;
    let cancelled = false;
    void ensureProject(project.projectDir, project.projectId).catch((cause: unknown) => {
      if (!cancelled && selection.current.projectDir === project.projectDir) setError(asFailure(cause));
    });
    return () => { cancelled = true; };
  }, [project?.projectDir, project?.projectId, ensureProject]);

  const send = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!projectDir || !draft.trim() || actionLock.current || running || archived) return;
    const target = projectDir, content = draft.trim(), key = draftKey;
    actionLock.current = true; setBusy(true); setError(null);
    try {
      let current = chat?.id === chatId ? chat : chatId ? await request<ChatDetail>(`/api/chat/sessions/${encodeURIComponent(chatId)}`) : null;
      if (!current) {
        const body: ChatCreateRequest = { projectDir: target, provider: defaults.provider, model: draftModel };
        current = await request<ChatDetail>("/api/chat/sessions", body);
        setDrafts((value) => ({ ...value, [current!.id]: content }));
        setChatId(current.id); setChat(current);
      }
      if (current.archived) { setChat(current); return; }
      await ensureProject(target, current.projectId);
      const body: ChatPostRequest = { content, projectId: current.projectId };
      const posted = await request<ChatDetail>(`/api/chat/sessions/${current.id}/messages`, body);
      if (selection.current.projectDir === target) { setChatId(posted.id); setChat(posted); }
      setDrafts((value) => ({ ...value, [key]: "", [posted.id]: "" }));
      await refresh();
      requestAnimationFrame(() => { if (messages.current) messages.current.scrollTop = messages.current.scrollHeight; });
    } catch (cause) { setError(asFailure(cause)); void refresh(); }
    finally { actionLock.current = false; setBusy(false); }
  };
  /** Change which model this conversation's next turns run on. */
  const chooseModel = async (value: string | null) => {
    setCustomModel(null);
    if (!chatId || chat?.id !== chatId) { setDraftModel(value); return; }
    if (running || archived || modelBusy || value === (chat.model ?? null)) return;
    setModelBusy(true); setError(null);
    try { setChat(await request<ChatDetail>(`/api/chat/sessions/${encodeURIComponent(chatId)}/model`, { model: value }, "PUT")); }
    catch (cause) { setError(asFailure(cause)); }
    finally { setModelBusy(false); }
  };
  const stop = async () => {
    if (!chatId || actionLock.current) return;
    try { setChat(await request<ChatDetail>(`/api/chat/sessions/${chatId}/stop`, {})); }
    catch (cause) { setError(asFailure(cause)); }
  };
  const setArchived = async (session: ChatSummary, value: boolean) => {
    if (actionLock.current || modelBusy || session.status === "running") return;
    actionLock.current = true; setArchiveBusy(session.id); setError(null);
    try {
      const body: ChatArchiveRequest = { archived: value };
      const updated = await request<ChatDetail>(`/api/chat/sessions/${encodeURIComponent(session.id)}/archive`, body, "PUT");
      setSessions((items) => items.filter((item) => item.id !== updated.id));
      if (selection.current.chatId === updated.id) setChat(updated);
      if (!value) { setArchivedView(false); selectChat(updated); setChat(updated); }
      await refresh();
    } catch (cause) { setError(asFailure(cause)); }
    finally { actionLock.current = false; setArchiveBusy(null); }
  };
  const choosePermission = async (permissionId: string, optionId: string | null) => {
    if (!chat || chat.id !== chatId || permissionLock.current) return;
    const current = chat;
    permissionLock.current = true; setPermissionBusy(permissionId); setError(null);
    try {
      const detail = await request<ChatDetail>(`/api/chat/sessions/${encodeURIComponent(current.id)}/permissions/${encodeURIComponent(permissionId)}`, { projectId: current.projectId, optionId });
      if (selection.current.chatId === current.id) setChat(detail);
    } catch (cause) { if (selection.current.chatId === current.id) setError(asFailure(cause)); }
    finally { permissionLock.current = false; setPermissionBusy(null); }
  };
  /** Make a new empty project in the workspace and open a conversation in it. */
  const createProject = async (event: FormEvent) => {
    event.preventDefault(); if (actionLock.current || !projectName.trim()) return;
    actionLock.current = true; setBusy(true); setDialogError(null);
    try {
      const project = await request<ChatProject>("/api/chat/projects", { name: projectName.trim() });
      setProjects((items) => [...items.filter((item) => item.projectDir !== project.projectDir), project]);
      setArchivedView(false); selectProject(project);
      newDialog.current?.close(); setProjectName("");
      const body: ChatCreateRequest = { projectDir: project.projectDir, provider: defaults.provider, model: defaults.model };
      const created = await request<ChatDetail>("/api/chat/sessions", body);
      selectChat(created); setChat(created); await refresh();
      input.current?.focus();
    } catch (cause) {
      if (newDialog.current?.open) setDialogError(asFailure(cause));
      else setError(asFailure(cause));
    }
    finally { actionLock.current = false; setBusy(false); }
  };
  const addProject = async (event: FormEvent) => {
    event.preventDefault(); if (actionLock.current || !folder.trim()) return;
    actionLock.current = true; setBusy(true); setDialogError(null);
    try {
      const body: ChatCreateRequest = { projectDir: folder.trim().replace(/^"(.*)"$/, "$1"), provider: defaults.provider, model: defaults.model };
      const created = await request<ChatDetail>("/api/chat/sessions", body);
      selectChat(created); setChat(created); addDialog.current?.close(); setFolder(""); await refresh();
    } catch (cause) { setDialogError(asFailure(cause)); }
    finally { actionLock.current = false; setBusy(false); }
  };
  /** `view` names what this page should open, such as the candidate a step produced. */
  const openTool = async (id: AppId, view?: Record<string, string>) => {
    const needsProject = id !== "monkeyfab" && id !== "monkeymonitor";
    if (needsProject && !projectDir) return;
    if (!view && tabs.some((item) => item.id === id)) {
      setPanel(true); setActiveTool(id); setError(null);
      return;
    }
    const existing = tabs.find((item) => item.id === id);
    if (view && existing) {
      const url = new URL(existing.url);
      for (const [key, value] of Object.entries(view)) url.searchParams.set(key, value);
      if (url.href !== existing.url) setTabs((items) => items.map((item) => item.id === id ? { ...item, url: url.href, revision: item.revision + 1 } : item));
      setPanel(true); setActiveTool(id); setError(null);
      return;
    }
    if (actionLock.current) return;
    setPanel(true);
    const target = projectDir, targetChat = chatId;
    actionLock.current = true; setToolBusy(id); setError(null);
    try {
      const location = needsProject ? await ensureProject(target!, project!.projectId, id)
        : apps?.find((item) => item.appId === id && item.state === "running")?.url ?? await startTool(id);
      const url = new URL(applicationUrl(location, preferences)); url.searchParams.set("embedded", "tool");
      // The page is told who embedded it, so it can ask this window - and only
      // this window - for the conversation it cannot open itself.
      url.searchParams.set("host", window.location.origin);
      for (const [key, value] of Object.entries(view ?? {})) url.searchParams.set(key, value);
      if (needsProject && (selection.current.projectDir !== target || selection.current.chatId !== targetChat)) return;
      setTabs((items) => items.some((item) => item.id === id && item.url === url.href) ? items : [...items.filter((item) => item.id !== id), { id, url: url.href, revision: 0 }]); setActiveTool(id);
    } catch (cause) {
      if (!needsProject || (selection.current.projectDir === target && selection.current.chatId === targetChat)) setError(asFailure(cause));
    }
    finally { actionLock.current = false; setToolBusy(null); }
  };

  // Show each successful candidate as soon as it is read back. A turn can keep
  // working on drawings afterward; later polling never reloads the same view.
  useEffect(() => {
    if (!chat || chat.id !== chatId || chat.archived || chat.projectDir !== projectDir || (chat.status !== "running" && !completedChats.current.has(chat.id)) || actionLock.current) return;
    const messages = chat.messages ?? [];
    const lastUser = messages.findLastIndex((message) => message.role === "user");
    const candidate = messages.slice(lastUser + 1).findLast((message) => message.candidateId && message.status === "complete")?.candidateId;
    if (chat.status !== "running") completedChats.current.delete(chat.id);
    const key = `${chat.id}:${messages[lastUser]?.id}:${candidate}`;
    if (candidate && !openedCandidates.current.has(key)) {
      openedCandidates.current.add(key);
      void openTool("monkeyarch", { candidate });
    }
  }, [chat, chatId, projectDir, busy, toolBusy]);
  // The tool panel may take everything except the rail and a usable conversation.
  const clampWidth = (width: number) => Math.max(320, Math.min(width, window.innerWidth - (sidebar ? 244 : 60) - RAIL_WIDTH - RESIZER_WIDTH - CHAT_MIN_WIDTH));

  return <div className="chat-shell" data-sidebar={sidebar} data-panel={panel} style={{ "--browser-width": `${panelWidth}px` } as CSSProperties}>
    <aside className="chat-sidebar" aria-label={t.projects}>
      <div className="chat-sidebar__top"><strong className="wordmark">MonkeyHub</strong><button className="chat-icon" aria-label={sidebar ? t.collapse : t.expand} onClick={() => setSidebar(!sidebar)}><Icon name="sidebar" /></button></div>
      <div className="chat-sidebar__body">
        <button className="chat-new" onClick={() => { setArchivedView(false); setChatId(null); setChat(null); setError(null); input.current?.focus(); }}><Icon name="plus" /><span>{t.newChat}</span></button>
        <button className="chat-new chat-new--project" onClick={() => { setDialogError(null); newDialog.current?.showModal(); }}><Icon name="folder" /><span>{t.newProject}</span></button>
        <div className="chat-project-label"><span>{archivedView ? t.archivedChats : t.projects}</span><button className="chat-icon" aria-label={t.addExisting} title={t.addExisting} onClick={() => { setDialogError(null); addDialog.current?.showModal(); }}><Icon name="plus" /></button></div>
        {!projects.length && <p className="chat-muted chat-project-empty">{loading ? t.loading : t.emptyProjects}</p>}
        {archivedView && !visibleSessions.length && <p className="chat-muted chat-project-empty">{t.archiveEmpty}</p>}
        {projects.filter((item) => !archivedView || visibleSessions.some((session) => session.projectDir === item.projectDir)).map((item) => <section className="chat-project" key={item.projectDir} data-selected={item.projectDir === projectDir}>
          <button className="chat-project__name" title={item.projectDir} onClick={() => selectProject(item)}><Icon name="folder" /><span>{item.name}</span></button>
          {visibleSessions.filter((session) => session.projectDir === item.projectDir).map((session) => <div key={session.id} className="chat-thread-row">
            <button className="chat-thread" aria-current={session.id === chatId ? "page" : undefined} onClick={() => selectChat(session)} title={session.title}>
              <span className="chat-thread__dot" data-status={session.status} /><span>{session.title}</span>
            </button>
            <button className="chat-icon chat-thread-action" aria-label={`${session.archived ? t.restore : t.archive}: ${session.title}`}
              title={session.status === "running" ? t.archiveRunning : session.archived ? t.restore : t.archive}
              disabled={session.status === "running" || busy || modelBusy || archiveBusy !== null}
              onClick={() => void setArchived(session, !session.archived)}><Icon name={session.archived ? "restore" : "archive"} /></button>
          </div>)}
        </section>)}
      </div>
      <button className="chat-settings chat-archive-toggle" aria-pressed={archivedView} onClick={() => setArchivedView(!archivedView)}><Icon name="archive" /><span>{archivedView ? t.activeChats : t.archivedChats}</span></button>
      <button className="chat-settings" onClick={() => settingsDialog.current?.showModal()}><Icon name="settings" /><span>{t.settings}</span></button>
    </aside>
    <main className="chat-main">
      <header className="chat-header"><button className="chat-icon mobile-project-toggle" aria-label={sidebar ? t.collapse : t.expand} onClick={() => setSidebar(!sidebar)}><Icon name="sidebar" /></button><div><span className="chat-header__project">{project?.name ?? "MonkeyHub"}</span><h1>{chat?.id === chatId ? chat.title : t.newChat}</h1></div></header>
      <div className="chat-messages" ref={messages} role="log" aria-live="polite" aria-relevant="additions text">
        {!chat?.messages?.length ? <div className="chat-welcome"><div className="chat-welcome__mark"><Icon name="chat" /></div><h2>{project ? t.empty : t.noProject}</h2><p>{t.emptyHint}</p>{!project && <div className="chat-welcome__actions"><button className="btn btn--primary" onClick={() => { setDialogError(null); newDialog.current?.showModal(); }}>{t.newProject}</button><button className="btn" onClick={() => { setDialogError(null); addDialog.current?.showModal(); }}>{t.addExisting}</button></div>}</div>
          : <div className="chat-message-list">{(chat?.messages ?? []).map((message) => message.role === "tool"
            ? <div className="chat-activity" key={message.id} data-status={message.status}>
                <details><summary><span className="chat-thread__dot" data-status={message.status === "streaming" ? "running" : message.status === "failed" ? "failed" : "idle"} /><span className="chat-activity__line">{activityLine(message.content).slice(0, 160) || t.toolActivity}</span></summary>
                  {activityDetail(message.content) ? <pre>{activityDetail(message.content)}</pre> : <p className="chat-muted">{t.activityEmpty}</p>}</details>
                {running && message.permission && <div className="chat-permission" role="group" aria-label={message.permission.title} aria-busy={permissionBusy === message.permission.id}>
                  <p>{message.permission.title}</p>
                  <div className="chat-permission__actions">{message.permission.options.map((option) => <button key={option.optionId} type="button" className="chat-activity__open" disabled={permissionBusy !== null}
                    onClick={() => void choosePermission(message.permission!.id, option.optionId)}>{option.name}</button>)}
                    <button type="button" className="chat-activity__open" disabled={permissionBusy !== null} onClick={() => void choosePermission(message.permission!.id, null)}>{t.cancel}</button>
                  </div>
                </div>}
                {message.candidateId ? <div className="chat-activity__result"><button type="button" className="chat-activity__open" title={message.candidateId} disabled={!project || Boolean(toolBusy)}
                  onClick={() => void openTool("monkeyarch", { candidate: message.candidateId! })}><Icon name="cube" /><span>{t.openCandidate}</span></button><span className="chat-muted">{t.candidateHint}</span></div> : null}
              </div>
            : <article className={`chat-message chat-message--${message.role}`} key={message.id}><MessageText text={message.content} />{message.status === "failed" || message.status === "interrupted" ? <p className="chat-muted">{t[message.status]}</p> : null}</article>)}</div>}
        {running && <div className="chat-thinking" role="status"><span className="chat-thread__dot" data-status="running" />{t.thinking}</div>}
      </div>
      <div className="chat-composer-wrap">
        {(error ?? (chat?.id === chatId ? chat?.error : null)) && <Failure failure={(error ?? chat!.error)!} language={preferences.language} labels={t} connection={connectionName}
          onClose={error ? () => setError(null) : undefined}
          onChangeModel={running || archived ? undefined : () => {
            const picker = document.getElementById("chat-model") as HTMLSelectElement | null;
            picker?.focus();
            try { (picker as unknown as { showPicker?: () => void })?.showPicker?.(); } catch { /* A browser that will not open it still focused it. */ }
          }} />}
        {archived ? <div className="chat-archived-notice">
          <p>{t.archivedNotice}</p>
          <button className="chat-activity__open" disabled={archiveBusy !== null} onClick={() => void setArchived(chat!, false)}><Icon name="restore" /><span>{t.restoreChat}</span></button>
        </div> : <form className="chat-composer" onSubmit={(event) => void send(event)}>
          <label className="sr-only" htmlFor="chat-input">{t.placeholder}</label><textarea id="chat-input" ref={input} value={draft} placeholder={project ? t.placeholder : t.projectRequired} disabled={!project || busy}
            onChange={(event) => setDrafts((value) => ({ ...value, [draftKey]: event.target.value }))} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (!running) void send(); } }} />
          <div className="chat-composer__bottom"><div className="chat-connection" title={running ? t.modelRunning : t.connectionHint}>
            <span className="chat-connection__name">{providers.find((item) => item.id === connection.provider)?.label ?? connection.provider}</span>
            <label className="sr-only" htmlFor="chat-model">{t.modelLabel}</label>
            <select id="chat-model" className="chat-connection__model" value={customModel !== null ? "__custom__" : chosenModel ?? ""} disabled={running || modelBusy || busy}
              onChange={(event) => { const value = event.target.value; if (value === "__custom__") setCustomModel(chosenModel ?? ""); else void chooseModel(value || null); }}>
              <option value="">{t.modelCliDefault}</option>
              {modelOptions.map((item) => <option key={item} value={item}>{item}</option>)}
              <option value="__custom__">{t.modelCustom}</option>
            </select>
            {availableProvider?.modelCatalog === "checking" && <span className="chat-connection__note">{t.modelChecking}</span>}
            {modelBusy && <span className="chat-connection__note" role="status">{t.modelSaving}</span>}
          </div>
          {running ? <button className="chat-send" type="button" aria-label={t.stop} onClick={() => void stop()}><Icon name="stop" /></button> : <button className="chat-send" type="submit" aria-label={t.send} disabled={busy || !project || !draft.trim() || (!chatId && !availableProvider?.available)}><Icon name="send" /></button>}</div>
        </form>}
        {!archived && customModel !== null && <form className="chat-custom-model" onSubmit={(event) => { event.preventDefault(); const value = customModel.trim(); if (value) void chooseModel(value); else setCustomModel(null); }}>
          <label htmlFor="chat-custom-model">{t.modelCustomLabel}</label>
          <input id="chat-custom-model" autoFocus value={customModel} onChange={(event) => setCustomModel(event.target.value)} />
          <button type="submit" className="btn">{t.modelApply}</button>
          <button type="button" className="btn" onClick={() => setCustomModel(null)}>{t.cancel}</button>
        </form>}
        <p className="chat-composer-note" role="status">{busy || toolBusy ? t.working : !availableProvider?.available && !chatId ? availableProvider?.detail ?? (loading ? "" : t.noProvider) : ""}</p>
      </div>
    </main>
    {panel && <div className="chat-resizer" role="separator" aria-label={t.resize} aria-orientation="vertical" aria-valuemin={320} aria-valuemax={Math.max(320, window.innerWidth - 400)} aria-valuenow={panelWidth} tabIndex={0}
      onKeyDown={(event) => { if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); setPanelWidth((width) => clampWidth(width + (event.key === "ArrowLeft" ? 32 : -32))); } }}
      onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); }} onPointerMove={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) setPanelWidth(clampWidth(window.innerWidth - event.clientX)); }} onPointerUp={(event) => event.currentTarget.releasePointerCapture(event.pointerId)} />}
    {(panel || tabs.length > 0) && <aside className="chat-browser" aria-label={t.browser} hidden={!panel} inert={!panel} aria-hidden={!panel}>
        {selectedTab ? <><div className="chat-browser__pages">{tabs.map((item) => <iframe key={`${item.id}:${item.revision}`} src={item.url} title={`${project?.name ?? ""} · ${t[tools.find((tool) => tool.id === item.id)!.label]}`} hidden={item.id !== activeTool} inert={item.id !== activeTool} allow="clipboard-read; clipboard-write" />)}</div></>
          : <div className="chat-browser__empty"><Icon name="panel" /><h2>{toolBusy ? t.working : t.toolEmpty}</h2><p>{t.toolHint}</p></div>}
      </aside>}
    {/* One column of entries for the whole right-hand side: the tools, this
        conversation's project, and whether the tool content is open at all. */}
    <nav className="chat-rail" aria-label={t.rail}>
      {/* The workspaces of this project, then the tools that answer about the
          machine: one rail, two kinds of entry, told apart on sight. */}
      {railGroups.map((group) => <div className="chat-rail__group" key={group.id} role="group" aria-label={t[group.caption]}>
        <span className="chat-rail__caption">{t[group.caption]}</span>
        {tools.filter((item) => item.group === group.id).map((item) => {
          const needsProject = item.id !== "monkeyfab" && item.id !== "monkeymonitor";
          const statuses = needsProject ? (projectApps?.projectDir === projectDir ? projectApps.apps : null) : apps;
          const status = statuses?.find((app) => app.appId === item.id);
          const state = status?.state;
          const stateText = state === "unavailable" ? t.toolUnavailable : state === "error" ? t.toolError
            : state === "running" ? t.toolRunning : state === "starting" ? t.toolStarting
            : state === "stopping" ? t.toolStopping : state === "stopped" ? t.toolStopped : t.toolUnknown;
          return <button key={item.id} className="chat-rail__tool" aria-label={t[item.label]} aria-pressed={panel && item.id === activeTool}
            title={status?.error?.detail ?? stateText} data-state={state} disabled={(needsProject && !project) || (!tabs.some((tab) => tab.id === item.id) && (busy || Boolean(toolBusy)))}
            onClick={() => void openTool(item.id)}><Icon name={item.icon} /><span>{t[item.label]}</span><small aria-hidden="true">{stateText}</small></button>;
        })}
      </div>)}
      <div className="chat-rail__spacer" />
      {projectInfo && <div className="chat-project-card" role="dialog" aria-label={t.projectInfo} onKeyDown={(event) => { if (event.key === "Escape") setProjectInfo(false); }}>
        <div className="chat-project-card__head"><h2>{t.projectInfo}</h2><button className="chat-icon" aria-label={t.close} onClick={() => setProjectInfo(false)}><Icon name="close" /></button></div>
        {project ? <dl>
          <dt>{t.projects}</dt><dd>{project.name}</dd>
          <dt>{t.path}</dt><dd className="chat-project-card__path" title={project.projectDir}>{project.projectDir}</dd>
          <dt>{t.version}</dt><dd>{project.version === null || project.version === undefined ? t.versionUnknown : t.versionNumber(project.version)}</dd>
          <dt>{t.stage}</dt><dd>{project.stage ?? t.stageNone}</dd>
          <dt>{t.candidate}</dt><dd>{candidates.length ? <>{candidates.map((candidate) => <div className="chat-project-card__run" key={candidate}>
            <span className="chat-project-card__candidate" title={candidate}>{candidate}</span>
            <button type="button" className="chat-activity__open" title={candidate} disabled={Boolean(toolBusy)}
              onClick={() => { setProjectInfo(false); void openTool("monkeyarch", { candidate }); }}><Icon name="cube" /><span>{t.openCandidate}</span></button>
          </div>)}<span className="chat-muted">{t.notEndorsed}</span></> : t.candidateNone}</dd>
        </dl> : <><p>{t.projectNone}</p><p className="chat-muted">{t.projectNoneHint}</p></>}
        {/* The local address of the page on the right is a connection detail:
            available when it is asked for, not on screen all the time. */}
        <details className="chat-project-card__connection">
          <summary>{t.connectionDetails}</summary>
          {selectedTab ? <>
            <label className="sr-only" htmlFor="tool-url">{t.toolUrl}</label>
            <input id="tool-url" className="chat-project-card__url" readOnly value={selectedTab.url} onFocus={(event) => event.currentTarget.select()} />
            <div className="chat-project-card__connection-actions">
              <button type="button" className="chat-activity__open" onClick={() => setTabs((items) => items.map((item) => item.id === activeTool ? { ...item, revision: item.revision + 1 } : item))}><Icon name="refresh" /><span>{t.refresh}</span></button>
              <span className="chat-muted">{t[tools.find((tool) => tool.id === activeTool)!.label]}</span>
            </div>
          </> : <p className="chat-muted">{t.connectionNone}</p>}
        </details>
      </div>}
      {/* This card answers for the project the conversation is bound to. The
          settings at the bottom left are the Hub's, for every conversation. */}
      <div className="chat-rail__group chat-rail__group--project" role="group" aria-label={t.railProject}>
        <span className="chat-rail__caption">{t.railProject}</span>
        <button className="chat-rail__action" aria-expanded={projectInfo} aria-haspopup="dialog" onClick={() => setProjectInfo(!projectInfo)}>
          <Icon name="settings" /><span>{project?.name ?? t.projectInfo}</span></button>
      </div>
      <button className="chat-rail__action chat-rail__action--view" aria-pressed={panel} aria-label={panel ? t.hideTools : t.showTools} onClick={() => setPanel(!panel)}>
        <Icon name="panel" /><span aria-hidden="true">{panel ? t.hideTools : t.showTools}</span></button>
    </nav>
    <dialog ref={newDialog} className="chat-dialog"><form onSubmit={(event) => void createProject(event)}>
      <div className="chat-dialog__heading"><h2>{t.newProjectHeading}</h2><button type="button" className="chat-icon" aria-label={t.close} onClick={() => newDialog.current?.close()}><Icon name="close" /></button></div>
      <label>{t.workspaceLabel}<input id="new-project-workspace" readOnly value={workspace?.workspaceDir ?? ""} /></label>
      <p className="chat-muted">{t.workspaceChange}</p>
      <label>{t.projectName}<input id="new-project-name" autoFocus value={projectName} placeholder={t.projectNamePlaceholder} required
        onChange={(event) => { setProjectName(event.target.value); setDialogError(null); }} /></label>
      <p className="chat-muted">{t.projectNameHelp}</p>
      {projectName.trim() && workspace && <p className="chat-muted chat-new-project__path">{t.createHere}: {[workspace.workspaceDir, projectName.trim()].join("\\")}</p>}
      {dialogError && <Failure failure={dialogError} language={preferences.language} labels={t} />}
      <div className="chat-dialog__actions"><button type="button" className="btn" onClick={() => newDialog.current?.close()}>{t.cancel}</button>
        <button type="submit" className="btn btn--primary" disabled={busy || !projectName.trim()}>{busy ? t.creating : t.create}</button></div>
    </form></dialog>
    <dialog ref={addDialog} className="chat-dialog"><form onSubmit={(event) => void addProject(event)}><div className="chat-dialog__heading"><h2>{t.addExisting}</h2><button type="button" className="chat-icon" aria-label={t.close} onClick={() => addDialog.current?.close()}><Icon name="close" /></button></div><p className="chat-muted">{t.folderHelp}</p><label>{t.folder}<input autoFocus value={folder} placeholder={t.folderPlaceholder} required onChange={(event) => setFolder(event.target.value)} /></label>{dialogError && <Failure failure={dialogError} language={preferences.language} labels={t} />}<div className="chat-dialog__actions"><button type="button" className="btn" onClick={() => addDialog.current?.close()}>{t.cancel}</button><button type="submit" className="btn btn--primary" disabled={busy || !folder.trim()}>{busy ? t.loading : t.add}</button></div></form></dialog>
    <dialog ref={settingsDialog} className="chat-dialog chat-dialog--settings"><div className="chat-dialog__heading"><h2>{t.settingsHeading}</h2><button className="chat-icon" aria-label={t.close} onClick={() => settingsDialog.current?.close()}><Icon name="close" /></button></div>{settings}</dialog>
  </div>;
}
