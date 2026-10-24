/**
 * The tracing-paper gesture tools, in toolbar order. Each renders `view.markup_tools.<kind>`; the
 * command tree's test reads this list to check every id is registered (#300).
 */
import type { GestureDto } from "../../api/project-runtime/generated";
import type { MessageKey } from "../../i18n/messages.en";

export type GestureTool = GestureDto["kind"];

export const GESTURE_TOOLS: ReadonlyArray<{
  kind: GestureTool;
  glyph: string;
  labelKey: MessageKey;
  titleKey: MessageKey;
}> = [
  {
    kind: "circle",
    glyph: "◯",
    labelKey: "stage.tools.circle.label",
    titleKey: "stage.tools.circle.title",
  },
  {
    kind: "arrow",
    glyph: "↗",
    labelKey: "stage.tools.arrow.label",
    titleKey: "stage.tools.arrow.title",
  },
  { kind: "freehand", glyph: "✎", labelKey: "stage.tools.freehand.label", titleKey: "stage.tools.freehand.title" },
  { kind: "text", glyph: "T", labelKey: "stage.tools.text.label", titleKey: "stage.tools.text.title" },
  { kind: "line", glyph: "╱", labelKey: "stage.tools.line.label", titleKey: "stage.tools.line.title" },
  { kind: "ruler", glyph: "↔", labelKey: "stage.tools.ruler.label", titleKey: "stage.tools.ruler.title" },
  { kind: "arc", glyph: "⌒", labelKey: "stage.tools.arc.label", titleKey: "stage.tools.arc.title" },
  {
    kind: "keep",
    glyph: "✓",
    labelKey: "stage.tools.keep.label",
    titleKey: "stage.tools.keep.title",
  },
  {
    kind: "remove",
    glyph: "✗",
    labelKey: "stage.tools.remove.label",
    titleKey: "stage.tools.remove.title",
  },
];
