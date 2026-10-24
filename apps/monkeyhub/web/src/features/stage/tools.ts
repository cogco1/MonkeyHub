/**
 * Modeling's camera views and drawing tools. The toolbar renders one command per entry, and the
 * command tree's test reads the same lists to check every id is registered (#300).
 */
export const STANDARD_VIEWS = ["top", "front", "right", "iso", "perspective"] as const;
export const SKETCH_TOOLS = ["line", "rectangle", "circle"] as const;
export const MORE_SKETCH_TOOLS = ["polygon", "arc", "freehand"] as const;
export const MODEL_TOOLS = ["pushPull", "move", "rotate", "scale", "copy"] as const;

/** The command a drawing or model tool renders, `draft.tools.<kind>` in snake_case (pushPull → push_pull). */
export function draftToolCommand(kind: string): string {
  return `draft.tools.${kind.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`)}`;
}
