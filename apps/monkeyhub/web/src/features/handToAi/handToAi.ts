/**
 * S08 / GH-300: one 「交给 AI」 for the Board's selection, the document page and the model's tracing paper.
 * The person circles something, writes one sentence and hands it over: the sentence is hung on the first
 * circle, the circled objects and a picture go along as attachments, and the whole lands in the project's
 * conversation as an ordinary message with attachments (the same body the composer posts). How the AI reads
 * the marks is the conversation's business; the change list that reads them arrives separately.
 */
import { createContext, useContext } from "react";
import type { Language } from "../settings/preferences";

export type HandToAiSurface = "board" | "document" | "model";

/** One mark as the AI receives it; `note` is the person's sentence, on the first circle only. */
export interface HandToAiMark {
  readonly id: string;
  readonly kind: string;
  readonly bounds?: { readonly x: number; readonly y: number; readonly width: number; readonly height: number } | null;
  readonly text?: string | null;
  readonly note?: string;
}

/** What one surface hands over. The host posts it to the bound project's conversation. */
export interface HandToAiRequest {
  readonly projectId: string;
  readonly surface: HandToAiSurface;
  readonly words: string;
  readonly marks: number;
  /** The picture first, then the marks file, then any references. */
  readonly files: readonly File[];
  /** A small picture for the history row; null when none could be made. */
  readonly thumbnail: string | null;
}

export interface HandToAiSent {
  readonly chatId: string;
}

export type HandToAiErrorCode = "EMPTY" | "NOTHING_MARKED" | "PROJECT_CHANGED" | "CONVERSATION_UNAVAILABLE" | "CONVERSATION_BUSY";

export class HandToAiError extends Error {
  readonly code: HandToAiErrorCode;
  constructor(code: HandToAiErrorCode, message: string) {
    super(message);
    this.code = code;
    this.name = "HandToAiError";
  }
}

/** The project-bound way to hand marks to the AI; null where no conversation can take them (a bare surface, a test). */
export const HandToAiContext = createContext<((request: HandToAiRequest) => Promise<HandToAiSent>) | null>(null);

export function useHandToAi(): ((request: HandToAiRequest) => Promise<HandToAiSent>) | null {
  return useContext(HandToAiContext);
}

export const HAND_TO_AI_WORDS = {
  "zh-CN": {
    action: "交给 AI", sending: "正在交给 AI…", sent: "已交给 AI，在对话里看回复；这次的圈和字记在「修改历史」里。",
    label: "想让 AI 怎么改", placeholder: "写一句话，比如：这面墙往外推一米",
    references: "附参照", referencesCount: (count: number) => count ? `附参照（${count}）` : "附参照",
    surface: { board: "画板", document: "图页", model: "模型" } as Record<HandToAiSurface, string>,
    marked: (count: number) => count ? `圈了 ${count} 处` : "没有圈",
    attachments: "截图和圈到的对象在附件里。",
    errors: {
      EMPTY: "先写一句话，告诉 AI 要怎么改。",
      NOTHING_MARKED: "先圈出要改的地方，再交给 AI。",
      PROJECT_CHANGED: "这一页和现在打开的项目对不上了，请重新打开这个项目再试。",
      CONVERSATION_UNAVAILABLE: "现在打开的不是这个项目的对话。先打开这个项目，再交给 AI。",
      CONVERSATION_BUSY: "AI 还在处理上一条，等它回完再交。",
    } as Record<HandToAiErrorCode, string>,
    failed: "没交出去，圈和字都还在，请再试一次。",
    history: "交给 AI 的意见", historyEmpty: "还没有交给 AI 的意见。", openChat: "看对话",
  },
  en: {
    action: "Hand to AI", sending: "Handing to AI…", sent: "Handed to the AI; the reply is in the conversation and these marks are kept in History.",
    label: "What should the AI change?", placeholder: "One sentence, such as: push this wall out by a metre",
    references: "Add references", referencesCount: (count: number) => count ? `Add references (${count})` : "Add references",
    surface: { board: "Board", document: "Page", model: "Model" } as Record<HandToAiSurface, string>,
    marked: (count: number) => count === 1 ? "1 mark" : `${count} marks`,
    attachments: "The picture and the marked objects are attached.",
    errors: {
      EMPTY: "Write one sentence saying what to change first.",
      NOTHING_MARKED: "Circle what should change first, then hand it to the AI.",
      PROJECT_CHANGED: "This page no longer matches the open project. Reopen the project and try again.",
      CONVERSATION_UNAVAILABLE: "The open conversation belongs to another project. Open this project first, then hand it over.",
      CONVERSATION_BUSY: "The AI is still on the last message. Hand this over once it has replied.",
    } as Record<HandToAiErrorCode, string>,
    failed: "Not handed over; the marks and words are still here. Try again.",
    history: "Handed to the AI", historyEmpty: "Nothing handed to the AI yet.", openChat: "Open conversation",
  },
} as const;

