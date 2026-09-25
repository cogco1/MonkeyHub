/**
 * How a refusal looks in this shell (FN-4).
 *
 * Anyone first reads what happened, in plain words, and the one next step. The
 * server's `code` and `detail` stay verbatim under Technical details, which
 * anyone can open and developer mode opens by default. A
 * `BLOCKED_NEEDS_HUMAN` shows the question it asked and the forms it will
 * accept — in place of the panel's content, never as a toast that scrolls away
 * and never as an empty list.
 */

import type { StudioApiError } from "../api/client";
import { MISSING_EDITABLE_CONTROL, NETWORK_ERROR, TRANSPORT_ERROR, UNSUPPORTED_REQUEST } from "../api/error";
import { BilingualText } from "../i18n/BilingualText";
import { useT } from "../i18n/useT";
import { usePreferences, type Language } from "../features/settings/preferences";

const PROTECTED_TEXT =
  /(`[^`]+`|https?:\/\/[^\s]+|(?:GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+\/[^\s,;]+|[A-Za-z]:\\[^\r\n]+|\/api\/[^\s,;]+|[0-9a-fA-F]{32,}|[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}|\b[A-Z][A-Z0-9_]{2,}\b|\b(?=[A-Za-z0-9_-]*\d)(?:[A-Za-z0-9]+[-_])+(?:[A-Za-z0-9_-]+)\b|[^\s,;]+\.(?:3dm|json|toml|ya?ml|txt|md)\b)/g;

/**
 * Translate prose while keeping protocol tokens, paths and identifiers verbatim.
 * The English source remains mounted inside every BilingualText layer.
 */
export function BilingualProse({ source }: { source: string }) {
  const parts: Array<{ protected: boolean; text: string }> = [];
  let cursor = 0;

  for (const match of source.matchAll(PROTECTED_TEXT)) {
    const start = match.index;
    if (start > cursor) {
      parts.push({ protected: false, text: source.slice(cursor, start) });
    }
    parts.push({ protected: true, text: match[0] });
    cursor = start + match[0].length;
  }
  if (cursor < source.length) {
    parts.push({ protected: false, text: source.slice(cursor) });
  }

  return (
    <span>
      {parts.map((part, index) =>
        part.protected ? (
          <span key={`${index}:${part.text}`} className="mono" lang="en">
            {part.text}
          </span>
        ) : (
          <BilingualText key={`${index}:${part.text}`} source={part.text} />
        ),
      )}
    </span>
  );
}

type FailureKind =
  | "network" | "version" | "crash" | "stale" | "project" | "base" | "pending" | "rejected"
  | "sectionMisses" | "sectionEye" | "section"
  | "drawingFont" | "drawingEmpty" | "drawingSource" | "drawing"
  | "unsupported" | "notFound" | "tooLarge" | "conflict" | "invalid" | "server" | "unknown";

interface FailureCopy { readonly reason: string; readonly next: string }

/**
 * What each kind of refusal says, as a reason and one next step. The copy stays
 * here until the catalogs can take it (review §4 steps 1 and 6); a code that
 * is not listed falls back on its HTTP status, then on `unknown`.
 */
const FAILURES: Record<FailureKind, Record<Language, FailureCopy>> = {
  network: {
    "zh-CN": { reason: "无法连接到项目服务。", next: "确认 MonkeyHub 仍在运行，然后重试。" },
    en: { reason: "The project service could not be reached.", next: "Check that MonkeyHub is still running, then try again." },
  },
  version: {
    "zh-CN": { reason: "项目服务与这个界面的版本不一致。", next: "重启 MonkeyHub，再打开这个项目。" },
    en: { reason: "The project service and this screen are different versions.", next: "Restart MonkeyHub, then open the project again." },
  },
  crash: {
    "zh-CN": { reason: "这部分界面出错，已经停止。", next: "用下面的按钮重新载入。" },
    en: { reason: "This part of the screen stopped after an error.", next: "Reload it with the button below." },
  },
  stale: {
    "zh-CN": { reason: "这一步基于的版本已不是最新。", next: "在最新版本上再做一次。" },
    en: { reason: "This step was based on a version that is no longer current.", next: "Do it again on the latest version." },
  },
  project: {
    "zh-CN": { reason: "这一步属于另一个项目。", next: "回到它所属的项目后再试。" },
    en: { reason: "This step belongs to another project.", next: "Open the project it belongs to, then try again." },
  },
  base: {
    "zh-CN": { reason: "这个模型版本现在无法打开。", next: "重试，或返回默认修改起点。" },
    en: { reason: "This model version cannot be opened right now.", next: "Retry, or return to the default editing base." },
  },
  pending: {
    "zh-CN": { reason: "这个结果还在生成。", next: "等它完成后再试。" },
    en: { reason: "This result is still being generated.", next: "Wait for it to finish, then try again." },
  },
  rejected: {
    "zh-CN": { reason: "这个结果已被否定，不能成为阶段。", next: "接受另一个结果，或从这里继续修改。" },
    en: { reason: "This result was turned down, so it cannot become a Stage.", next: "Accept another result, or continue from here and change it." },
  },
  sectionMisses: {
    "zh-CN": { reason: "剖切线没有切到模型。", next: "把剖切线移到建筑上，再生成一次。" },
    en: { reason: "The section line does not cut through the model.", next: "Move the section line across the building, then draw again." },
  },
  sectionEye: {
    "zh-CN": { reason: "视点不在被剖去的一侧。", next: "把视点移到被剖去的一侧，再生成一次。" },
    en: { reason: "The eye point is not on the side the section removes.", next: "Move the eye point to the removed side, then draw again." },
  },
  section: {
    "zh-CN": { reason: "这组剖透视设置无法生成图纸。", next: "调整剖切线、视点或进深，再生成一次。" },
    en: { reason: "These section perspective settings cannot make a drawing.", next: "Adjust the section line, eye point or depth, then draw again." },
  },
  drawingFont: {
    "zh-CN": { reason: "这台电脑缺少出图所需的字体。", next: "安装 Arial、DejaVu Sans 或 Liberation Sans 后重试。" },
    en: { reason: "This computer is missing a font the sheet needs.", next: "Install Arial, DejaVu Sans or Liberation Sans, then try again." },
  },
  drawingEmpty: {
    "zh-CN": { reason: "图上没有可画的构件了。", next: "至少显示一个构件，再生成一次。" },
    en: { reason: "No object is left to draw.", next: "Show at least one object, then draw again." },
  },
  drawingSource: {
    "zh-CN": { reason: "图纸无法使用所选的模型。", next: "为图纸另选一个模型版本后重试。" },
    en: { reason: "The drawing cannot use the selected model.", next: "Choose another model version for the drawing, then try again." },
  },
  drawing: {
    "zh-CN": { reason: "图纸没有生成。", next: "检查图纸设置后重试。" },
    en: { reason: "The drawing could not be made.", next: "Check the drawing settings, then try again." },
  },
  unsupported: {
    "zh-CN": { reason: "这项修改目前无法自动完成。", next: "换一种说法，或直接在模型上修改。" },
    en: { reason: "This change cannot be made automatically yet.", next: "Describe it another way, or edit the model directly." },
  },
  notFound: {
    "zh-CN": { reason: "这一步要用的内容已不在项目中。", next: "从当前列表重新选择后再试。" },
    en: { reason: "Something this step needs is no longer in the project.", next: "Choose again from the current list, then try again." },
  },
  tooLarge: {
    "zh-CN": { reason: "文件太大，无法处理。", next: "换一个较小的文件再试。" },
    en: { reason: "The file is too large.", next: "Use a smaller file, then try again." },
  },
  conflict: {
    "zh-CN": { reason: "这一步与项目的最新状态冲突。", next: "在最新状态上再做一次。" },
    en: { reason: "This step conflicts with the project's latest state.", next: "Do it again on the latest state." },
  },
  invalid: {
    "zh-CN": { reason: "项目服务没有接受这一步的内容。", next: "检查填写的内容后再试。" },
    en: { reason: "The project service did not accept what this step sent.", next: "Check what you entered, then try again." },
  },
  server: {
    "zh-CN": { reason: "项目服务出错，这一步没有完成。", next: "稍后再试；如果一直失败，重启 MonkeyHub。" },
    en: { reason: "The project service ran into a problem and did not finish this step.", next: "Try again in a moment. If it keeps failing, restart MonkeyHub." },
  },
  unknown: {
    "zh-CN": { reason: "这一步没有完成。", next: "再试一次；如果仍然失败，打开下面的技术详情查看原因。" },
    en: { reason: "This step did not finish.", next: "Try again. If it fails again, open Technical details below to see why." },
  },
};

/** The next step after a building edit the compiler could not take; its reason is the catalog's. */
const SEMANTIC_EDIT_NEXT: Record<Language, string> = {
  "zh-CN": "换一种说法描述这项修改，或先点选要改的构件。",
  en: "Describe the change another way, or pick the element to change first.",
};

/**
 * A browser's own words for a request that never left it. `asStudioApiError`
 * files those under `TRANSPORT_ERROR` with no status, beside thrown client
 * sentences that are not network failures.
 */
const FETCH_FAILED = /failed to fetch|networkerror|load failed|fetch failed/i;

function failureKind(error: StudioApiError): FailureKind {
  const { code, status } = error;
  if (code === NETWORK_ERROR || (code === TRANSPORT_ERROR && status === 0 && FETCH_FAILED.test(error.detail))) return "network";
  if (code === "CLIENT_CRASH") return "crash";
  if (code.startsWith("PROTOCOL_")) return "version";
  if (code === "CANDIDATE_REJECTED") return "rejected";
  if (code === "CANDIDATE_RUNNING" || code === "CANDIDATE_NOT_READY" || code === "CANDIDATE_NOT_FINISHED") return "pending";
  if (code === "SECTION_PLANE_MISSES_MODEL") return "sectionMisses";
  if (code.startsWith("SECTION_EYE_")) return "sectionEye";
  if (code.startsWith("SECTION_")) return "section";
  if (code === "DRAWING_FONT_UNAVAILABLE") return "drawingFont";
  if (code === "DRAWING_EMPTY") return "drawingEmpty";
  if (code.startsWith("DRAWING_SOURCE_") || code === "DRAWING_COMPLETE_SOURCE_UNAVAILABLE" ||
    code === "DRAWING_NATIVE_GEOMETRY_UNSUPPORTED") return "drawingSource";
  if (code.startsWith("DRAWING_")) return "drawing";
  if (code.endsWith("PROJECT_MISMATCH") || code === "EDITING_PROJECT_CHANGED") return "project";
  if (code === "EDITING_BASE_UNAVAILABLE" || code === "WORKING_DRAFT_SOURCE_CHANGED") return "base";
  if (code.includes("STALE") || code.endsWith("_CHANGED")) return "stale";
  // The client also files its own local preconditions under UNSUPPORTED_REQUEST,
  // with no status; only the server's answer means the change itself.
  if (code === MISSING_EDITABLE_CONTROL || (code === UNSUPPORTED_REQUEST && status > 0)) return "unsupported";
  if (status === 404) return "notFound";
  if (status === 413) return "tooLarge";
  if (status === 409) return "conflict";
  if (status === 400 || status === 422) return "invalid";
  if (status >= 500) return "server";
  return "unknown";
}

/** A Board note refused before it was sent keeps its own sentence: the note is still below. */
function boardSourceFailure(error: StudioApiError, what: string | undefined, language: Language): string | null {
  if (what !== "MonkeyBoard") return null;
  if (error.code === "EDITING_PROJECT_CHANGED") {
    return language === "zh-CN"
      ? "这张图纸与保存的模型版本已不一致。意见尚未发送，原文保留在下方输入框中。"
      : "This drawing no longer matches its saved model version. Your feedback was not sent; the original instruction is kept below.";
  }
  if (error.code === "EDITING_BASE_UNAVAILABLE") {
    return language === "zh-CN"
      ? "这张图纸对应的模型暂时无法打开。意见尚未发送，原文保留在下方输入框中。"
      : "This drawing's saved model could not be opened. Your feedback was not sent; the original instruction is kept below.";
  }
  return null;
}

export function ErrorPanel({
  error,
  what,
}: {
  error: StudioApiError;
  what?: string;
}) {
  const t = useT();
  const { developerMode, language } = usePreferences();
  const board = boardSourceFailure(error, what, language);
  const semantic = error.code === "SEMANTIC_EDIT_INVALID";
  const copy = FAILURES[failureKind(error)][language];
  // A question is its own next step, answered through the forms below it.
  const reason = board ?? (error.question ? <BilingualProse source={error.question} />
    : semantic ? t("error.semanticEditInvalid") : copy.reason);
  const next = board || error.question ? null : semantic ? SEMANTIC_EDIT_NEXT[language] : copy.next;
  return (
    <div className="error-panel" role="alert">
      <p className="error-panel__detail error-panel__reason">{reason}</p>
      {next && <p className="error-panel__detail error-panel__next">{next}</p>}
      {error.acceptedForms.length > 0 && (
        <ul className="error-panel__forms">
          {error.acceptedForms.map((form) => (
            <li key={form}>
              {developerMode ? <code>{form}</code> : <BilingualProse source={form} />}
            </li>
          ))}
        </ul>
      )}
      {/* A new refusal starts folded rather than inheriting the last one's state. */}
      <details key={`${error.code}:${error.detail}`} className="card__details error-panel__details" open={developerMode}>
        <summary>{t("common.technicalDetails")}</summary>
        <p className="error-panel__head">
          <span className="error-panel__code">{error.code}</span>
          {error.status > 0 && (
            <span className="error-panel__status">HTTP {error.status}</span>
          )}
          {what && <span className="error-panel__what">{what}</span>}
        </p>
        <p className="error-panel__detail" lang="en" translate="no">{error.detail}</p>
      </details>
    </div>
  );
}
