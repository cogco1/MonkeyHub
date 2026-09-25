/**
 * One Agent turn's tool calls, read as a single process (#285).
 *
 * The Hub saves every call an Agent makes as its own `tool` message whose first
 * line is the call as the CLI reported it: `studio_request · GET /api/board ·
 * completed`, `rg -n … · failed`, `Web search: … · completed`. This module
 * groups a transcript into turns, keeps a turn's calls together as steps, and
 * names each step in plain words; the raw line stays available as technical
 * detail. It reads saved messages only and decides nothing about them.
 */

import type { ChatMessage } from "./api/generated";

/** Steps named by the action they took, without an argument of their own. */
export type StepKey =
  | "boardRead" | "boardUpdate" | "boardExport" | "designContext" | "candidate" | "modelView" | "sheet" | "state"
  | "documents" | "proposal" | "proposalRead" | "candidateRead" | "compare" | "progress" | "capabilities" | "parameter"
  | "project" | "modeling" | "programRead" | "programUpdate" | "decisionsRead" | "decisionSave" | "elevation"
  | "planDrawing" | "drawingStyles" | "exportModel" | "exportStatus" | "artifacts" | "annotationsRead"
  | "annotationsUpdate" | "semantics" | "optionsRead" | "optionsUpdate" | "studies" | "combine" | "closure"
  | "projectRead" | "projectWrite" | "projectStep" | "fabProfiles" | "fabCheck" | "present" | "attachment" | "bind"
  | "desktopStep" | "readFiles" | "editFiles" | "command" | "planSteps" | "webFetch" | "generic";

/** A step as a person reads it: a plain action, one wrapping another, or an action with its own words. */
export type Step =
  | { key: StepKey }
  | { key: "prepare"; inner: Step }
  | { key: "webSearch" | "desktop" | "permission"; detail: string };

export interface ProcessWords {
  steps: Readonly<Record<StepKey, string>>;
  prepare: (label: string) => string;
  webSearch: (query: string) => string;
  desktop: (target: string) => string;
  permission: (choice: string) => string;
}

export function stepText(step: Step, words: ProcessWords): string {
  if (step.key === "prepare") return words.prepare(stepText(step.inner, words));
  if (step.key === "webSearch") return words.webSearch(step.detail);
  if (step.key === "desktop") return words.desktop(step.detail);
  if (step.key === "permission") return words.permission(step.detail);
  return words.steps[step.key];
}

const STATUS_WORDS = new Set(["completed", "complete", "failed", "in_progress", "pending", "cancelled", "canceled", "interrupted", "streaming"]);
const REQUEST = /^(GET|POST|PUT|PATCH|DELETE) (\S+)$/;

/** Studio routes the Agent is told to use, by what they do for the architect. */
const ROUTES: ReadonlyArray<readonly [RegExp, RegExp, StepKey]> = [
  [/^GET$/, /^\/board$/, "boardRead"],
  [/^PUT$/, /^\/board$/, "boardUpdate"],
  [/^POST$/, /^\/board\/export$/, "boardExport"],
  [/^POST$/, /^\/intents\/context$/, "designContext"],
  [/^POST$/, /^\/proposals\/[^/]+\/candidate$/, "candidate"],
  [/^GET$/, /^\/drawings\/model-view$/, "modelView"],
  [/^POST$/, /^\/drawings\/sheets$/, "sheet"],
  [/^GET$/, /^\/state(\/frame|\/volumes)?$/, "state"],
  [/^GET$/, /^\/documents$/, "documents"],
  [/^POST$/, /^\/proposals(\/(sketch|transform|push-pull|elevation|delete|parameter-locks))?$/, "proposal"],
  [/^GET$/, /^\/proposals\/[^/]+$/, "proposalRead"],
  [/^GET$/, /^\/candidates\/[^/]+\/compare$/, "compare"],
  [/^GET$/, /^\/candidates\/[^/]+$/, "candidateRead"],
  [/^GET$/, /^\/jobs(\/[^/]+)?$/, "progress"],
  [/^POST$/, /^\/capabilities\/[^/]+\/run$/, "parameter"],
  [/^GET$/, /^\/capabilities(\/.*)?$/, "capabilities"],
  [/^GET$/, /^\/project$/, "project"],
  [/^POST$/, /^\/project\/modeling$/, "modeling"],
  [/^GET$/, /^\/program$/, "programRead"],
  [/^(POST|PUT)$/, /^\/program$/, "programUpdate"],
  [/^GET$/, /^\/decisions(\/[^/]+)?$/, "decisionsRead"],
  [/^POST$/, /^\/decisions(\/[^/]+\/revisions)?$/, "decisionSave"],
  [/^POST$/, /^\/drawings\/elevations$/, "elevation"],
  [/^POST$/, /^\/drawings\/plans(\/.*)?$/, "planDrawing"],
  [/^GET$/, /^\/drawings\/styles$/, "drawingStyles"],
  [/^POST$/, /^\/exports$/, "exportModel"],
  [/^GET$/, /^\/exports(\/.*)?$/, "exportStatus"],
  [/^GET$/, /^\/artifacts(\/.*)?$/, "artifacts"],
  [/^GET$/, /^\/(document|model)-annotations$/, "annotationsRead"],
  [/^PUT$/, /^\/(document|model)-annotations$/, "annotationsUpdate"],
  [/^GET$/, /^\/semantics$/, "semantics"],
  [/^GET$/, /^\/options(\/.*)?$/, "optionsRead"],
  [/^POST$/, /^\/options(\/.*)?$/, "optionsUpdate"],
  [/^GET$/, /^\/studies(\/.*)?$/, "studies"],
  [/^POST$/, /^\/candidates\/combine$/, "combine"],
  [/^POST$/, /^\/state\/closure$/, "closure"],
];

