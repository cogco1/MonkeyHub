/**
 * The frame: a slim toolbar across the top, the conversation beside the
 * stage, and — when the evidence drawer is pinned — the drawer as a third
 * column. This file decides where things go and nothing else.
 */

import type { ReactNode } from "react";

export function AppShell({
  toolbar,
  conversation,
  stage,
  pinnedDrawer,
}: {
  toolbar: ReactNode;
  conversation: ReactNode;
  stage: ReactNode;
  /** The drawer when it stands beside the stage; null when it overlays it. */
  pinnedDrawer: ReactNode;
}) {
  return (
    <div className="app" data-pinned={String(pinnedDrawer !== null)}>
      <header className="toolbar">{toolbar}</header>
      {conversation}
      {stage}
      {pinnedDrawer}
    </div>
  );
}
