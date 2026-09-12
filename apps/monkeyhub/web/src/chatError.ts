/**
 * What a failed step says to the person, and what it keeps for whoever has to
 * debug it.
 *
 * The Hub answers every failure as {code, detail}: `code` is its own, `detail`
 * is whatever the failing thing said — a sentence from the Hub itself, the
 * tail of a CLI's stderr, or a provider's raw JSON body. The raw text is worth
 * keeping and is not worth reading first, so this splits one failure into a
 * short accurate line and the untouched original beneath it.
 *
 * This is only ever applied to a failure. A message whose content happens to
 * be JSON is a message, and is rendered as one.
 */

import type { HubError } from "./api/generated";

export type Language = "en" | "zh-CN";

export interface PresentedFailure {
  /** One line, in the reader's language, that does not guess a cause. */
  readonly summary: string;
  /** The failure exactly as it arrived, for the person diagnosing it. */
  readonly technical: string;
  /**
   * True only when the failure itself named the model as the problem, so the
   * offer to choose another one is the failure's own advice, not a hunch.
   */
  readonly modelRejected: boolean;
}

/** Hub codes that already describe themselves; the detail stays available. */
const summaries: Record<Language, Record<string, string>> = {
  "zh-CN": {
    CHAT_TIMEOUT: "这一轮超过了时间上限，CLI 没有回复完。",
    CHAT_INCOMPLETE: "CLI 中途结束，这一轮没有完成。",
    CHAT_STOPPED: "回复已按你的要求停止。",
    CHAT_INTERRUPTED: "Hub 关闭时这一轮还没结束，这个对话可以继续。",
    CHAT_PROCESS_FAILED: "CLI 没能完成这一轮。",
    CHAT_PROVIDER_FAILED: "连接的一方报告这一轮失败。",
    CHAT_PROVIDER_UNAVAILABLE: "这个 CLI 连接现在不可用。",
    CHAT_RUNNING: "这个对话正在回复，先等它结束。",
    CHAT_NOT_RUNNING: "这个对话已经不在回复中了。",
    CHAT_PROJECT_BUSY: "另一个项目还在执行，先等它结束或停止它。",
    CHAT_PROJECT_MISMATCH: "这个操作属于另一个项目。",
    CHAT_PROJECT_INVALID: "这个文件夹不是可读的 ArchFlow 项目。",
    CHAT_NOT_FOUND: "这个对话不存在，可能已经被删除。",
    CHAT_MESSAGE_EMPTY: "先输入一条消息。",
    CHAT_CONFIG_INVALID: "已安装 CLI 的配置读不出来。",
    CHAT_STUDIO_UNAVAILABLE: "先为这个项目打开建模页面，再使用设计工具。",
    CHAT_TOOL_FAILED: "这次工具调用失败了。",
    CHAT_TOOL_UNAVAILABLE: "聊天里没有这个操作。",
    CHAT_CLOSING: "Hub 正在关闭。",
    PROJECT_NAME_REQUIRED: "给新项目起一个名称。",
    PROJECT_NAME_INVALID: "这个项目名称不能用。",
    PROJECT_EXISTS: "这个名称已经被占用。",
    PROJECT_CREATE_FAILED: "这个项目没有创建成功。",
    WORKSPACE_INVALID: "先在 Hub 设置里选一个工作区。",
  },
  en: {
    CHAT_TIMEOUT: "This turn passed its time limit before the CLI answered.",
    CHAT_INCOMPLETE: "The CLI stopped part way and this turn did not finish.",
    CHAT_STOPPED: "The reply stopped because you asked it to.",
    CHAT_INTERRUPTED: "Hub closed before this turn finished. The conversation can continue.",
    CHAT_PROCESS_FAILED: "The CLI could not finish this turn.",
    CHAT_PROVIDER_FAILED: "The connected side reported a failed turn.",
    CHAT_PROVIDER_UNAVAILABLE: "This CLI connection is not available right now.",
    CHAT_RUNNING: "This conversation is replying. Wait for it to finish.",
    CHAT_NOT_RUNNING: "This conversation is no longer replying.",
    CHAT_PROJECT_BUSY: "Another project is still running. Wait for it or stop it.",
    CHAT_PROJECT_MISMATCH: "This action belongs to another project.",
    CHAT_PROJECT_INVALID: "That folder is not a readable ArchFlow project.",
    CHAT_NOT_FOUND: "This conversation does not exist any more.",
    CHAT_MESSAGE_EMPTY: "Enter a message first.",
    CHAT_CONFIG_INVALID: "The installed CLI's configuration could not be read.",
    CHAT_STUDIO_UNAVAILABLE: "Open the modeling page for this project before using a design tool.",
    CHAT_TOOL_FAILED: "That tool call failed.",
    CHAT_TOOL_UNAVAILABLE: "The chat does not expose that action.",
    CHAT_CLOSING: "Hub is closing.",
    PROJECT_NAME_REQUIRED: "Give the new project a name.",
    PROJECT_NAME_INVALID: "That project name cannot be used.",
    PROJECT_EXISTS: "That name is already taken.",
    PROJECT_CREATE_FAILED: "That project was not created.",
    WORKSPACE_INVALID: "Choose a workspace in Hub settings first.",
  },
};
/**
 * The one recognised kind: the connection answered that this model is not one
 * it will run. Only this kind gets a sentence of its own; every other failure
 * keeps the provider's own words or the Hub's code.
 */
