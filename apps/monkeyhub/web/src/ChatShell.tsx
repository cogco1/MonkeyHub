import "../workspaces/src/styles.css";
import { ErrorBoundary } from "../workspaces/src/app/ErrorBoundary";
import { Fragment, lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent, type ReactNode } from "react";
import { applicationUrl, type AppearancePreferences } from "../../../shared-web/src/appearance.js";
import type { WorktreeGraphDto } from "../workspaces/src/api/generated";
import { projectStatus, refLabel, workRows } from "./worktreeGraph";
import type { AppStatus, ChatArchiveRequest, ChatCreateRequest, ChatDetail, ChatMessage, ChatPostRequest, ChatProject, ChatProvider, ChatSummary, ChatWorkspace, HubError, HubRuntimeDto, ProjectArchiveExportRequest, ProjectArchiveRestoreRequest, ProjectArchiveRestoreResult, ProjectArchiveSummary, ProjectRuntimeDto, RuntimeEvent, UpdateStatus } from "./api/generated";
import { ProjectRuntimeProvider } from "../workspaces/src/api/ProjectRuntimeContext";
import type { WorkspaceDesignContext } from "../workspaces/src/app/ProjectWorkspace";
import { MonitorPage } from "./MonitorPage";
import { ChatMarkdown, ChatMessageFiles, type ChatDocument } from "./ChatMessageContent";
import type { PageSource } from "../workspaces/src/workspaces/monkeyboard/boardScene";
const ProjectWorkspace = lazy(() => import("../workspaces/src/app/ProjectWorkspace").then((module) => ({ default: module.ProjectWorkspace })));
import { presentFailure } from "./chatError";
import { clock, currentStep, describeCall, describeStep, rawDetail, rawLine, stepText, turnsOf, workedSeconds, type ProcessTurn, type ProcessWords } from "./chatProcess";
import { recentUsage, serialMonitorRead, type MonitorEvent, type RecentUsage } from "./monitorData";
import { activeWork, newSchemes, sidebarTasks, type SidebarTask } from "./sidebarTasks";
import { SoftwareUpdateSettings, type RestartBlocker } from "./SoftwareUpdateSettings";
import "./ChatShell.css";

type AppId = AppStatus["appId"] | "drawing" | "publish" | "tree";
type Props = {
  preferences: AppearancePreferences;
  settings: ReactNode;
  settingsDirty?: boolean;
  configuredProject: string | null;
  /** The saved global defaults a *new* conversation starts with. */
  defaults: { provider: ChatCreateRequest["provider"]; model: string | null };
  /** Where a new project is created, as the Hub reports it. */
  workspace: ChatWorkspace | null;
  apps: readonly AppStatus[] | null;
};
// followHead: the tab was restored on a cold start, not opened to inspect that exact
// candidate; the workspace shows the architect's editing base instead (#271). A delivered
// result opens view-only, and only Continue makes it the base (GH-234 Q1/Q2).
// returnTo: the primary surface this project's Drawing tool was opened from (#295).
type ToolTab = { id: AppId; url: string; revision: number; projectDir?: string; projectId?: string; runtimeId?: string; candidate?: string; followHead?: boolean; returnTo?: AppId };
type SavedTool = { id: AppId; candidate?: string };
type ProjectPreparation = { promise: Promise<AppStatus[]>; apps: AppStatus[] | null; modeling?: Promise<unknown> };
const VIEW_KEY = "monkeyhub.chat-view.v1";
/** The rail is always on screen; the conversation never shrinks past this. */
const RAIL_WIDTH = 76, RESIZER_WIDTH = 5, CHAT_MIN_WIDTH = 360;
import { chatCopyCatalog as words } from "./i18n/catalogs";
/** The rail's two kinds of entry (#295, regrouped by the owner for #300): the
    surfaces you work on in this project (Modeling, Board and the Design Tree,
    #284), and the tools that produce output over it or report on the machine.
    Drawing is a projection of the project's current state, not another
    architectural surface. Layout is Board's second mode, reached from its
    Board | Layout switch, so it has no rail entry of its own. */
const tools: { id: AppId; label: "model" | "drawing" | "board" | "render" | "publish" | "fab" | "monitor" | "tree"; icon: string; group: "workspace" | "tools" | null }[] = [
  { id: "monkeyarch", label: "model", icon: "cube", group: "workspace" },
  { id: "monkeyboard", label: "board", icon: "board", group: "workspace" },
  { id: "tree", label: "tree", icon: "tree", group: "workspace" },
  { id: "drawing", label: "drawing", icon: "drawing", group: "tools" },
  { id: "monkeyrender", label: "render", icon: "render", group: "tools" },
  { id: "monkeyfab", label: "fab", icon: "fab", group: "tools" },
  { id: "monkeymonitor", label: "monitor", icon: "chart", group: "tools" },
  { id: "publish", label: "publish", icon: "board", group: null },
];
const railGroups = [{ id: "workspace", caption: "railSurfaces" }, { id: "tools", caption: "railTools" }] as const;
const labelOf = (id: AppId) => tools.find((tool) => tool.id === id)!.label;
/** Project tools that open over a surface and hand the panel back to it when pressed again (#295). */
const returnsToSurface = (id: AppId | undefined) => id === "drawing" || id === "monkeyrender";

function Icon({ name }: { name: string }) {
  const paths: Record<string, ReactNode> = {
    render: <><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="8" cy="9" r="1.5" /><path d="m4 18 5-5 3 3 4-6 4 8" /></>,
    drawing: <><path d="M5 3h10l4 4v14H5ZM15 3v5h4M8 11h8v5H8Z" /><path d="M8 19h8M8 18v2m8-2v2" /></>,
    plus: <path d="M12 5v14M5 12h14" />, panel: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M15 4v16" /></>,
    sidebar: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></>, close: <path d="m6 6 12 12M6 18 18 6" />,
    send: <path d="M12 19V5m-6 6 6-6 6 6" />, stop: <rect x="6" y="6" width="12" height="12" rx="2" />,
    down: <path d="M12 5v14m-6-6 6 6 6-6" />,
    tasks: <><path d="M10 6h10M10 12h10M10 18h10" /><path d="m3.5 6 1.5 1.5L7.5 5m-4 7 1.5 1.5 2.5-2.5m-4 7 1.5 1.5 2.5-2.5" /></>,
    update: <><circle cx="12" cy="12" r="9" /><path d="M12 16V8m-4 4 4-4 4 4" /></>,
    attach: <path d="m8 13 7-7a3 3 0 0 1 4 4L9 20a5 5 0 0 1-7-7L13 2m-5 11 7-7" />,
    folder: <path d="M3 6h7l2 2h9v11H3Z" />, chat: <path d="M4 4h16v13H9l-5 4Z" />,
    archive: <><path d="M4 8h16v13H4ZM3 3h18v5H3ZM9 12h6" /></>,
    restore: <><path d="M4 10a8 8 0 1 1 2 8M4 4v6h6" /></>,
    cube: <><path d="m12 3 9 5v9l-9 5-9-5V8Zm0 10 9-5M3 8l9 5v9M7.5 5.5l9 5" /></>,
    file: <><path d="M5 3h9l5 5v13H5ZM14 3v6h5M8 13h8M8 17h6" /></>,
    board: <><rect x="3" y="4" width="18" height="15" rx="2" /><path d="M7 8h4v5H7Zm7 0h3M14 12h3M8 22l4-3 4 3" /></>,
    fab: <><path d="M6 8V3h12v5M6 17H3V8h18v9h-3M6 13h12v8H6Z" /><path d="M17 10h1" /></>,
    chart: <><path d="M4 4v16h17M8 16v-4M13 16V7M18 16v-7" /></>,
    refresh: <path d="M20 10a8 8 0 1 0-2 8M20 4v6h-6" />,
    tree: <><circle cx="5" cy="12" r="2" /><circle cx="19" cy="12" r="2" /><circle cx="12" cy="5" r="2" /><path d="M7 12h10M12 7v5M12 12l-4 6M12 12l4 6" /></>,
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
const fileSize = (size: number) => size < 1024 ? `${size} B` : size < 1024 * 1024 ? `${(size / 1024).toFixed(1)} KiB` : `${(size / (1024 * 1024)).toFixed(1)} MiB`;
const fileData = (file: File, failure: string) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
  reader.onerror = () => reject(new Error(failure));
  reader.onabort = () => reject(new Error(failure));
  reader.readAsDataURL(file);
});
/** What this page looked like last time: the same conversation and the same frame. */
function readView(): { chatId: string | null; projectDir: string | null; sidebar: boolean | null; panel: boolean | null; panelWidth: number | null; tools: SavedTool[]; activeTool: AppId | null } {
  const routeChatId = new URLSearchParams(window.location.search).get("chatId") || null;
  const empty = { chatId: routeChatId, projectDir: null, sidebar: null, panel: null, panelWidth: null, tools: [], activeTool: null };
  try {
    const saved = JSON.parse(localStorage.getItem(VIEW_KEY) ?? "null");
    const changedRoute = routeChatId && routeChatId !== saved?.chatId;
    return {
      chatId: routeChatId ?? (typeof saved?.chatId === "string" ? saved.chatId : null),
      projectDir: changedRoute ? null : typeof saved?.projectDir === "string" ? saved.projectDir : null,
      sidebar: typeof saved?.sidebar === "boolean" ? saved.sidebar : null,
      panel: changedRoute ? false : typeof saved?.panel === "boolean" ? saved.panel : null,
      panelWidth: typeof saved?.panelWidth === "number" && Number.isFinite(saved.panelWidth) ? saved.panelWidth : null,
      tools: !changedRoute && Array.isArray(saved?.tools) ? saved.tools.filter((item: SavedTool) => item && tools.some((tool) => tool.id === item.id))
        .map((item: SavedTool) => ({ id: item.id, ...(typeof item.candidate === "string" ? { candidate: item.candidate } : {}) })) : [],
      activeTool: !changedRoute && tools.some((tool) => tool.id === saved?.activeTool) ? saved.activeTool : null,
    };
  } catch { return empty; }
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

type ChatWords = (typeof words)[keyof typeof words];
const processWords = (t: ChatWords): ProcessWords => ({ steps: t.processStep, prepare: t.processPrepare,
  webSearch: t.processWebSearch, desktop: t.processDesktop, permission: t.processPermission });
/** The wall clock while `active`; a finished turn needs no ticking. */
function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}
const stepMark = (status: string | undefined) => status === "failed" ? "✕" : status === "streaming" ? "•" : status === "interrupted" ? "–" : "✓";

/**
 * One turn's tool calls as a single row (#285): "Worked 1m 18s · 12 steps",
 * or while the turn runs, its clock and the step it is on. Opened, it lists
 * the steps in plain words; the lines the CLI reported stay under Technical
 * details. It starts folded, and folding leaves no space behind.
 */