/** The plain reason a hand-over failed, never the service's own words. */
export function handToAiErrorText(error: unknown, language: Language): string {
  const words = HAND_TO_AI_WORDS[language];
  if (error instanceof HandToAiError) return words.errors[error.code];
  return words.failed;
}

/** The message the conversation receives: the person's sentence, then one line saying what came with it. */
export function handToAiMessage(surface: HandToAiSurface, words: string, marks: number, language: Language): string {
  const text = HAND_TO_AI_WORDS[language];
  const [open, close] = language === "en" ? ["(", ")"] : ["（", "）"];
  return `${words.trim()}\n\n${open}${text.surface[surface]} · ${text.marked(marks)} · ${text.attachments}${close}`;
}

/** The sentence on the first circle: the first mark that encloses something, else the first mark. */
export function withNote(marks: readonly HandToAiMark[], words: string): HandToAiMark[] {
  const enclosing = new Set(["ellipse", "circle", "rectangle", "diamond", "polyline", "freedraw", "freehand"]);
  const first = marks.findIndex((mark) => enclosing.has(mark.kind));
  const at = first >= 0 ? first : 0;
  return marks.map((mark, index) => index === at ? { ...mark, note: words.trim() } : mark);
}

/** Marks drawn as points (a page's ink in page fractions, the tracing paper's in its own space), boxed for the AI. */
export function pointMarks(gestures: readonly { id?: string; kind: string; points: readonly (readonly number[])[]; label?: string | null }[]): HandToAiMark[] {
  return gestures.map((gesture, index) => {
    const xs = gesture.points.map((point) => point[0]!), ys = gesture.points.map((point) => point[1]!);
    const round = (value: number) => Math.round(value * 10000) / 10000;
    const bounds = xs.length ? { x: round(Math.min(...xs)), y: round(Math.min(...ys)),
      width: round(Math.max(...xs) - Math.min(...xs)), height: round(Math.max(...ys) - Math.min(...ys)) } : null;
    return { id: gesture.id ?? String(index), kind: gesture.kind, bounds, text: gesture.label?.trim() || null };
  });
}

/** The marks file: the sentence on the first circle, the marks, and whatever the surface knows about what they cover. */
export function marksFile(surface: HandToAiSurface, words: string, marks: readonly HandToAiMark[], context: Record<string, unknown> = {}): File {
  const body = { kind: "monkeyhub.hand_to_ai", version: 1, surface, words: words.trim(), marks: withNote(marks, words), ...context };
  return new File([JSON.stringify(body, null, 2)], `hand-to-ai-${surface}-marks.json`, { type: "application/json" });
}

export function pngFile(blob: Blob, surface: HandToAiSurface, name = "picture"): File {
  return new File([blob], `hand-to-ai-${surface}-${name}.png`, { type: "image/png" });
}

export function base64Png(data: string, surface: HandToAiSurface, name: string): File {
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return pngFile(new Blob([bytes], { type: "image/png" }), surface, name);
}

/** A small JPEG of the picture for the history row; null where the browser cannot draw it. */
export async function thumbnailOf(blob: Blob, width = 200): Promise<string | null> {
  try {
    const bitmap = await createImageBitmap(blob);
    const scale = Math.min(1, width / bitmap.width);
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale)); canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d");
    if (!context) return null;
    context.fillStyle = "#fff"; context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    return canvas.toDataURL("image/jpeg", 0.7);
  } catch { return null; }
}
