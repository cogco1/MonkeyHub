import { useMemo, useState } from "react";

import { entriesOnScreen, useCommands } from "../features/commands/CommandProvider";
import { searchable } from "../features/commands/search";
import { usePreferences } from "../features/settings/preferences";
import type { MessageKey } from "../i18n/messages.en";
import { useT } from "../i18n/useT";

/** The advanced tools' twelve groups, in the order the side panel lists them. */
const GROUPS = ["shell", "settings", "project_bar", "modeling", "board", "layout", "document", "drawings", "render",
  "design_tree", "fabrication", "usage"] as const;

/**
 * The side panel's advanced tools: every command of the tree, by group, each one press away. The
 * same commands Ctrl+K finds, run the same way; one that cannot run here is greyed with why.
 */
export function AdvancedTools() {
  const t = useT();
  const { language, developerMode } = usePreferences();
  const { tree, run, stateOf } = useCommands();
  const [onScreen] = useState(entriesOnScreen);
  const groups = useMemo(() => {
    const commands = searchable(tree, developerMode);
    const order = (group: string) => { const index = (GROUPS as readonly string[]).indexOf(group); return index < 0 ? GROUPS.length : index; };
    const byGroup = new Map<string, typeof commands>();
    for (const command of commands) byGroup.set(command.group, [...(byGroup.get(command.group) ?? []), command]);
    return [...byGroup.entries()].sort(([a], [b]) => order(a) - order(b));
  }, [tree, developerMode]);
  if (!groups.length) return <p className="attention-shell__empty">{t("shell.side.tools.empty")}</p>;
  return <div className="advanced-tools">{groups.map(([group, commands]) => <section key={group} className="advanced-tools__group">
    <h3>{(GROUPS as readonly string[]).includes(group) ? t(`commands.group.${group}` as MessageKey) : group}</h3>
    <ul>{commands.map((command) => {
      const state = stateOf(command, onScreen);
      const reason = state.kind === "unavailable" ? state.reason ?? t("commands.unavailable")
        : state.kind === "opens" ? t("commands.opens", { surface: t(`commands.surface.${state.surface}` as MessageKey) })
        : state.kind === "locate" ? t("commands.locate") : null;
      return <li key={command.id}>
        <button type="button" className="advanced-tools__command" data-row={command.id} aria-disabled={state.kind === "unavailable" || undefined}
          title={reason ?? undefined} onClick={() => { if (state.kind !== "unavailable") run(command.id, "tools"); }}>
          <span>{command.label[language]}</span>
          {reason && <small>{reason}</small>}
          {command.shortcut && <kbd>{command.shortcut}</kbd>}
        </button>
      </li>;
    })}</ul>
  </section>)}</div>;
}
