/**
 * The render results' views. Each renders `view.render.<view>`; the command tree's test reads this
 * list to check every id is registered (#300).
 */
export const RENDER_VIEWS = ["result", "source", "compare"] as const;