function ProcessRow({ turn, running, open, t, onToggle }: {
  turn: ProcessTurn; running: boolean; open: boolean; t: ChatWords; onToggle: (row: HTMLElement) => void;
}) {
  const now = useNow(running);
  const names = processWords(t);
  const count = turn.steps.length;
  const worked = running ? null : workedSeconds(turn);
  const current = running ? currentStep(turn) : null;
  const body = `chat-process-${turn.key}`;
  return <div className="chat-process" data-turn={turn.key} data-running={running}>
    <button type="button" className="chat-process__row" aria-expanded={open} aria-controls={open ? body : undefined} onClick={(event) => onToggle(event.currentTarget)}>
      {running && <span className="chat-thread__dot" data-status="running" />}
      <span className="chat-process__summary">{running ? <>
        {t.processWorking}
        {/* The ticking clock is for the eye; announcing it every second would drown the log. */}
        {turn.startedAt !== null && <span aria-hidden="true"> · {clock((now - turn.startedAt) / 1000)}</span>}
        {current && <> · <span className="chat-process__current">{stepText(describeStep(current), names)}…</span></>}
      </> : worked !== null ? t.processWorked(t.elapsed(worked), count) : t.processSteps(count)}</span>
      {/* A narrow column shortens the current step, never the counts. */}
      {running && <span className="chat-process__count">{"\u00a0· "}{t.processStepCount(count)}</span>}
      {turn.failed > 0 && <span className="chat-process__failed">{"\u00a0· "}{t.processFailed(turn.failed)}</span>}
      <svg className="chat-process__chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>
    </button>
    {open && <div className="chat-process__body" id={body}>
      <ol className="chat-process__steps">{turn.steps.map((step) => <li key={step.id} data-status={step.status}>
        <span className="chat-process__mark" aria-hidden="true">{stepMark(step.status)}</span>
        <span>{stepText(describeStep(step), names)}{step.status === "failed" && <span className="chat-process__step-failed"> · {t.processStepFailed}</span>}</span>
      </li>)}</ol>
      <details className="chat-process__technical">
        <summary>{t.processTechnical}</summary>
        <ol>{turn.steps.map((step) => <li key={step.id}>
          <code lang="en" translate="no">{rawLine(step)}</code>
          {rawDetail(step) && <pre lang="en" translate="no">{rawDetail(step)}</pre>}
        </li>)}</ol>
      </details>
    </div>}
  </div>;
}

