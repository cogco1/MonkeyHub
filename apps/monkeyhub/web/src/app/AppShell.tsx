/** Layout inside the Hub's project workspace. The Hub owns application chrome. */
import type { ReactNode } from "react";

export function AppShell({ conversation, stage, pinnedDrawer }: {
  conversation: ReactNode;
  stage: ReactNode;
  pinnedDrawer: ReactNode;
}) {
  return <div className="app" data-conversation={String(conversation !== null)}
    data-pinned={String(pinnedDrawer !== null)}>
    {conversation}{stage}{pinnedDrawer}
  </div>;
}