/** A request to a bound tool, named for what it did; unknown routes say only read or write. */
function requestStep(tool: string | null, method: string, target: string): Step {
  const path = target.split("?")[0]!.replace(/^\/api(?=\/)/, "");
  if (tool === "fab_request") return { key: method === "GET" ? "fabProfiles" : "fabCheck" };
  const known = ROUTES.find(([verb, route]) => verb.test(method) && route.test(path));
  const step: Step = { key: known ? known[2] : method === "GET" ? "projectRead" : "projectWrite" };
  // A schema read describes the action it names; it did not perform it.
  return tool === "studio_schema" ? { key: "prepare", inner: step } : step;
}

const BOUND: Readonly<Record<string, StepKey>> = {
  chat_present: "present", attachment_read: "attachment", presentation_bind: "bind", fab_request: "fabCheck",
  studio_schema: "projectRead", studio_request: "projectStep", computer_action: "desktopStep",
};
const READS = new Set(["rg", "grep", "egrep", "findstr", "select-string", "sls", "get-content", "gc", "cat", "type", "head", "tail",
  "sed", "awk", "less", "more", "ls", "dir", "get-childitem", "gci", "find", "fd", "tree", "wc", "read", "glob", "search",
  "list", "view", "get-item", "test-path", "resolve-path", "where", "which", "stat"]);
const EDITS = new Set(["edit", "write", "multiedit", "apply_patch", "patch", "create", "delete", "remove", "move", "rename",
  "notebookedit", "set-content", "add-content", "out-file", "new-item", "remove-item", "copy-item", "move-item",
  "rm", "mv", "cp", "mkdir", "touch"]);
const SHELLS = new Set(["bash", "powershell", "pwsh", "cmd", "shell", "sh", "terminal", "python", "py", "node", "npm", "npx",
  "pnpm", "yarn", "git", "pip", "uv", "pytest", "curl", "wget", "invoke-webrequest", "iwr", "start-process", "gh"]);

