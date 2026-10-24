/**
 * The document page's annotation tools, in toolbar order. Each renders `view.markup_tools.<kind>`;
 * the command tree's test reads this list to check every id is registered (#300).
 */
export const DOCUMENT_TOOLS = ["select", "freehand", "line", "polyline", "arrow", "circle", "text", "eraser", "pan"] as const;

/** The tools an evidence-only page keeps. */
export const EVIDENCE_TOOLS: readonly (typeof DOCUMENT_TOOLS)[number][] = ["select", "polyline", "eraser", "pan"];
