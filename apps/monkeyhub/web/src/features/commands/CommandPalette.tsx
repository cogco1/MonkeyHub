import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { useT, type TFunction } from "../../i18n/useT";
import { usePreferences } from "../settings/preferences";
import type { MessageKey } from "../../i18n/messages.en";
import { useCommands, type CommandState } from "./CommandProvider";
import { rankCommands, type Language } from "./search";
import type { Command } from "./tree";
import "./commands.css";

/** Rows shown at once; typing narrows the rest. */
const SHOWN = 80;

type Row =
  | { kind: "heading"; key: string; label: string }
  | { kind: "command"; key: string; command: Command; state: CommandState }
  | { kind: "ask"; key: string; text: string };

/** The advanced tools' twelve groups, in the order they are listed. */
const GROUPS = ["shell", "settings", "project_bar", "modeling", "board", "layout", "document", "drawings", "render",
  "design_tree", "fabrication", "usage"] as const;
const SURFACES = ["hub", ...GROUPS.filter((group) => group !== "shell" && group !== "settings" && group !== "project_bar")] as const;

function groupLabel(t: TFunction, group: string): string {
  return (GROUPS as readonly string[]).includes(group) ? t(`commands.group.${group}` as MessageKey) : group;
}
function surfaceLabel(t: TFunction, surface: string): string {
  return (SURFACES as readonly string[]).includes(surface) ? t(`commands.surface.${surface}` as MessageKey) : surface;
}

/** Ctrl+K: every command the tree holds, the ones that cannot run here greyed with why. */
export function CommandPalette({ language, onClose, askAI }: {
  language: Language;
  onClose: () => void;
  askAI: ((text: string) => void) | null;
}) {
  const t = useT();
  const { tree, usage, run, stateOf } = useCommands();
  const { developerMode } = usePreferences();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
  }, []);

  const rows = useMemo<Row[]>(() => {
    const ranked = rankCommands(tree, query, language, usage.recent, developerMode).slice(0, SHOWN);
    const out: Row[] = [];
    if (!query.trim()) {
      // Recent first, then one heading per advanced-tools group, in the groups' order.
      const order = (group: string) => { const index = (GROUPS as readonly string[]).indexOf(group); return index < 0 ? GROUPS.length : index; };
      const recent = ranked.filter((row) => row.recent);
      const rest = ranked.filter((row) => !row.recent).map((row, index) => ({ ...row, index }))
        .sort((a, b) => order(a.command.group) - order(b.command.group) || a.index - b.index);
      let heading: string | null = null;
      for (const { command, recent: isRecent } of [...recent, ...rest]) {
        const key = isRecent ? "recent" : command.group;
        if (key !== heading) {
          out.push({ kind: "heading", key: `h:${key}`, label: isRecent ? t("commands.recent") : groupLabel(t, command.group) });
          heading = key;
        }
        out.push({ kind: "command", key: command.id, command, state: stateOf(command) });
      }
    } else {
      for (const { command } of ranked) out.push({ kind: "command", key: command.id, command, state: stateOf(command) });
    }
    if (askAI && query.trim()) out.push({ kind: "ask", key: "ask", text: query.trim() });
    return out;
  }, [tree, query, language, usage.recent, developerMode, stateOf, askAI, t]);
  const choices = rows.filter((row) => row.kind !== "heading");
  const current = choices[Math.min(active, choices.length - 1)];

  useEffect(() => { setActive(0); }, [query]);
  useEffect(() => {
    if (!current) return;
    list.current?.querySelector<HTMLElement>(`[data-row="${CSS.escape(current.key)}"]`)?.scrollIntoView({ block: "nearest" });
  }, [current]);

  const choose = (row: Row | undefined) => {
    if (!row) return;
    if (row.kind === "ask") { askAI?.(row.text); return; }
    if (row.kind !== "command" || row.state.kind === "unavailable") return;
    onClose();
    // Run once the palette is gone: while a modal dialog is open nothing else can take focus.
    window.setTimeout(() => run(row.command.id, "palette"), 0);
  };
  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    const step = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
    if (step && choices.length) {
      event.preventDefault();
      setActive((index) => (Math.min(index, choices.length - 1) + step + choices.length) % choices.length);
    } else if (event.key === "Enter" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      choose(current);
    }
  };
  const optionId = (key: string) => `command-option-${key.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
  const stateText = (state: CommandState) => state.kind === "opens" ? t("commands.opens", { surface: surfaceLabel(t, state.surface) })
    : state.kind === "locate" ? t("commands.locate")
    : state.kind === "unavailable" ? state.reason ?? t("commands.unavailable") : null;

  return <dialog ref={dialog} className="command-palette" aria-label={t("commands.palette")}
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClick={(event) => { if (event.target === dialog.current) onClose(); }}>
    <div className="command-palette__box">
      <input className="command-palette__input" autoFocus type="text" role="combobox" aria-expanded="true"
        aria-controls="command-palette-list" aria-activedescendant={current ? optionId(current.key) : undefined}
        aria-label={t("commands.palette")} placeholder={t("commands.placeholder")} value={query}
        onChange={(event) => setQuery(event.target.value)} onKeyDown={onKey} spellCheck={false} autoComplete="off" />
      <div className="command-palette__list" id="command-palette-list" role="listbox" ref={list} aria-label={t("commands.palette")}>
        {rows.map((row) => {
          if (row.kind === "heading") return <div key={row.key} className="command-palette__heading" role="presentation">{row.label}</div>;
          const selected = current?.key === row.key;
          if (row.kind === "ask") return <div key={row.key} id={optionId(row.key)} data-row={row.key} role="option" aria-selected={selected}
            className="command-palette__row command-palette__row--ask" onMouseMove={() => setActive(choices.indexOf(row))} onClick={() => choose(row)}>
            <span className="command-palette__label">{t("commands.askAI", { text: row.text })}</span>
          </div>;
          const note = stateText(row.state);
          return <div key={row.key} id={optionId(row.key)} data-row={row.key} role="option" aria-selected={selected}
            aria-disabled={row.state.kind === "unavailable" || undefined} className="command-palette__row"
            onMouseMove={() => setActive(choices.indexOf(row))} onClick={() => choose(row)}>
            <span className="command-palette__label">{row.command.label[language]}</span>
            <span className="command-palette__meta">{note ?? (query.trim() ? groupLabel(t, row.command.group) : "")}</span>
            {row.command.shortcut && <kbd className="command-palette__key">{row.command.shortcut}</kbd>}
          </div>;
        })}
        {query.trim() && !rows.some((row) => row.kind === "command")
          && <div className="command-palette__empty" role="presentation">{t("commands.empty", { query: query.trim() })}</div>}
      </div>
      <div className="command-palette__foot" aria-hidden="true">{t("commands.keys")}</div>
    </div>
  </dialog>;
}