const modelRefused: Record<Language, (provider: string | null) => string> = {
  "zh-CN": (provider) => provider
    ? `当前模型无法通过 ${provider} 使用，请更换模型。`
    : "当前模型不可用，请更换模型。",
  en: (provider) => provider
    ? `The current model is not available through ${provider}. Choose another model.`
    : "The current model is not available. Choose another model.",
};
const unknown: Record<Language, string> = {
  // No cause is claimed here: an unrecognised failure is unrecognised.
  "zh-CN": "这一步没有完成。下面是原始错误信息。",
  en: "This step did not finish. The original error is below.",
};

/**
 * The readable sentence inside a provider's body, if it put one there. Codex
 * reports `{"type":"error","status":400,"error":{"message":"…"}}` as a string
 * inside the event's own message; Claude reports a plain sentence. Both are
 * unwrapped here, and neither replaces the original text.
 */
function innerMessage(detail: string): string | null {
  let value: unknown = detail.trim();
  for (let depth = 0; depth < 3 && typeof value === "string"; depth += 1) {
    const text = value.trim();
    // Plain prose is already the sentence; only a body gets unwrapped.
    if (!text.startsWith("{") && !text.startsWith("[")) return text || null;
    try { value = JSON.parse(text); } catch { return null; }
  }
  const seen = new Set<unknown>();
  const find = (node: unknown, depth: number): string | null => {
    if (typeof node === "string") return node.trim() || null;
    if (!node || typeof node !== "object" || depth > 4 || seen.has(node)) return null;
    seen.add(node);
    const row = node as Record<string, unknown>;
    for (const key of ["message", "detail", "error", "result"]) {
      if (key in row) {
        const found = find(row[key], depth + 1);
        if (found) return found;
      }
    }
    return null;
  };
  return find(value, 0);
}

/**
 * Did the failure name the model? Both installed CLIs say so plainly when a
 * model is unknown or not on the account: Codex answers `invalid_request_error`
 * with "The '<id>' model is not supported…", Claude answers `model_not_found`
 * with "There's an issue with the selected model (<id>)…". Nothing else is
 * read as a model problem, and no failure is read as an account problem.
 */
function namesTheModel(text: string): boolean {
  const value = text.toLowerCase();
  if (/model_not_found|unrecognized_model|unknown[_ ]model|model[_ ]not[_ ]supported/.test(value)) return true;
  return /\bmodels?\b/.test(value)
    && /not supported|not found|does not exist|doesn't exist|no access|not have access|unavailable|invalid[_ ]request[_ ]error/.test(value);
}

export function presentFailure(
  failure: HubError | null | undefined,
  language: Language,
  /** The connection this conversation runs on, as the Hub named it. */
  provider?: string | null,
): PresentedFailure | null {
  if (!failure) return null;
  const code = typeof failure.code === "string" ? failure.code : "";
  const detail = typeof failure.detail === "string" ? failure.detail : "";
  const inner = innerMessage(detail);
  const known = summaries[language][code];
  const line = (text: string) => text.split("\n")[0]!.trim().slice(0, 300);
  // A provider's own sentence about this turn says more than "the provider
  // failed"; the Hub's own codes already speak for themselves, and a CLI's
  // stderr tail is diagnostics rather than a sentence to lead with.
  const rejected = namesTheModel(`${inner ?? ""}\n${detail}`);
  const summary = rejected ? modelRefused[language](provider?.trim() || null)
    : code === "CHAT_PROVIDER_FAILED" && inner ? line(inner)
      : known ? known
        : inner ? line(inner)
          : detail.trim() && !detail.trim().startsWith("{") ? line(detail)
            : unknown[language];
  return {
    summary: summary || unknown[language],
    technical: code ? `${code}: ${detail}` : detail,
    modelRejected: rejected,
  };
}
