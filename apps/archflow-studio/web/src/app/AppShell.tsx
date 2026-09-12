/**
 * The frame: a compact toolbar across the top, an optional conversation beside
 * the stage, and — when the evidence drawer is pinned — the drawer as the last
 * column. This file decides where things go and nothing else.
 */

import type { ReactNode } from "react";

/**
 * True when a host page has embedded this tool beside its own chrome. The host
 * draws the workspace entries and the project's position itself, so this page
 * does not repeat them.
 */
export function embeddedInHost(): boolean {
  return typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).get("embedded") === "tool";
}

export function AppShell({
  toolbar,
  conversation,
  stage,
  pinnedDrawer,
  embedded = false,
}: {
  toolbar: ReactNode;
  conversation: ReactNode;
  stage: ReactNode;
  /** The drawer when it stands beside the stage; null when it overlays it. */
  pinnedDrawer: ReactNode;
  /** A host page already carries this bar; do not draw a second one. */
  embedded?: boolean;
}) {
  return (
    <div
      className="app"
      data-conversation={String(conversation !== null)}
      data-pinned={String(pinnedDrawer !== null)}
      data-embedded={String(embedded)}
    >
      {!embedded && <header className="toolbar">{toolbar}</header>}
      {conversation}
      {stage}
      {pinnedDrawer}
    </div>
  );
}