export function ChatShell({ preferences, settings, settingsDirty = false, configuredProject, defaults, workspace, apps }: Props) {
  const t = words[preferences.language];
  const [initial] = useState(readView);
  const [projects, setProjects] = useState<ChatProject[]>([]);
  const [sessions, setSessions] = useState<ChatSummary[]>([]);
  const [archivedView, setArchivedView] = useState(false);
  const [archiveBusy, setArchiveBusy] = useState<string | null>(null);
  const [providers, setProviders] = useState<ChatProvider[]>([]);
  const [projectApps, setProjectApps] = useState<{ projectDir: string | null; apps: AppStatus[] } | null>(null);
  const [runtime, setRuntime] = useState<HubRuntimeDto | null>(null);
  const [eventsConnected, setEventsConnected] = useState(true);
  const [recovering, setRecovering] = useState(false);
  const [projectDir, setProjectDir] = useState<string | null>(initial.projectDir ?? configuredProject);
  const [chatId, setChatId] = useState<string | null>(initial.chatId);
  const [chat, setChat] = useState<ChatDetail | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [draftAttachments, setDraftAttachments] = useState<Record<string, File[]>>({});
  const [contextModes, setContextModes] = useState<Record<string, "continue" | "project">>({});
  const [designContexts, setDesignContexts] = useState<Record<string, WorkspaceDesignContext | null>>({});
  const contextCallbacks = useRef(new Map<string, (context: WorkspaceDesignContext | null) => void>());
  const workspaceContextCallback = (runtimeId: string) => {
    let callback = contextCallbacks.current.get(runtimeId);
    if (!callback) {
      callback = (context) => setDesignContexts((current) => JSON.stringify(current[runtimeId]) === JSON.stringify(context)
        ? current : { ...current, [runtimeId]: context });
      contextCallbacks.current.set(runtimeId, callback);
    }
    return callback;
  };
  const [draggingFiles, setDraggingFiles] = useState(false);
  const [error, setError] = useState<HubError | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [sidebar, setSidebar] = useState(() => initial.sidebar ?? window.innerWidth > 900);
  const [panel, setPanel] = useState(() => initial.panel ?? false);
  const [tabs, setTabs] = useState<ToolTab[]>([]);
  const [activeTool, setActiveTool] = useState<AppId | null>(null);
  const [documentRequests, setDocumentRequests] = useState<Record<string, { source: PageSource; requestId: number }>>({});
  const documentRequestSequence = useRef(0);
  const [panelWidth, setPanelWidth] = useState(() => initial.panelWidth ?? 620);
  const [toolBusy, setToolBusy] = useState<AppId | null>(null);
  const [folder, setFolder] = useState("");
  const [projectName, setProjectName] = useState("");
  const [projectInfo, setProjectInfo] = useState(false);
  // #271: the read-only Worktree Graph shown in the project card.
  const [worktrees, setWorktrees] = useState<{ runtimeId: string; graph: WorktreeGraphDto } | null>(null);
  const [worktreeError, setWorktreeError] = useState<string | null>(null);
  const [worktreeRead, setWorktreeRead] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [updateRestarting, setUpdateRestarting] = useState(false);
  // The model this conversation will use next. An existing chat keeps its own;
  // a new one starts from the saved default until it is sent.
  const [draftModel, setDraftModel] = useState<string | null>(defaults.model);
  const [customModel, setCustomModel] = useState<string | null>(null);
  const [modelBusy, setModelBusy] = useState(false);
  const [permissionBusy, setPermissionBusy] = useState<string | null>(null);
  const [dialogError, setDialogError] = useState<HubError | null>(null);
  // One archive at a time: the path each dialog was given, and what the Hub
  // answered about the file it actually wrote or read back.
  const [archivePath, setArchivePath] = useState("");
  const [restorePath, setRestorePath] = useState("");
  const [restoreTarget, setRestoreTarget] = useState("");
  const [archiveSummary, setArchiveSummary] = useState<ProjectArchiveSummary | null>(null);
  const [restoreResult, setRestoreResult] = useState<ProjectArchiveRestoreResult | null>(null);
  const addDialog = useRef<HTMLDialogElement>(null);
  const newDialog = useRef<HTMLDialogElement>(null);
  const archiveDialog = useRef<HTMLDialogElement>(null);
  const restoreDialog = useRef<HTMLDialogElement>(null);
  const settingsDialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const messages = useRef<HTMLDivElement>(null);
  const actionLock = useRef(false);
  const permissionLock = useRef(false);
  const readLock = useRef(false);
  const readAgain = useRef(false);
  const runtimeRef = useRef<HubRuntimeDto | null>(null);
  const runtimeAttachments = useRef(new Map<string, ProjectRuntimeDto>());
  const restoredTools = useRef(false);
  const workerInstances = useRef(new Map<string, string>());
  const observedSessions = useRef(new Map<string, ChatSummary["status"]>());
  const completedChats = useRef(new Set<string>());
  const openedCandidates = useRef(new Set<string>());
  const observedRuntimeCandidates = useRef(new Map<string, { completed: Set<string>; sequence: number }>());
  const projectPreparations = useRef(new Map<string, ProjectPreparation>());
  const selection = useRef({ chatId, projectDir, archivedView, projects }); selection.current = { chatId, projectDir, archivedView, projects };
  const project = projects.find((item) => item.projectDir === projectDir);
  const projectRuntime = runtime?.projects.find((item) => item.projectDir === projectDir && item.projectId === project?.projectId);
  const studioWorker = projectRuntime?.workers?.find((item) => item.serviceId === "studio");
  const crashed = studioWorker?.state === "crashed";
  const recoverableOperation = projectRuntime?.operations?.find((item) => ["needs_recovery", "failed", "stale"].includes(item.status));
  // An edited work copy the project would not take is the architect's own save
  // going nowhere. It is said here, on the runtime strip that already reports
  // what this attachment knows, and nowhere else.
  const workCopyRefusal = projectRuntime?.error?.code?.startsWith("WORK_COPY_") ? projectRuntime.error : null;
  const draftKey = chatId ?? `new:${projectDir ?? ""}`;
  const draft = drafts[draftKey] ?? "";
  const attachments = draftAttachments[draftKey] ?? [];
  const contextMode = contextModes[draftKey] ?? "continue";
  const workspaceContext = projectRuntime ? designContexts[projectRuntime.runtimeId] : null;
  const designContext = workspaceContext?.projectId === project?.projectId ? workspaceContext?.designContext : null;
  // Saved or not, local model edits no candidate holds keep project state out of chat.
  const contextUnavailable = workspaceContext?.unavailableReason === "unsaved" || workspaceContext?.unavailableReason === "unsynced"
    ? t.contextUnsaved : workspaceContext?.unavailableReason === "loading" ? t.contextLoading : t.contextOpenProject;
  const running = chat?.id === chatId && chat.status === "running";
  // #285: a turn's tool calls fold into one process row; the Agent's text,
  // results, permission prompts and errors stay in the conversation.
  const shownMessages = chat?.id === chatId ? chat.messages : undefined;
  const turns = useMemo(() => turnsOf(shownMessages ?? [], running), [shownMessages, running]);
  const [expandedTurns, setExpandedTurns] = useState<ReadonlySet<string>>(() => new Set());
  // A conversation opens at its latest message and follows new output only
  // while the reader is already there; a reader who scrolled up is never moved.
  const followLatest = useRef(true);
  const jumping = useRef(false);
  const openingChat = useRef<string | null>(initial.chatId);
  const seenEntries = useRef<{ chatId: string | null; ids: ReadonlySet<string> }>({ chatId: null, ids: new Set() });
  const foldAnchor = useRef<{ key: string; top: number } | null>(null);
  const [latest, setLatest] = useState({ away: false, unseen: 0 });
  // #300: work running or waiting across open projects, this week's usage and a ready update.
  const tasks = useMemo(() => sidebarTasks(runtime, projects), [runtime, projects]);
  const busyProjects = useMemo(() => activeWork(tasks), [tasks]);
  const [tasksOpen, setTasksOpen] = useState(false);
  const [usage, setUsage] = useState<RecentUsage | null>(null);
  const [usageRead, setUsageRead] = useState(0);
  const [updateReady, setUpdateReady] = useState(false);
  // Include hidden conversations and mounted project workspaces: a restart
  // would lose their in-memory drafts just as it would the visible composer.
  // Model edits the project's working draft already holds ("unsynced") are
  // restored after the restart; only edits it does not hold yet block it.
  const restartBlocker: RestartBlocker = Object.values(drafts).some((text) => Boolean(text.trim())) || Object.values(draftAttachments).some((files) => files.length)
    ? "drafts" : Object.values(designContexts).some((context) => context?.unavailableReason === "unsaved") ? "model"
      : settingsDirty ? "settings" : busy || toolBusy || modelBusy || archiveBusy || permissionBusy || recovering || loading || running || !eventsConnected ||
        sessions.some((session) => session.status === "running") ||
        Object.values(designContexts).some((context) => context?.unavailableReason === "loading") ||
        runtime?.projects.some((item) => item.workers?.some((worker) => ["starting", "busy", "stopping", "recovering"].includes(worker.state)) ||
          item.operations?.some((operation) => ["queued", "planning", "validated", "executing", "committing"].includes(operation.status)))
        ? "busy" : null;
  const archived = chat?.id === chatId && chat.archived;
  const external = chat?.id === chatId && Boolean(chat.sourceSessionId);
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
  const currentTabs = tabs.filter((item) => !item.projectDir || item.projectDir === projectDir);
  const selectedTab = currentTabs.find((item) => item.id === activeTool);
  // Prepare the same project session for chat before its viewport is opened.
  // These hidden mounts are not navigation tabs and never open the tool panel.
  const workspaceTabs: ToolTab[] = [...tabs, ...(projectRuntime ? [projectRuntime] : []).filter((item) =>
    item.projection === "ready" && item.workers?.some((worker) => worker.serviceId === "studio" && worker.healthy) &&
    !tabs.some((tab) => tab.runtimeId === item.runtimeId)).map((item) => ({ id: "monkeyarch" as const, url: "", revision: 0,
      projectDir: item.projectDir, projectId: item.projectId, runtimeId: item.runtimeId }))];

  // Read the project's Worktree Graph while its card is open, and again as its work changes.
  const operationSignature = (projectRuntime?.operations ?? []).map((item) => `${item.operationId}:${item.status}`).join("|");
  useEffect(() => {
    const runtimeId = projectRuntime?.runtimeId;
    if (!projectInfo || !runtimeId || projectRuntime?.projection !== "ready") return;
    let live = true;
    request<WorktreeGraphDto>(`/api/runtime/projects/${encodeURIComponent(runtimeId)}/studio/api/worktrees`)
      .then((graph) => { if (live) { setWorktrees({ runtimeId, graph }); setWorktreeError(null); } })
      .catch((cause: unknown) => { if (live) setWorktreeError(asFailure(cause).detail); });
    return () => { live = false; };
  }, [projectInfo, projectRuntime?.runtimeId, projectRuntime?.projection, operationSignature, worktreeRead]);
  const graph = worktrees?.runtimeId === projectRuntime?.runtimeId ? worktrees?.graph ?? null : null;
  const status = graph ? projectStatus(graph) : null;
  const rows = graph ? workRows(graph, projectRuntime?.operations ?? [], sessions, { tools: t.workTools, unattributed: t.workUnattributed }) : [];

  const receiveRuntime = useCallback((snapshot: HubRuntimeDto) => {
    const previous = runtimeRef.current;
    if (previous?.serverId === snapshot.serverId && snapshot.sequence < previous.sequence) return;
    for (const item of snapshot.projects) runtimeAttachments.current.set(item.projectDir, item);
    runtimeRef.current = snapshot; setRuntime(snapshot);
  }, []);
  const refresh = useCallback(async () => {
    if (readLock.current) { readAgain.current = true; return; }
    readLock.current = true;
    const selectedId = selection.current.chatId;
    const selectedProject = selection.current.projectDir;
    const prepared = projectPreparations.current.get(selectedProject ?? "");
    const preparedStudio = prepared?.apps?.find((item) => item.appId === "monkeyarch");
    const selectedArchived = selection.current.archivedView;
    try {
      const [nextProjects, nextSessions, nextProviders, detail, nextApps, nextRuntime] = await Promise.all([
        request<ChatProject[]>("/api/chat/projects"), request<ChatSummary[]>(`/api/chat/sessions${selectedArchived ? "?archived=true" : ""}`), request<ChatProvider[]>("/api/chat/providers"),
        selectedId ? request<ChatDetail>(`/api/chat/sessions/${encodeURIComponent(selectedId)}`).catch((cause: unknown) => {
          if (cause && typeof cause === "object" && "status" in cause && cause.status === 404) { if (selection.current.chatId === selectedId) setChatId(null); return null; }
          throw cause;
        }) : Promise.resolve(null),
        request<AppStatus[]>(`/api/apps${selectedProject ? `?${new URLSearchParams({ projectDir: selectedProject })}` : ""}`),
        request<HubRuntimeDto>("/api/runtime"),
      ]);
      receiveRuntime(nextRuntime);
      for (const session of nextSessions) {
        if (observedSessions.current.get(session.id) === "running" && session.status !== "running") {
          completedChats.current.add(session.id);
          setUsageRead((value) => value + 1);
        }
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
      const detailProject = detail && knownProjects.find((item) => item.projectId === detail.projectId && item.projectDir === detail.projectDir);
      if (selection.current.chatId === selectedId) {
        setChat(detail);
        if (detailProject && selection.current.projectDir !== detailProject.projectDir) setProjectDir(detailProject.projectDir);
      }
      if (!detailProject && !knownProjects.some((item) => item.projectDir === selection.current.projectDir)) setProjectDir(knownProjects.find((item) => item.projectDir === configuredProject)?.projectDir ?? knownProjects[0]?.projectDir ?? null);
      setLoading(false);
    } catch (cause) { setError(asFailure(cause)); setLoading(false); }
    finally { readLock.current = false; if (readAgain.current) { readAgain.current = false; void refresh(); } }
  }, [configuredProject, receiveRuntime]);
  useEffect(() => {
    void refresh();
    let connected = false;
    let stream: EventSource, reconnectTimer: number | undefined;
    const connect = () => {
      reconnectTimer = undefined;
      stream = new EventSource("/api/runtime/events");
      stream.onopen = () => { connected = true; setEventsConnected(true); void refresh(); };
      stream.onerror = () => {
        connected = false; setEventsConnected(false);
        // A 503 can close EventSource permanently; transport errors use its
        // built-in retry. Both reconnect paths only read current state.
        if (stream.readyState === EventSource.CLOSED && reconnectTimer === undefined) reconnectTimer = window.setTimeout(connect, 1500);
      };
      stream.addEventListener("runtime", (message) => {
        try { const event: RuntimeEvent = JSON.parse((message as MessageEvent).data); if (event.snapshot) receiveRuntime(event.snapshot); } catch { /* Re-read the authoritative snapshot below. */ }
        void refresh();
      });
    };
    connect();
    const timer = window.setInterval(() => { if (!connected && !document.hidden) void refresh(); }, 5000);
    const visible = () => { if (!document.hidden) void refresh(); };
    document.addEventListener("visibilitychange", visible);
    return () => { stream.close(); window.clearTimeout(reconnectTimer); window.clearInterval(timer); document.removeEventListener("visibilitychange", visible); };
  }, [refresh, receiveRuntime]);
  useEffect(() => {
    if (!providers.some((item) => item.modelCatalog === "checking")) return;
    const timer = window.setInterval(() => { void request<ChatProvider[]>("/api/chat/providers").then(setProviders).catch(() => {}); }, 5000);
    return () => window.clearInterval(timer);
  }, [providers]);
  useEffect(() => {
    setChat(null); setError(null);
    // Reopening a chat shows its turns folded and lands at its latest message.
    setExpandedTurns(new Set()); openingChat.current = chatId; followLatest.current = true; jumping.current = false;
    setLatest({ away: false, unseen: 0 });
    void refresh();
  }, [chatId, refresh]);
  useEffect(() => { void refresh(); }, [archivedView, refresh]);
  useEffect(() => { if (!chatId) { setDraftModel(defaults.model); setCustomModel(null); } }, [chatId, defaults.model]);
  useEffect(() => {
    const url = new URL(window.location.href);
    if (chatId) url.searchParams.set("chatId", chatId); else url.searchParams.delete("chatId");
    window.history.replaceState(null, "", url);
  }, [chatId]);
  useEffect(() => { try { localStorage.setItem(VIEW_KEY, JSON.stringify({ chatId, projectDir, sidebar, panel, panelWidth, activeTool,
    tools: !restoredTools.current && initial.projectDir === projectDir ? initial.tools : currentTabs.map((item) => ({ id: item.id, candidate: item.candidate })),
  })); } catch { /* Navigation stays in this page. */ } }, [chatId, projectDir, sidebar, panel, panelWidth, tabs, activeTool, initial]);
  const nearLatest = (node: HTMLElement) => node.scrollHeight - node.scrollTop - node.clientHeight <= 80;
  // The list scrolls smoothly by its own style; following output must not lag behind it.
  const toLatest = (behavior: ScrollBehavior = "instant") => { messages.current?.scrollTo({ top: messages.current.scrollHeight, behavior }); };
  useLayoutEffect(() => {
    const node = messages.current, list = shownMessages ?? [];
    // What a reader would count as new: entries in the conversation, not each folded step.
    const ids = new Set(list.filter((message) => message.role !== "tool" || message.id.includes(":progress:") || message.candidateId || message.permission).map((message) => message.id));
    const seen = seenEntries.current;
    const fresh = seen.chatId === chatId ? [...ids].filter((id) => !seen.ids.has(id)).length : 0;
    seenEntries.current = { chatId, ids };
    if (!node || !list.length) return;
    if (openingChat.current === chatId) { openingChat.current = null; followLatest.current = true; toLatest(); return; }
    if (followLatest.current) toLatest();
    else if (fresh) setLatest((value) => ({ away: true, unseen: value.unseen + fresh }));
  }, [shownMessages, chatId]);
  useEffect(() => {
    // Images and documents finish loading after the text: keep a following reader at the end.
    const node = messages.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => { if (followLatest.current && !jumping.current) toLatest(); });
    observer.observe(node);
    for (const child of node.children) observer.observe(child);
    return () => observer.disconnect();
  }, [Boolean(shownMessages?.length), chatId]);
  const readPosition = () => {
    const node = messages.current;
    if (!node) return;
    const at = nearLatest(node);
    if (jumping.current) { if (!at) return; jumping.current = false; }
    followLatest.current = at;
    setLatest((value) => at ? (value.away || value.unseen ? { away: false, unseen: 0 } : value) : value.away ? value : { ...value, away: true });
  };
  const jumpToLatest = () => {
    const node = messages.current;
    if (!node) return;
    jumping.current = true; followLatest.current = true; setLatest({ away: false, unseen: 0 });
    toLatest(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth");
    // A reader who takes the wheel mid-scroll is reading again.
    window.setTimeout(() => { jumping.current = false; }, 1200);
  };
  const toggleTurn = (key: string, row: HTMLElement) => {
    foldAnchor.current = { key, top: row.getBoundingClientRect().top };
    followLatest.current = false;
    setExpandedTurns((current) => { const next = new Set(current); if (next.has(key)) next.delete(key); else next.add(key); return next; });
  };
  useLayoutEffect(() => {
    // Keep the row that was pressed where it was on screen; only the space below it changes.
    const anchor = foldAnchor.current, node = messages.current;
    if (!anchor || !node) return;
    foldAnchor.current = null;
    const row = [...node.querySelectorAll<HTMLElement>(".chat-process")].find((item) => item.dataset.turn === anchor.key)?.querySelector("button");
    if (row) node.scrollBy({ top: row.getBoundingClientRect().top - anchor.top, behavior: "instant" });
    const at = nearLatest(node);
    followLatest.current = at;
    setLatest((value) => at ? { away: false, unseen: 0 } : { ...value, away: true });
  }, [expandedTurns]);
  // The usage figure reads Monitor's records when the Hub reports it running and
  // again after a turn finishes; Monitor's own page polls, this footer does not.
  const monitorApp = apps?.find((item) => item.appId === "monkeymonitor");
  const monitorBase = monitorApp?.state === "running" && monitorApp.apiUrl ? monitorApp.apiUrl.replace(/\/+$/, "") : null;
  useEffect(() => {
    if (!monitorBase) return;
    let live = true;
    void serialMonitorRead(async () => {
      const response = await fetch(`${monitorBase}/api/events`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.json() as { events?: MonitorEvent[] };
    }).then((body) => { if (live) setUsage(recentUsage(body.events ?? [], Date.now())); }, () => { /* Busy or restarting: keep the last figure. */ });
    return () => { live = false; };
  }, [monitorBase, usageRead]);
  const usageFigure = usage && {
    short: new Intl.NumberFormat(preferences.language, { notation: "compact", maximumFractionDigits: 1 }).format(usage.tokens),
    full: usage.tokens.toLocaleString(preferences.language),
  };
  useEffect(() => {
    // Software Update reads its own status while Settings is open; the footer reflects it otherwise.
    if (settingsOpen) return;
    let live = true;
    const read = () => fetch("/api/updates/status", { cache: "no-store" })
      .then((response) => response.ok ? response.json() as Promise<UpdateStatus> : null)
      .then((status) => { if (live && status) setUpdateReady(status.state === "ready"); }, () => { /* The Hub may be restarting. */ });
    void read();
    const timer = window.setInterval(() => { if (!document.hidden) void read(); }, 60_000);
    return () => { live = false; window.clearInterval(timer); };
  }, [settingsOpen]);
  const openSettings = () => { setSettingsOpen(true); settingsDialog.current?.showModal(); };
  const openSoftwareUpdate = () => {
    openSettings();
    requestAnimationFrame(() => document.getElementById("software-update-heading")?.scrollIntoView({ block: "start" }));
  };
  useEffect(() => { if (input.current) { input.current.style.height = "auto"; input.current.style.height = `${Math.min(input.current.scrollHeight, 180)}px`; } }, [draft]);
  const focusConversation = () => {
    setDrafts((value) => ({ ...value, [draftKey]: value[draftKey]?.trim() ? value[draftKey]! : t.startingDraft }));
    input.current?.focus();
  };

  const selectChat = (item: ChatSummary) => {
    if (item.projectDir !== projectDir) { const view = tabs.find((tab) => tab.projectDir === item.projectDir); setActiveTool(view?.id ?? null); setPanel(Boolean(view)); }
    setArchivedView(Boolean(item.archived)); setProjectDir(item.projectDir); setChatId(item.id);
  };
  const openTask = (task: SidebarTask) => {
    if (task.chat) { selectChat(task.chat); return; }
    const target = projects.find((item) => item.projectDir === task.projectDir && item.projectId === task.projectId);
    if (target) { setArchivedView(false); selectProject(target); }
  };
  const selectProject = (item: ChatProject) => {
    const recent = visibleSessions.find((session) => session.projectDir === item.projectDir);
    if (recent) selectChat(recent);
    else { const view = tabs.find((tab) => tab.projectDir === item.projectDir); setProjectDir(item.projectDir); setChatId(null); setActiveTool(view?.id ?? null); setPanel(Boolean(view)); }
  };

  const startTool = useCallback(async (appId: AppId, target?: string) => {
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
  }, []);

  /** Runtime attachment is read-only for project content; only an explicit Arch entry seeds modeling. */
  const ensureProject = useCallback(async (target: string, projectId: string, appId?: AppId) => {
    let preparation = projectPreparations.current.get(target);
    if (!preparation) {
      const query = new URLSearchParams({ projectDir: target });
      const entry: ProjectPreparation = { apps: null, promise: (async () => {
        const opened = await request<ProjectRuntimeDto>("/api/runtime/projects/open", { projectDir: target, projectId });
        // Open acknowledges attachment without a sequence. Only a versioned
        // snapshot may update worker state or supersede an event already read.
        receiveRuntime(await request<HubRuntimeDto>("/api/runtime"));
        const attached = runtimeRef.current?.projects.find((item) => item.runtimeId === opened.runtimeId);
        if (!attached || attached.projectId !== projectId || opened.projectId !== projectId) throw new Error("The project runtime is no longer attached to this project.");
        const worker = attached.workers?.find((item) => item.serviceId === "studio");
        if (worker?.state === "crashed" || (worker?.state === "unavailable" && worker.processId)) {
          throw Object.assign(new Error("The project service needs recovery."), { failure: worker.error ?? { code: "WORKER_NEEDS_RECOVERY", detail: "The project service exited. Recover it to read saved results." } });
        }
        if (!worker?.healthy) await startTool("monkeyrender", target);
        const statuses = await request<AppStatus[]>(`/api/apps?${query}`);
        if (!statuses.some((item) => item.appId === "monkeyarch" && item.state === "running" && item.url)) throw new Error("The project service did not become ready.");
        const binding = await request<{ projectId: string }>(`/api/runtime/projects/${opened.runtimeId}/studio/api/project`);
        if (binding.projectId !== projectId) throw new Error("The running application belongs to a different project.");
        return statuses;
      })() };
      preparation = entry;
      projectPreparations.current.set(target, entry);
    }
    try {
      const statuses = await preparation.promise;
      preparation.apps = statuses;
      if (appId === "monkeyarch" && runtimeAttachments.current.get(target)?.projection !== "ready") {
        preparation.modeling ??= request(`/api/project/modeling?${new URLSearchParams({ projectDir: target })}`, { projectId });
        await preparation.modeling;
      }
      if (selection.current.projectDir === target) setProjectApps({ projectDir: target, apps: statuses });
      const url = statuses.find((item) => item.appId === (!appId || (appId === "drawing" || appId === "publish" || appId === "tree") ? "monkeyarch" : appId) && item.state === "running")?.url;
      if (!url) throw new Error("The project workspace is unavailable.");
      return url;
    } catch (cause) {
      if (projectPreparations.current.get(target) === preparation) projectPreparations.current.delete(target);
      throw cause;
    }
  }, [receiveRuntime, startTool]);
  useEffect(() => {
    if (!project) return;
    let cancelled = false;
    void ensureProject(project.projectDir, project.projectId).catch((cause: unknown) => {
      if (!cancelled && selection.current.projectDir === project.projectDir) setError(asFailure(cause));
    });
    return () => { cancelled = true; };
  }, [project?.projectDir, project?.projectId, ensureProject]);

  const recoverWorker = async () => {
    if (!projectRuntime || !crashed || recovering || actionLock.current) return;
    const target = projectRuntime;
    actionLock.current = true; setRecovering(true); setError(null);
    try {
      await request<ProjectRuntimeDto>(`/api/runtime/projects/${target.runtimeId}/recover`, { projectId: target.projectId });
      projectPreparations.current.delete(target.projectDir);
      await refresh();
    } catch (cause) { if (selection.current.projectDir === target.projectDir) setError(asFailure(cause)); }
    finally { actionLock.current = false; setRecovering(false); }
  };

  // A changed, healthy instance is a new page host. Preserve each view selection,
  // but never submit a project operation while reconnecting its frame.
  useEffect(() => {
    if (!projectRuntime || !studioWorker?.healthy || !["ready", "busy"].includes(studioWorker.state)
      || projectApps?.projectDir !== projectDir) return;
    const statuses = projectApps.apps;
    if (!statuses.some((item) => item.serviceId === "studio" && item.processId === studioWorker.processId && item.state === "running")) return;
    const previous = workerInstances.current.get(projectRuntime.runtimeId);
    workerInstances.current.set(projectRuntime.runtimeId, studioWorker.instanceId);
    if (!previous || previous === studioWorker.instanceId) return;
    projectPreparations.current.set(projectRuntime.projectDir, { apps: statuses, promise: Promise.resolve(statuses) });
    setTabs((items) => items.map((item) => item.runtimeId === projectRuntime.runtimeId
      ? { ...item, revision: item.revision + 1 } : item));
  }, [projectRuntime, studioWorker, projectApps, projectDir]);

  const addAttachments = (files: File[]) => {
    if (!files.length || !project || busy || archived) return;
    const combined = [...attachments, ...files];
    const detail = combined.length > 8 ? t.attachmentCount : files.some((file) => file.size > 20 * 1024 * 1024) ? t.attachmentSize
      : combined.reduce((size, file) => size + file.size, 0) > 40 * 1024 * 1024 ? t.attachmentTotal : null;
    if (detail) { setError({ code: "CHAT_ATTACHMENT_LIMIT", detail }); return; }
    setDraftAttachments((value) => ({ ...value, [draftKey]: combined }));
    setError(null);
  };

  const send = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!projectDir || (!draft.trim() && !attachments.length) || actionLock.current || archived || external) return;
    if (running && chat) {
      // #301: while the Agent works, a message goes into the running turn. It
      // carries only its text: no new design source, and files being gathered
      // stay in the draft for the next turn. The composer stays open.
      if (!draft.trim()) return;
      const target = chat.id, content = draft.trim(), key = draftKey;
      actionLock.current = true; setError(null);
      setDrafts((value) => ({ ...value, [key]: "" }));
      try {
        const body: ChatPostRequest = { content, projectId: chat.projectId };
        const posted = await request<ChatDetail>(`/api/chat/sessions/${encodeURIComponent(target)}/messages`, body);
        if (selection.current.chatId === target) setChat(posted);
        requestAnimationFrame(() => { if (messages.current) messages.current.scrollTop = messages.current.scrollHeight; });
      } catch (cause) { setDrafts((value) => ({ ...value, [key]: value[key] || content })); setError(asFailure(cause)); }
      finally { actionLock.current = false; }
      return;
    }
    const target = projectDir, content = draft.trim(), key = draftKey, files = attachments;
    const requestedContext = designContext, requestedContextMode = contextMode, contextProjectId = workspaceContext?.projectId;
    if (requestedContextMode === "project" && !requestedContext) {
      setError({ code: "CHAT_CONTEXT_UNAVAILABLE", detail: contextUnavailable }); return;
    }
    actionLock.current = true; setBusy(true); setError(null);
    try {
      let current = chat?.id === chatId ? chat : chatId ? await request<ChatDetail>(`/api/chat/sessions/${encodeURIComponent(chatId)}`) : null;
      if (!current) {
        const body: ChatCreateRequest = { projectDir: target, provider: defaults.provider, model: draftModel };
        current = await request<ChatDetail>("/api/chat/sessions", body);
        setDrafts((value) => ({ ...value, [current!.id]: content }));
        setDraftAttachments((value) => ({ ...value, [key]: [], [current!.id]: files }));
        if (selection.current.projectDir === target && selection.current.chatId === chatId) { setChatId(current.id); setChat(current); }
      }
      if (current.archived) { setChat(current); return; }
      await ensureProject(target, current.projectId);
      const body: ChatPostRequest = { content, projectId: current.projectId };
      if (requestedContext && contextProjectId === current.projectId) {
        body.designContext = requestedContext;
        body.contextMode = "stage";
      }
      if (requestedContextMode === "project") {
        if (!body.designContext) throw new Error(t.contextOpenProject);
        body.contextMode = "project";
      }
      if (files.length) body.attachments = await Promise.all(files.map(async (file) => ({ name: file.name, mimeType: file.type || "application/octet-stream", data: await fileData(file, t.attachmentRead) })));
      const posted = await request<ChatDetail>(`/api/chat/sessions/${current.id}/messages`, body);
      if (selection.current.projectDir === target && (selection.current.chatId === chatId || selection.current.chatId === current.id)) { setChatId(posted.id); setChat(posted); }
      setDrafts((value) => ({ ...value, [key]: "", [posted.id]: "" }));
      setDraftAttachments((value) => ({ ...value, [key]: [], [posted.id]: [] }));
      setContextModes((value) => ({ ...value, [key]: "continue", [posted.id]: "continue" }));
      await refresh();
      // Whoever just sent follows the reply.
      followLatest.current = true; setLatest({ away: false, unseen: 0 });
      requestAnimationFrame(() => toLatest());
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
  /** A path as it was pasted: Explorer quotes the whole thing, the API does not. */
  const asPath = (value: string) => value.trim().replace(/^"(.*)"$/, "$1");
  /** Write this project's retained snapshot to one portable archive file. */
  const exportArchive = async (event: FormEvent) => {
    event.preventDefault(); if (actionLock.current || !project || !archivePath.trim()) return;
    actionLock.current = true; setBusy(true); setDialogError(null); setArchiveSummary(null);
    try {
      setArchiveSummary(await request<ProjectArchiveSummary>("/api/project/archive/export",
        { projectDir: project.projectDir, archivePath: asPath(archivePath) } satisfies ProjectArchiveExportRequest));
    } catch (cause) { setDialogError(asFailure(cause)); }
    finally { actionLock.current = false; setBusy(false); }
  };
  /** Install one archive as a project of its own, beside the listed ones. */
  const restoreArchive = async (event: FormEvent) => {
    event.preventDefault(); if (actionLock.current || !restorePath.trim()) return;
    actionLock.current = true; setBusy(true); setDialogError(null); setRestoreResult(null);
    try {
      const result = await request<ProjectArchiveRestoreResult>("/api/project/archive/restore",
        { archivePath: asPath(restorePath), targetParent: asPath(restoreTarget) || null } satisfies ProjectArchiveRestoreRequest);
      setProjects((items) => [...items.filter((item) => item.projectDir !== result.project.projectDir), result.project]);
      setRestoreResult(result);
    } catch (cause) { setDialogError(asFailure(cause)); }
    finally { actionLock.current = false; setBusy(false); }
  };
  /** What one archive holds, as both dialogs state it; `target` is where it landed. */
  const archiveSummaryList = (summary: ProjectArchiveSummary, target?: string) => <>
    <dl className="chat-archive-summary">
      <dt>{t.archiveSummaryProject}</dt><dd>{summary.projectId} · {t.versionNumber(summary.version)}</dd>
      <dt>{t.archiveSummarySize}</dt><dd>{fileSize(summary.archiveBytes)}</dd>
      <dt>{t.archiveSummaryContents}</dt><dd>{t.archiveSummaryFiles(summary.fileCount)} · {t.archiveSummaryRuns(summary.runCount)}</dd>
      <dt>{t.archiveSummaryCategories}</dt><dd>{Object.entries(summary.categories).map(([name, count]) => `${name} ${count}`).join(" · ")}</dd>
      <dt>{t.archiveSummaryOmitted}</dt><dd><ul>{summary.omissions.map((item) => <li key={item}>{item}</li>)}</ul></dd>
      <dt>{t.archiveSummaryExternal}</dt><dd>{summary.externalDependencies.length
        ? <ul>{summary.externalDependencies.map((item) => <li key={item}>{item}</li>)}</ul> : t.archiveSummaryExternalNone}</dd>
      <dt>{t.archivePath}</dt><dd className="chat-project-card__path">{summary.archivePath}</dd>
      {target !== undefined && <><dt>{t.archiveSummaryTarget}</dt><dd className="chat-project-card__path">{target}</dd></>}
    </dl>
    {summary.verified && <p className="chat-muted">{t.archiveSummaryVerified}</p>}
  </>;
  /** `view` names what this page should open, such as the candidate a step produced. */
  const openTool = async (id: AppId, view?: Record<string, string>) => {
    const needsProject = id !== "monkeyfab" && id !== "monkeymonitor";
    if (needsProject && !projectDir) return false;
    // An explicit system-page choice supersedes even a stale project link.
    if (id === "monkeymonitor") routeRestored.current = true;
    const existing = tabs.find((item) => needsProject ? item.projectDir === projectDir : item.id === id);
    if (existing && id !== "monkeyarch") {
      setTabs((items) => items.map((item) => item === existing ? { ...item, id,
        // Drawing and Render open over the surface already shown, in this same project workspace.
        returnTo: !returnsToSurface(id) ? undefined : returnsToSurface(item.id) ? item.returnTo : item.id,
        candidate: view?.candidate ?? item.candidate, followHead: view?.candidate ? view.follow === "head" : item.followHead,
        url: needsProject ? `${window.location.origin}/?${new URLSearchParams({ runtimeId: item.runtimeId!, view: id === "monkeyboard" ? "board" : id === "publish" ? "publish" : id === "drawing" ? "drawing" : id === "monkeyrender" ? "render" : id === "tree" ? "tree" : "arch" })}` : item.url } : item));
      setPanel(true); setActiveTool(id); setError(null);
      return true;
    }
    // Monitor connects its data service inside the Hub, so even an unavailable
    // service cannot replace application navigation or prevent leaving the page.
    if (id === "monkeymonitor") {
      setTabs((items) => [...items.filter((item) => item.id !== id), { id,
        url: `${window.location.origin}/?view=monitor`, revision: 0 }]);
      setPanel(true); setActiveTool(id); setError(null);
      return true;
    }
    if (actionLock.current) return false;
    setPanel(true);
    const target = projectDir, targetChat = chatId;
    actionLock.current = true; setToolBusy(id); setError(null);
    try {
      const location = needsProject ? await ensureProject(target!, project!.projectId, id)
        : apps?.find((item) => item.appId === id && item.state === "running")?.url ?? await startTool(id);
      let tab: ToolTab;
      if (needsProject) {
        const attached = runtimeAttachments.current.get(target!);
        if (!attached || attached.projectId !== project!.projectId) throw new Error("The project runtime has not been attached.");
        tab = { id, projectDir: target!, projectId: attached.projectId, runtimeId: attached.runtimeId, candidate: view?.candidate ?? existing?.candidate,
          followHead: view?.candidate ? view.follow === "head" : existing?.followHead, revision: existing?.revision ?? 0,
          url: `${window.location.origin}/?${new URLSearchParams({ runtimeId: attached.runtimeId, view: id === "monkeyboard" ? "board" : id === "publish" ? "publish" : id === "drawing" ? "drawing" : id === "monkeyrender" ? "render" : id === "tree" ? "tree" : "arch" })}` };
      } else {
        tab = { id, url: applicationUrl(location, preferences), revision: 0 };
      }
      // A late result remains attached to the project that requested it.
      setTabs((items) => [...items.filter((item) => needsProject ? item.projectDir !== target : item.id !== id), tab]);
      if (!needsProject || (selection.current.projectDir === target && selection.current.chatId === targetChat)) setActiveTool(id);
      return true;
    } catch (cause) {
      if (!needsProject || (selection.current.projectDir === target && selection.current.chatId === targetChat)) setError(asFailure(cause));
      return false;
    }
    finally { actionLock.current = false; setToolBusy(null); }
  };
  /** Leaving the Drawing tool goes back to the surface it was opened over, in the
      same mounted project workspace. Opened straight from the conversation, it
      closes the panel again. The project, its editing base and drawings stay. */
  const toolReturn = returnsToSurface(selectedTab?.id) ? selectedTab!.returnTo : undefined;
  const leaveTool = () => { if (toolReturn) void openTool(toolReturn); else setPanel(false); };

  const initialRuntimeRoute = useRef(new URLSearchParams(window.location.search).get("runtimeId")).current;
  const initialMonitorRoute = useRef(new URLSearchParams(window.location.search).get("view") === "monitor").current;
  const routeRestored = useRef(initialRuntimeRoute === null && !initialMonitorRoute);
  useEffect(() => {
    if (initialMonitorRoute && !routeRestored.current) {
      restoredTools.current = true;
      void openTool("monkeymonitor").then((opened) => { if (opened) routeRestored.current = true; });
      return;
    }
    const query = new URLSearchParams(window.location.search);
    const runtimeId = query.get("runtimeId");
    if (!runtimeId || routeRestored.current || !runtime) return;
    const attached = runtime.projects.find((item) => item.runtimeId === runtimeId);
    const match = attached && projects.find((item) => item.projectDir === attached.projectDir && item.projectId === attached.projectId);
    if (!match) return;
    if (projectDir !== match.projectDir) { selectProject(match); return; }
    if (actionLock.current || busy || toolBusy || !attached.workers?.some((worker) => worker.serviceId === "studio" && worker.healthy)) return;
    const id = query.get("view") === "board" ? "monkeyboard" : query.get("view") === "publish" ? "publish" : query.get("view") === "drawing" ? "drawing" : query.get("view") === "render" ? "monkeyrender" : query.get("view") === "tree" ? "tree" : "monkeyarch";
    const saved = initial.projectDir === match.projectDir && initial.activeTool === id
      ? initial.tools.find((item) => item.id === id) : undefined;
    void openTool(id, saved?.candidate ? { candidate: saved.candidate } : undefined)
      .then((opened) => { if (opened) routeRestored.current = true; });
  }, [runtime, projects, projectDir, busy, toolBusy, initial, initialMonitorRoute]);
  useEffect(() => {
    // Let an explicit incoming workspace link resolve before reflecting navigation.
    if (!routeRestored.current) return;
    const url = new URL(window.location.href);
    if (panel && selectedTab?.id === "monkeymonitor") {
      url.searchParams.delete("runtimeId");
      url.searchParams.set("view", "monitor");
    } else if (selectedTab?.runtimeId) {
      url.searchParams.set("runtimeId", selectedTab.runtimeId);
      url.searchParams.set("view", selectedTab.id === "monkeyboard" ? "board" : selectedTab.id === "publish" ? "publish" : selectedTab.id === "drawing" ? "drawing" : selectedTab.id === "monkeyrender" ? "render" : selectedTab.id === "tree" ? "tree" : "arch");
    } else {
      url.searchParams.delete("runtimeId");
      if (["arch", "board", "drawing", "render", "publish", "tree", "monitor"].includes(url.searchParams.get("view") ?? "")) url.searchParams.delete("view");
    }
    window.history.replaceState(null, "", url);
  }, [selectedTab?.runtimeId, selectedTab?.id, projectDir, panel]);

  // Save view choices, not old worker URLs. Reopening always resolves the live host.
  useEffect(() => {
    if (restoredTools.current || !project) return;
    if (initialRuntimeRoute || initialMonitorRoute) { restoredTools.current = true; return; }
    if (!initial.tools.length || initial.projectDir !== projectDir) { restoredTools.current = true; return; }
    if (!studioWorker?.healthy || busy || toolBusy || actionLock.current) return;
    restoredTools.current = true;
    void (async () => {
      for (const item of initial.tools) {
        if (selection.current.projectDir !== initial.projectDir) return;
        // A restored pin is not today's choice: the workspace follows the project's head.
        await openTool(item.id, item.candidate ? { candidate: item.candidate, follow: "head" } : undefined);
      }
      if (selection.current.projectDir === initial.projectDir) { setActiveTool(initial.activeTool); setPanel(initial.panel ?? false); }
    })();
  }, [project, projectDir, studioWorker, busy, toolBusy, initial]);

  useEffect(() => {
    // openTool releases its ref lock before React commits the restored tab.
    // Wait for that render before observing a delivery against its candidate.
    if (!runtime || !restoredTools.current || actionLock.current || toolBusy) return;
    const updates = new Map<string, { tab: ToolTab; previous?: string; candidate: string }>();
    // A headless delivery can arrive before the architect opens any tool.
    // Promote the already prepared workspace instead of mounting another one.
    const targets: ToolTab[] = [...tabs];
    if (projectRuntime?.projection === "ready" && studioWorker?.healthy &&
        !targets.some((tab) => tab.runtimeId === projectRuntime.runtimeId)) {
      targets.push({ id: "monkeyarch", revision: 0, projectDir: projectRuntime.projectDir,
        projectId: projectRuntime.projectId, runtimeId: projectRuntime.runtimeId,
        url: `${window.location.origin}/?${new URLSearchParams({ runtimeId: projectRuntime.runtimeId, view: "arch" })}` });
    }
    for (const tab of targets) {
      const current = runtime.projects.find((item) => item.runtimeId === tab.runtimeId &&
        item.projectId === tab.projectId && item.projectDir === tab.projectDir);
      if (!current?.retained || current.retained.projectId !== current.projectId || current.retained.projectDir !== current.projectDir) continue;
      const jobs = new Map(current.retained.jobs.map((job) => [job.jobId, job]));
      const retained = new Map(current.retained.candidates.map((candidate) => [candidate.candidateId, candidate]));
      const completed = (current.operations ?? []).filter((item) => {
        const candidate = item.candidateId ? retained.get(item.candidateId) : undefined;
        const job = item.jobId ? jobs.get(item.jobId) : undefined;
        return item.projectId === current.projectId && item.status === "completed" && item.resultDigest &&
          candidate?.receiptRef && ["completed", "succeeded"].includes(candidate.status) &&
          [candidate.resultStateDigest, candidate.resultRecordDigest].includes(item.resultDigest) &&
          (!job || (job.candidateId === item.candidateId && job.status === "succeeded"));
      });
      const choices = completed.filter((item) => item.source === "studio" && !item.sessionId && !item.committed)
        .map((item) => ({ candidate: item.candidateId!, sequence: Number.isSafeInteger(item.admissionSequence) && item.admissionSequence! > 0
          ? item.admissionSequence! : NaN }));
      const previous = observedRuntimeCandidates.current.get(current.runtimeId);
      const unseen = choices.filter((item) => !previous?.completed.has(item.candidate));
      const sequence = Math.max(previous?.sequence ?? -Infinity, ...choices.map((item) => item.sequence).filter(Number.isFinite));
      observedRuntimeCandidates.current.set(current.runtimeId, {
        completed: new Set([...(previous?.completed ?? []), ...choices.map((item) => item.candidate)]), sequence,
      });
      // The journal supplies request order even after jobs leave memory. It
      // never proves success: the retained receipt and digest above do that.
      // Older slow requests and unordered legacy observations cannot win.
      if (!unseen.length || unseen.some((item) => !Number.isFinite(item.sequence))) continue;
      unseen.sort((a, b) => b.sequence - a.sequence);
      const newest = unseen[0]!;
      if (newest.sequence <= (previous?.sequence ?? -Infinity) || unseen[1]?.sequence === newest.sequence) continue;
      const latestCompletedRequest = Math.max(...completed.map((item) => item.admissionSequence ?? NaN).filter(Number.isFinite));
      if (newest.sequence < latestCompletedRequest || newest.candidate === tab.candidate) continue;
      updates.set(current.runtimeId, { tab, previous: tab.candidate, candidate: newest.candidate });
    }
    if (!updates.size) return;
    setTabs((items) => {
      const next = items.map((item) => {
        const update = item.runtimeId ? updates.get(item.runtimeId) : undefined;
        // Keep the same workspace mounted, including a Board with unsent marks.
        // A manual choice made meanwhile wins; polling never reopens old results.
        return update && item.candidate === update.previous ? { ...item, candidate: update.candidate, followHead: false } : item;
      });
      for (const [runtimeId, update] of updates) {
        if (!next.some((item) => item.runtimeId === runtimeId)) next.push({ ...update.tab, candidate: update.candidate, followHead: false });
      }
      return next;
    });
    const delivered = projectRuntime && updates.get(projectRuntime.runtimeId);
    if (delivered && activeTool !== "monkeymonitor" && activeTool !== "monkeyfab" &&
        !(initialMonitorRoute && activeTool === null)) {
      setPanel(true);
      setActiveTool(delivered.tab.id);
    }
  }, [runtime, tabs, projectRuntime, studioWorker?.healthy, projectDir, busy, toolBusy, activeTool, initialMonitorRoute]);

  // Show each successful candidate as soon as it is read back. A turn can keep
  // working on drawings afterward; later readback never reloads the same view.
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

  /** The user's words and the Agent's answer, with their files: never folded. */
  const messageEntry = (message: ChatMessage) => <article className={`chat-message chat-message--${message.role}`} key={message.id}>
    {message.role === "user" && message.contextMode === "project" && <p className="chat-muted">{t.contextProjectMessage}</p>}
    {message.role === "user" && message.contextMode === "stage" && <p className="chat-muted">{t.contextStageMessage}: {message.confirmedStageLabel}</p>}
    {message.role === "user" && message.interjection && <p className="chat-muted" data-interjection={message.interjection}>{t.interjected} · {{ pending: t.interjectionPending, delivered: t.interjectionDelivered, restarted: t.interjectionRestarted, undelivered: t.interjectionUndelivered }[message.interjection]}</p>}
    <ChatMarkdown text={message.content} />
    <ChatMessageFiles sessionId={chat!.id} messageId={message.id} attachments={message.attachments} documents={message.documents} labels={t}
      documentBusy={busy || Boolean(toolBusy)} onOpenDocument={project && project.projectId === chat!.projectId ? (document: ChatDocument) => {
        // Capture the project before preparation; changing chats while it
        // starts must never open this document in the new project.
        const target = project.projectDir;
        const requestId = ++documentRequestSequence.current;
        setDocumentRequests((current) => ({ ...current, [target]: { source: {
          runId: document.runId, assetSha256: document.assetSha256,
          revisionRef: document.revisionRef ?? null, pageIndex: document.pageIndex ?? 0,
        }, requestId } }));
        void openTool("monkeyboard");
      } : undefined} />
    {message.status === "failed" || message.status === "interrupted" ? <p className="chat-muted">{t[message.status]}</p> : null}</article>;
  return <div className="chat-shell" data-sidebar={sidebar} data-panel={panel} style={{ "--browser-width": `${panelWidth}px` } as CSSProperties}>
    <aside className="chat-sidebar" aria-label={t.projects}>
      <div className="chat-sidebar__top"><strong className="wordmark">MonkeyHub</strong><button className="chat-icon" aria-label={sidebar ? t.collapse : t.expand} onClick={() => setSidebar(!sidebar)}><Icon name="sidebar" /></button></div>
      <div className="chat-sidebar__body">
        <button className="chat-new" onClick={() => { setArchivedView(false); setChatId(null); setChat(null); setError(null); input.current?.focus(); }}><Icon name="plus" /><span>{t.newChat}</span></button>
        <button className="chat-new chat-new--project" onClick={() => { setDialogError(null); newDialog.current?.showModal(); }}><Icon name="folder" /><span>{t.newProject}</span></button>
        {/* #300: Agent work running or waiting in any open project, one entry away. */}
        <button className="chat-new chat-tasks-toggle" aria-expanded={tasksOpen} aria-controls="chat-tasks" onClick={() => setTasksOpen(!tasksOpen)}>
          <Icon name="tasks" /><span>{t.tasks}</span>
          {tasks.length > 0 && <><span className="chat-count" aria-hidden="true">{tasks.length}</span><span className="sr-only">{t.tasksActive(tasks.length)}</span></>}
        </button>
        {tasksOpen && <ul className="chat-tasks" id="chat-tasks" aria-label={t.tasks}>
          {tasks.length ? tasks.map((task) => {
            const action = task.action ? stepText(describeCall(task.action), processWords(t)) : null;
            const title = task.chat?.title ?? action ?? t.workTools;
            // Where and whether it runs come before what it is doing: a narrow sidebar cuts the end.
            const caption = [task.projectName, task.state === "running" ? t.taskRunning : t.taskQueued, task.chat ? action : null].filter(Boolean).join(" · ");
            return <li key={task.key}><button type="button" className="chat-task" data-state={task.state} title={`${title}\n${caption}`} onClick={() => openTask(task)}>
              <span className="chat-thread__dot" data-status={task.state === "running" ? "running" : "idle"} />
              <span className="chat-task__text"><span className="chat-task__title">{title}</span><small>{caption}</small></span>
            </button></li>;
          }) : <li className="chat-muted chat-tasks__empty">{t.tasksEmpty}</li>}
        </ul>}
        <div className="chat-project-label"><span>{archivedView ? t.archivedChats : t.projects}</span><button className="chat-icon" aria-label={t.addExisting} title={t.addExisting} onClick={() => { setDialogError(null); addDialog.current?.showModal(); }}><Icon name="plus" /></button></div>
        {!projects.length && <p className="chat-muted chat-project-empty">{loading ? t.loading : t.emptyProjects}</p>}
        {archivedView && !visibleSessions.length && <p className="chat-muted chat-project-empty">{t.archiveEmpty}</p>}
        {projects.filter((item) => !archivedView || visibleSessions.some((session) => session.projectDir === item.projectDir)).map((item) => <section className="chat-project" key={item.projectDir} data-selected={item.projectDir === projectDir}>
          <div className="chat-project__head">
            <button className="chat-project__name" title={item.projectDir} onClick={() => selectProject(item)}
              aria-description={busyProjects.get(item.projectDir) ? t.projectBusy(busyProjects.get(item.projectDir)!) : undefined}><Icon name="folder" /><span>{item.name}</span></button>
            {/* #300: a small badge while the project has work running or waiting, on the project's own line. */}
            {busyProjects.has(item.projectDir) && <span className="chat-project__badge" data-kind="running" aria-hidden="true">{t.projectBusyBadge}</span>}
            {/* #300 hook: the "N new" schemes badge, drawn from #294's admission data (S1–S2);
                newSchemes() answers null until the runtime reports it, so no number is guessed. */}
            {(() => {
              const count = newSchemes(runtime?.projects.find((row) => row.projectDir === item.projectDir && row.projectId === item.projectId));
              return count ? <span className="chat-project__badge" data-kind="new">{t.projectNewSchemes(count)}</span> : null;
            })()}
          </div>
          {visibleSessions.filter((session) => session.projectDir === item.projectDir).map((session) => <div key={session.id} className="chat-thread-row">
            <button className="chat-thread" aria-current={session.id === chatId ? "page" : undefined} onClick={() => selectChat(session)} title={session.title}>
              <span className="chat-thread__dot" data-status={session.status} /><span>{session.title}{session.sourceSessionId && <small className="chat-external-badge">{t.externalChat}</small>}</span>
            </button>
            <button className="chat-icon chat-thread-action" aria-label={`${session.archived ? t.restore : t.archive}: ${session.title}`}
              title={session.status === "running" ? t.archiveRunning : session.archived ? t.restore : t.archive}
              disabled={session.status === "running" || busy || modelBusy || archiveBusy !== null}
              onClick={() => void setArchived(session, !session.archived)}><Icon name={session.archived ? "restore" : "archive"} /></button>
          </div>)}
        </section>)}
      </div>
      <div className="chat-sidebar__footer">
        {/* #300: the last seven days of model usage, as MonkeyMonitor recorded it. */}
        <button className="chat-settings chat-usage" aria-label={t.usageName(usageFigure?.full ?? null)} title={t.usageTitle} onClick={() => void openTool("monkeymonitor")}>
          <Icon name="chart" /><span>{t.monitor}</span>{usageFigure && <small className="chat-usage__figure" aria-hidden="true">{t.usageWeek(usageFigure.short)}</small>}
        </button>
        {updateReady && <button className="chat-settings chat-update" title={t.updateReadyHint} onClick={openSoftwareUpdate}><Icon name="update" /><span>{t.updateReady}</span></button>}
        <button className="chat-settings chat-archive-toggle" aria-pressed={archivedView} onClick={() => setArchivedView(!archivedView)}><Icon name="archive" /><span>{archivedView ? t.activeChats : t.archivedChats}</span></button>
        <button className="chat-settings" onClick={openSettings}><Icon name="settings" /><span>{t.settings}</span></button>
      </div>
    </aside>
    <main className="chat-main">
      <header className="chat-header"><button className="chat-icon mobile-project-toggle" aria-label={sidebar ? t.collapse : t.expand} onClick={() => setSidebar(!sidebar)}><Icon name="sidebar" /></button><div><span className="chat-header__project">{project?.name ?? "MonkeyHub"}</span><h1>{chat?.id === chatId ? chat.title : t.newChat}{external && <small className="chat-external-badge">{t.externalChat}</small>}</h1></div></header>
      {(!eventsConnected || crashed || recovering || recoverableOperation || workCopyRefusal) && <div className="chat-runtime" role="status" aria-live="polite">
        <div>{!eventsConnected && <p>{t.reconnecting}</p>}
          {(crashed || recovering) && <><p>{recovering ? t.recovering : t.workerCrashed}</p><small>{t.recoveryHint}</small></>}
          {recoverableOperation && <p>{recoverableOperation.status === "needs_recovery" ? t.operationRecovery : recoverableOperation.status === "stale" ? t.operationStale : t.operationFailed}</p>}
          {workCopyRefusal && <><p>{t.workCopyRefused}</p><small>{workCopyRefusal.detail}</small></>}
        </div>
        {crashed && <button type="button" className="chat-activity__open" disabled={recovering || busy || Boolean(toolBusy)} onClick={() => void recoverWorker()}><Icon name="refresh" />{recovering ? t.recovering : t.recoverWorker}</button>}
      </div>}
      <div className="chat-messages" ref={messages} role="log" aria-live="polite" aria-relevant="additions text" onScroll={readPosition}
        onWheel={() => { jumping.current = false; }} onTouchStart={() => { jumping.current = false; }}>
        {!shownMessages?.length ? <div className="chat-welcome"><div className="chat-welcome__mark"><Icon name="chat" /></div><h2>{project ? t.empty : t.noProject}</h2><p>{t.emptyHint}</p>{!project && <div className="chat-welcome__actions"><button className="btn btn--primary" onClick={() => { setDialogError(null); newDialog.current?.showModal(); }}>{t.newProject}</button><button className="btn" onClick={() => { setDialogError(null); addDialog.current?.showModal(); }}>{t.addExisting}</button></div>}</div>
          : <div className="chat-message-list">{turns.map((turn, index) => <Fragment key={turn.key}>
            {turn.user && messageEntry(turn.user)}
            {turn.steps.length > 0 && <ProcessRow turn={turn} running={running && index === turns.length - 1} open={expandedTurns.has(turn.key)} t={t}
              onToggle={(row) => toggleTurn(turn.key, row)} />}
            {turn.visible.map((message) => message.role !== "tool" ? messageEntry(message)
              : message.id.includes(":progress:") ? <article className="chat-progress" key={message.id} data-status={message.status}>
                <div className="chat-progress__heading"><span className="chat-thread__dot" data-status={message.status === "streaming" ? "running" : message.status === "failed" ? "failed" : "idle"} />{t.progress}</div>
                <ChatMarkdown text={message.content} />
                {(message.status === "failed" || message.status === "interrupted") && <p className="chat-muted">{t[message.status]}</p>}
              </article>
              : <div className="chat-activity chat-activity--permission" key={message.id} data-status={message.status}>
                <div className="chat-permission" role="group" aria-label={message.permission!.title} aria-busy={permissionBusy === message.permission!.id}>
                  <p>{message.permission!.title}</p>
                  <div className="chat-permission__actions">{message.permission!.options.map((option) => <button key={option.optionId} type="button" className="chat-activity__open" disabled={permissionBusy !== null}
                    onClick={() => void choosePermission(message.permission!.id, option.optionId)}>{option.name}</button>)}
                    <button type="button" className="chat-activity__open" disabled={permissionBusy !== null} onClick={() => void choosePermission(message.permission!.id, null)}>{t.cancel}</button>
                  </div>
                </div>
              </div>)}
            {turn.results.map((message) => <div className="chat-activity__result" key={`${message.id}:result`}><button type="button" className="chat-activity__open" title={message.candidateId ?? undefined} disabled={!project || Boolean(toolBusy)}
                  onClick={() => void openTool("monkeyarch", { candidate: message.candidateId! })}><Icon name="cube" /><span>{t.openCandidate}</span></button><span className="chat-muted">{t.candidateHint}</span></div>)}
          </Fragment>)}</div>}
        {running && !turns.at(-1)?.steps.length && <div className="chat-thinking" role="status"><span className="chat-thread__dot" data-status="running" />{t.thinking}</div>}
      </div>
      <div className="chat-composer-wrap">
        {latest.away && Boolean(shownMessages?.length) && <button type="button" className="chat-jump" onClick={jumpToLatest}
          aria-label={latest.unseen ? t.jumpLatestNew(latest.unseen) : t.jumpLatest} title={latest.unseen ? t.jumpLatestNew(latest.unseen) : t.jumpLatest}>
          <Icon name="down" />{latest.unseen > 0 && <span className="chat-jump__count" aria-hidden="true">{latest.unseen > 99 ? "99+" : latest.unseen}</span>}
        </button>}
        {(error ?? (chat?.id === chatId ? chat?.error : null)) && <Failure failure={(error ?? chat!.error)!} language={preferences.language} labels={t} connection={connectionName}
          onClose={error ? () => setError(null) : undefined}
          onChangeModel={running || archived || external ? undefined : () => {
            const picker = document.getElementById("chat-model") as HTMLSelectElement | null;
            picker?.focus();
            try { (picker as unknown as { showPicker?: () => void })?.showPicker?.(); } catch { /* A browser that will not open it still focused it. */ }
          }} />}
        {archived ? <div className="chat-archived-notice">
          <p>{t.archivedNotice}</p>
          <button className="chat-activity__open" disabled={archiveBusy !== null} onClick={() => void setArchived(chat!, false)}><Icon name="restore" /><span>{t.restoreChat}</span></button>
        </div> : external ? <div className="chat-external-notice" role="status"><strong>{t.externalChat}</strong><p>{t.externalNotice}</p></div> : <form className="chat-composer" data-dragging={draggingFiles} onSubmit={(event) => void send(event)}
          onDragOver={(event) => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); event.dataTransfer.dropEffect = !project || busy ? "none" : "copy"; setDraggingFiles(Boolean(project) && !busy); } }}
          onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDraggingFiles(false); }}
          onDrop={(event) => { if (event.dataTransfer.files.length) { event.preventDefault(); addAttachments(Array.from(event.dataTransfer.files)); } setDraggingFiles(false); }}
          onPaste={(event) => { if (event.clipboardData.files.length) { event.preventDefault(); addAttachments(Array.from(event.clipboardData.files)); } }}>
          {draggingFiles && <p className="chat-attachment-drop" role="status">{t.dropFiles}</p>}
          {Boolean(attachments.length) && <ul className="chat-attachments chat-attachments--draft" aria-label={t.attachments}>{attachments.map((file, index) => <li key={`${index}:${file.name}`}>
            <Icon name="file" /><span className="chat-attachment__details"><span className="chat-attachment__name" title={file.name}>{file.name}</span><span className="chat-attachment__size">{fileSize(file.size)}</span></span>
            <button type="button" className="chat-icon" aria-label={`${t.removeAttachment}: ${file.name}`} disabled={busy} onClick={() => {
              setDraftAttachments((value) => ({ ...value, [draftKey]: value[draftKey]!.filter((_, position) => position !== index) }));
              input.current?.focus();
            }}><Icon name="close" /></button>
          </li>)}</ul>}
          <label className="sr-only" htmlFor="chat-input">{t.placeholder}</label><textarea id="chat-input" ref={input} value={draft} placeholder={!project ? t.projectRequired : running ? t.interjectPlaceholder : t.placeholder} disabled={!project || busy}
            onChange={(event) => setDrafts((value) => ({ ...value, [draftKey]: event.target.value }))} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} />
          <input ref={fileInput} type="file" multiple hidden aria-label={t.attach} disabled={!project || busy} onChange={(event) => { addAttachments(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
          <label className="chat-context-option" title={designContext ? t.contextProjectHint : contextUnavailable}>
            <input type="checkbox" checked={contextMode === "project"} aria-description={designContext ? t.contextProjectHint : contextUnavailable} disabled={busy || running || (!designContext && contextMode !== "project")}
              onChange={(event) => setContextModes((value) => ({ ...value, [draftKey]: event.target.checked ? "project" : "continue" }))} />
            {t.contextProject}
          </label>
          {contextMode === "project" && <p className="chat-muted" role="status">{designContext ? t.contextProjectHint : contextUnavailable}</p>}
          <div className="chat-composer__bottom"><button type="button" className="chat-icon chat-attach" aria-label={t.attach} title={t.attach} disabled={!project || busy} onClick={() => fileInput.current?.click()}><Icon name="plus" /></button><div className="chat-connection" title={running ? t.modelRunning : t.connectionHint}>
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
          {running ? <><button className="chat-icon chat-stop" type="button" aria-label={t.stop} title={t.stop} onClick={() => void stop()}><Icon name="stop" /></button>
            <button className="chat-send" type="submit" aria-label={t.interject} title={t.interject} disabled={!draft.trim()}><Icon name="send" /></button></> : <button className="chat-send" type="submit" aria-label={t.send} disabled={busy || !project || (!draft.trim() && !attachments.length) || (!chatId && !availableProvider?.available)}><Icon name="send" /></button>}</div>
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
    {(panel || workspaceTabs.length > 0) && <aside className="chat-browser" aria-label={t.browser} hidden={!panel} inert={!panel} aria-hidden={!panel}>
      <div className="chat-browser__pages">{workspaceTabs.map((item) => {
        const visible = panel && item === selectedTab;
        if (item.id === "monkeymonitor") return <div className="chat-monitor-workspace" key={`${item.id}:${item.revision}`} hidden={!visible} inert={!visible}>
          <ErrorBoundary label={t.monitor}><MonitorPage preferences={preferences} active={visible} /></ErrorBoundary>
        </div>;
        if (item.runtimeId) return <div className="chat-project-workspace project-workspace" key={item.runtimeId} hidden={!visible} inert={!visible}>
          <ProjectRuntimeProvider baseUrl={`${window.location.origin}/api/runtime/projects/${item.runtimeId}/studio`}>
            <ErrorBoundary label={t.tools}><Suspense fallback={<div role="status">{t.working}</div>}>
              <ProjectWorkspace workspace={item.id === "monkeyboard" ? "board" : item.id === "publish" ? "publish" : item.id === "drawing" ? "drawing" : item.id === "monkeyrender" ? "render" : item.id === "tree" ? "tree" : "arch"} active={visible}
                expectedProjectId={item.projectId} candidateRunId={item.candidate} candidateFollowsHead={item.followHead} refreshKey={item.revision} onChatRequest={focusConversation}
                documentRequest={item.projectDir ? documentRequests[item.projectDir] : undefined}
                onDesignContextChange={workspaceContextCallback(item.runtimeId)}
                onWorkspaceChange={(workspace) => { const id = workspace === "board" ? "monkeyboard" : workspace === "publish" ? "publish" : workspace === "drawing" ? "drawing" : workspace === "render" ? "monkeyrender" : workspace === "tree" ? "tree" : "monkeyarch";
                  setTabs((items) => items.map((tab) => tab.runtimeId === item.runtimeId ? { ...tab, id,
                    url: `${window.location.origin}/?${new URLSearchParams({ runtimeId: item.runtimeId!, view: workspace })}` } : tab));
                  if (selection.current.projectDir === item.projectDir) setActiveTool(id);
                }} />
            </Suspense></ErrorBoundary>
          </ProjectRuntimeProvider>
        </div>;
        return <iframe key={`${item.id}:${item.revision}`} src={item.url} title={t[tools.find((tool) => tool.id === item.id)!.label]}
          hidden={!visible} inert={!visible} allow="clipboard-read; clipboard-write" />;
      })}</div>
      {!selectedTab && <div className="chat-browser__empty"><Icon name="panel" /><h2>{toolBusy ? t.working : t.toolEmpty}</h2><p>{t.toolHint}</p></div>}
    </aside>}
    {/* One column of entries for the whole right-hand side: the tools, this
        conversation's project, and whether the tool content is open at all. */}
    <nav className="chat-rail" aria-label={t.rail}>
      {/* The workspaces of this project, then the tools used over it (Drawing on
          the project's state, Usage on the machine): one rail, two kinds of
          entry, told apart on sight. */}
      {railGroups.map((group) => <div className="chat-rail__group" key={group.id} role="group" aria-label={t[group.caption]}>
        <span className="chat-rail__caption">{t[group.caption]}</span>
        {tools.filter((item) => item.group === group.id).map((item) => {
          const needsProject = item.id !== "monkeyfab" && item.id !== "monkeymonitor";
          const statuses = needsProject ? (projectApps?.projectDir === projectDir ? projectApps.apps : null) : apps;
          const status = statuses?.find((app) => app.appId === ((item.id === "drawing" || item.id === "publish" || item.id === "tree") ? "monkeyarch" : item.id));
          const state = needsProject && studioWorker?.state === "crashed" ? "error" : status?.state;
          const stateText = state === "unavailable" ? t.toolUnavailable : state === "error" ? t.toolError
            : state === "starting" ? t.toolStarting : state === "stopping" ? t.toolStopping
            : state === "running" || state === "stopped" ? undefined : t.toolUnknown;
          // Pressing an open Drawing or Render again leaves it for the surface it was opened over (#295).
          const leaves = returnsToSurface(item.id) && panel && activeTool === item.id && selectedTab?.id === item.id;
          const leaveText = !leaves ? undefined : toolReturn ? t.toolReturn(t[item.label], t[labelOf(toolReturn)]) : t.toolClose(t[item.label]);
          // Board stays pressed in its Layout mode.
          const pressed = panel && (item.id === activeTool || (item.id === "monkeyboard" && activeTool === "publish"));
          return <button key={item.id} className="chat-rail__tool" aria-label={t[item.label]} aria-pressed={pressed}
            title={status?.error?.detail ?? stateText ?? leaveText} data-state={state} disabled={(needsProject && !project) || (!currentTabs.some((tab) => tab.runtimeId || tab.id === item.id) && (busy || Boolean(toolBusy)))}
            onClick={() => { if (leaves) leaveTool(); else void openTool(item.id); }}><Icon name={item.icon} /><span>{t[item.label]}</span>{stateText && <small aria-hidden="true">{stateText}</small>}</button>;
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
          {/* #271: people see the current project, what is stale and what is running; candidate ids stay internal. */}
          {status && <><dt>{t.workCurrent}</dt><dd className="chat-project-card__head-line">{status.headLabel ?? t.workLatest}
            <small>{status.accepted ? t.workAccepted : t.workUnaccepted}</small></dd>
          <dt>{t.workStatus}</dt><dd><ul className="chat-project-card__status">
            <li data-state="current">{t.workModeling}</li>
            {status.drawings.current + status.drawings.stale + status.drawings.frozen > 0 && <li data-state={status.drawings.stale ? "stale" : "current"}
              title={graph?.representations.filter((row) => row.kind === "drawing" && row.state !== "current").map((row) => `${row.label}: ${row.detail ?? ""}`).join("\n") || undefined}>
              {t.workDrawings(status.drawings.current, status.drawings.stale, status.drawings.frozen)}</li>}
            {status.renders.current + status.renders.stale > 0 && <li data-state={status.renders.stale ? "stale" : "current"}>{t.workRenders(status.renders.current, status.renders.stale)}</li>}
            {status.background > 0 && <li data-state="running">{t.workBackground(status.background)}</li>}
            {status.unreadable > 0 && <li data-state="stale" title={graph?.warnings.join("\n")}>{t.workUnreadable(status.unreadable)}</li>}
          </ul></dd></>}
          <dt>{t.workLines}</dt><dd className="chat-project-card__work">
            {worktreeError && !graph ? <span className="chat-muted" role="status">{t.workUnavailable}</span>
              : rows.length === 0 ? <span className="chat-muted">{graph ? t.workNone : t.loading}</span>
              : rows.map((row) => <div className="chat-project-card__line" key={row.key} data-kind={row.kind} data-reconcile={row.reconcile}>
                <span>{row.kind === "branch" ? t.workBranch(graph?.lines.find((line) => line.lineId === row.key)?.branchId ?? "", row.label ?? "")
                  : `${row.owner} · ${row.status === "running" ? t.workRunning : row.status === "queued" ? t.workQueued : row.status === "interrupted" ? t.workInterrupted : t.workReady}`}</span>
                {row.kind !== "branch" && <small>{row.relation === "ahead" ? t.workAhead : row.relation === "behind" ? t.workBehind
                  : row.relation === "diverged" ? t.workDiverged : t.workSeparate}{row.kind === "result" && row.reconcile !== "none" && row.reconcile !== "conflict"
                  ? ` · ${row.reconcile === "can-combine" ? t.workCombine : t.workReview}` : ""}</small>}
                {row.conflicts.length > 0 && <small className="chat-project-card__conflict" role="note">{t.workConflict(row.conflicts.map(refLabel).join(", "))}</small>}
                {row.kind === "result" && row.runId && <button type="button" className="chat-activity__open" disabled={Boolean(toolBusy)}
                  onClick={() => { setProjectInfo(false); void openTool("monkeyarch", { candidate: row.runId! }); }}><Icon name="cube" /><span>{t.openCandidate}</span></button>}
              </div>)}
            <button type="button" className="chat-activity__open" onClick={() => setWorktreeRead((value) => value + 1)}><Icon name="refresh" /><span>{t.workRefresh}</span></button>
          </dd>
        </dl> : <><p>{t.projectNone}</p><p className="chat-muted">{t.projectNoneHint}</p></>}
        {/* The local address of the page on the right is a connection detail:
            available when it is asked for, not on screen all the time. */}
        {Boolean(projectRuntime?.operations?.length) && <details className="chat-project-card__connection" open={Boolean(recoverableOperation) || undefined}>
          <summary>{t.recoveryDetails}</summary>
          <p className="chat-muted">{t.runtimeOperations}</p>
          {projectRuntime!.operations!.map((operation) => <div className="chat-project-card__operation" key={operation.operationId}>
            <span>{operation.kind} · {operation.status}</span><small>{operation.committed ? t.operationCommitted : t.operationPending}</small>
            {operation.candidateId && <button type="button" className="chat-activity__open" disabled={Boolean(toolBusy)} onClick={() => void openTool("monkeyarch", { candidate: operation.candidateId! })}>{t.openCandidate}</button>}
          </div>)}
        </details>}
        <details className="chat-project-card__connection">
          <summary>{t.connectionDetails}</summary>
          {selectedTab ? <>
            <label className="sr-only" htmlFor="tool-url">{t.toolUrl}</label>
            <input id="tool-url" className="chat-project-card__url" readOnly value={selectedTab.url} onFocus={(event) => event.currentTarget.select()} />
            <div className="chat-project-card__connection-actions">
              <button type="button" className="chat-activity__open" onClick={() => setTabs((items) => items.map((item) => item === selectedTab ? { ...item, revision: item.revision + 1 } : item))}><Icon name="refresh" /><span>{t.refresh}</span></button>
              <span className="chat-muted">{t[tools.find((tool) => tool.id === activeTool)!.label]}</span>
            </div>
          </> : <p className="chat-muted">{t.connectionNone}</p>}
        </details>
        {/* A whole project moved as one file: written from what is retained,
            and read back into a folder of its own. Neither touches this view. */}
        <div className="chat-project-card__archive">
          <button type="button" className="chat-activity__open" disabled={!project || busy}
            onClick={() => { setDialogError(null); setArchiveSummary(null); setRestoreResult(null); archiveDialog.current?.showModal(); }}><Icon name="archive" /><span>{t.archiveProject}</span></button>
          <button type="button" className="chat-activity__open" disabled={busy}
            onClick={() => { setDialogError(null); setArchiveSummary(null); setRestoreResult(null); setRestoreTarget(workspace?.workspaceDir ?? ""); restoreDialog.current?.showModal(); }}><Icon name="restore" /><span>{t.restoreProject}</span></button>
        </div>
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
    <dialog ref={archiveDialog} className="chat-dialog chat-dialog--archive"><form onSubmit={(event) => void exportArchive(event)}>
      <div className="chat-dialog__heading"><h2>{t.archiveHeading}</h2><button type="button" className="chat-icon" aria-label={t.close} onClick={() => archiveDialog.current?.close()}><Icon name="close" /></button></div>
      <p className="chat-muted">{t.archiveHelp}</p>
      {project && <p className="chat-archive-project">{project.name} · {project.version === null || project.version === undefined ? t.versionUnknown : t.versionNumber(project.version)}</p>}
      <label>{t.archivePath}<input autoFocus value={archivePath} placeholder={t.archivePathPlaceholder} required
        onChange={(event) => { setArchivePath(event.target.value); setDialogError(null); }} /></label>
      <p className="chat-muted">{t.archivePathHelp}</p>
      {archiveSummary && <><p>{t.archiveExported}</p>{archiveSummaryList(archiveSummary)}</>}
      {dialogError && <Failure failure={dialogError} language={preferences.language} labels={t} />}
      <div className="chat-dialog__actions"><button type="button" className="btn" onClick={() => archiveDialog.current?.close()}>{t.close}</button>
        <button type="submit" className="btn btn--primary" disabled={!project || busy || !archivePath.trim()}>{busy ? t.archiveExporting : t.archiveExport}</button></div>
    </form></dialog>
    <dialog ref={restoreDialog} className="chat-dialog chat-dialog--archive"><form onSubmit={(event) => void restoreArchive(event)}>
      <div className="chat-dialog__heading"><h2>{t.restoreHeading}</h2><button type="button" className="chat-icon" aria-label={t.close} onClick={() => restoreDialog.current?.close()}><Icon name="close" /></button></div>
      <p className="chat-muted">{t.restoreHelp}</p>
      <label>{t.archivePath}<input autoFocus value={restorePath} placeholder={t.archivePathPlaceholder} required
        onChange={(event) => { setRestorePath(event.target.value); setDialogError(null); }} /></label>
      <label>{t.restoreTarget}<input value={restoreTarget} onChange={(event) => { setRestoreTarget(event.target.value); setDialogError(null); }} /></label>
      <p className="chat-muted">{t.restoreTargetHelp}</p>
      {restoreResult && <><p>{t.restored}</p>{archiveSummaryList(restoreResult.summary, restoreResult.summary.projectDir)}
        <button type="button" className="chat-activity__open" onClick={() => { restoreDialog.current?.close(); selectProject(restoreResult.project); }}><Icon name="folder" /><span>{t.restoredOpen}</span></button></>}
      {dialogError && <Failure failure={dialogError} language={preferences.language} labels={t} />}
      <div className="chat-dialog__actions"><button type="button" className="btn" onClick={() => restoreDialog.current?.close()}>{t.close}</button>
        <button type="submit" className="btn btn--primary" disabled={busy || !restorePath.trim()}>{busy ? t.restoring : t.restoreRun}</button></div>
    </form></dialog>
    <dialog ref={settingsDialog} className="chat-dialog chat-dialog--settings" closedby={updateRestarting ? "none" : "closerequest"}
      onClose={() => setSettingsOpen(false)} onCancel={(event) => { if (updateRestarting) event.preventDefault(); }}>
      <div className="chat-dialog__heading"><h2>{t.settingsHeading}</h2><button className="chat-icon" aria-label={t.close} disabled={updateRestarting} onClick={() => settingsDialog.current?.close()}><Icon name="close" /></button></div>
      <div inert={updateRestarting}>{settings}</div>
      <SoftwareUpdateSettings language={preferences.language} open={settingsOpen} restartBlocker={restartBlocker} onRestarting={setUpdateRestarting} />
    </dialog>
  </div>;
}