/** A CLI's own tool, named by its title: a shell command, a file tool or a web search. */
function titleStep(title: string): Step {
  const search = /^web[ _]?search\b:?\s*(.*)$/i.exec(title);
  if (search) return { key: "webSearch", detail: search[1]!.trim().slice(0, 120) };
  if (/^(web[ _]?fetch|fetch\b|open https?:)/i.test(title)) return { key: "webFetch" };
  if (/^(todo[ _]?write|update[ _]plan|plan)\b/i.test(title)) return { key: "planSteps" };
  const words = title.replace(/^[&`'"(]+/, "").split(/\s+/);
  let first = (words[0] ?? "").replace(/[`'"]/g, "").toLowerCase().replace(/\.exe$/, "");
  // "Run rg -n …" is the command it runs.
  if ((first === "run" || first === "execute" || first === "running") && words.length > 1) {
    return titleStep(words.slice(1).join(" "));
  }
  if (READS.has(first)) return { key: "readFiles" };
  if (EDITS.has(first)) return { key: "editFiles" };
  if (SHELLS.has(first) || /^[a-z]+-[a-z]+$/.test(first)) return { key: "command" };
  return { key: "generic" };
}

/** The first line of a tool message as the CLI reported the call. */
export const rawLine = (message: ChatMessage) => message.content.split("\n")[0] ?? "";
/** The diagnostics the Hub saved under that line, if any. */
export const rawDetail = (message: ChatMessage) => message.content.split("\n").slice(1).join("\n");

/** One saved call, or an operation's `METHOD /path` kind, named in plain words. */
export function describeCall(line: string): Step {
  const parts = line.split(" · ").map((part) => part.trim()).filter(Boolean);
  const request = REQUEST.exec(parts[0] ?? "");
  if (request) return requestStep(null, request[1]!, request[2]!);
  const asked = parts.length > 1 ? REQUEST.exec(parts[1]!) : null;
  if (asked) return requestStep(parts[0]!, asked[1]!, asked[2]!);
  const desktop = /^([A-Z_]+) — (.+?)(?: ✓| ✕.*)?$/.exec(parts[0] ?? "");
  if (desktop) return { key: "desktop", detail: desktop[2]!.trim() };
  const title = (STATUS_WORDS.has(parts.at(-1) ?? "") ? parts.slice(0, -1) : parts).join(" · ");
  if (BOUND[title]) return { key: BOUND[title] };
  return titleStep(title);
}

export function describeStep(message: ChatMessage): Step {
  // A permission row is the request itself, then the choice that answered it.
  if (message.id.includes(":permission:")) {
    const parts = message.content.split(" · ");
    return { key: "permission", detail: parts.length > 1 ? parts.at(-1)!.trim() : "" };
  }
  return describeCall(rawLine(message));
}

export interface ProcessTurn {
  /** The user message that started the turn, or "start" for what came before any. */
  key: string;
  user: ChatMessage | null;
  /** The turn's tool calls, folded together. */
  steps: ChatMessage[];
  /** What stays in the conversation: the Agent's text, public progress and waiting permission prompts. */
  visible: ChatMessage[];
  /** Steps that read back a candidate the architect can open. */
  results: ChatMessage[];
  failed: number;
  /** Milliseconds since the epoch, when the saved messages carry readable times. */
  startedAt: number | null;
  endedAt: number | null;
}

const time = (value: string | undefined) => {
  const parsed = value ? Date.parse(value) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
};

/** A transcript as turns. A permission prompt stays visible only while its turn can still answer it. */
export function turnsOf(messages: readonly ChatMessage[], running: boolean): ProcessTurn[] {
  const turns: ProcessTurn[] = [];
  let current: ProcessTurn | null = null;
  for (const message of messages) {
    // An interjection the running turn takes in (#301) belongs to that turn: it shows
    // inline and the turn's calls keep folding into one row. A step the Agent stopped
    // to continue with the message ("restarted") starts a new turn.
    if (message.role === "user" && current && (message.interjection === "pending" || message.interjection === "delivered")) {
      current.visible.push(message);
      continue;
    }
    if (message.role === "user" || !current) {
      current = { key: message.role === "user" ? message.id : "start", user: message.role === "user" ? message : null,
        steps: [], visible: [], results: [], failed: 0, startedAt: time(message.createdAt), endedAt: null };
      turns.push(current);
      if (message.role === "user") continue;
    }
    const at = time(message.createdAt);
    if (at !== null) current.endedAt = Math.max(current.endedAt ?? at, at);
    if (message.role !== "tool" || message.id.includes(":progress:")) { current.visible.push(message); continue; }
    current.steps.push(message);
    if (message.status === "failed") current.failed++;
    if (message.candidateId) current.results.push(message);
    if (running && message.permission) current.visible.push(message);
  }
  return turns;
}

/** The step a running turn is on: the latest call still under way, else the latest call. */
export const currentStep = (turn: ProcessTurn) => turn.steps.findLast((step) => step.status === "streaming") ?? turn.steps.at(-1) ?? null;

/** Whole seconds a finished turn took, from its first to its last saved event. */
export function workedSeconds(turn: ProcessTurn): number | null {
  if (turn.startedAt === null || turn.endedAt === null || turn.endedAt < turn.startedAt) return null;
  return Math.max(1, Math.round((turn.endedAt - turn.startedAt) / 1000));
}

/** A running clock: 0:42, 12:05, 1:02:03. */
export function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600), minutes = Math.floor((whole % 3600) / 60), rest = whole % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  return hours ? `${hours}:${pad(minutes)}:${pad(rest)}` : `${minutes}:${pad(rest)}`;
}
