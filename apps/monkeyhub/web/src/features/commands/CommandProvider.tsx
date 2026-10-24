import { createContext, startTransition, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { usePreferences } from "../settings/preferences";
import { CommandPalette } from "./CommandPalette";
import { hubMayTake, normalizeShortcut, PALETTE_SHORTCUT, shortcutOf } from "./keys";
import { COMMAND_TREE } from "./tree.generated";
import type { Command, CommandTree } from "./tree";
import { readUsage, recordUse, type CommandUsage, type UseSource } from "./usage";

/**
 * The surface that performs a command registers a handler under the command's id while it
 * is mounted; Ctrl+K, shortcuts and (later) cards run it by that id. A command whose
 * surface is not open is run by opening that surface first: it runs once the surface
 * registers it. Besides commands, the shell registers `surface.open:<surface>` and `chat.ask`.
 */
/** What a run is handed: words typed for it, or the arguments a card or a generated command carries. */
export interface CommandInput {
  readonly text?: string;
  readonly args?: Readonly<Record<string, unknown>>;
}

export interface CommandHandler {
  run: (input: CommandInput) => unknown;
  /** True when it can run now, or the sentence saying why not. */
  available?: () => true | string;
}

/** Shell actions the provider itself relies on. */
export const ASK_AI_ACTION = "chat.ask";
export const openSurfaceAction = (surface: string) => `surface.open:${surface}`;

export type CommandState =
  | { kind: "ready" }
  | { kind: "opens"; surface: string }
  /** No handler, but its standing entry (data-command) is on screen: running it points there. */
  | { kind: "locate" }
  | { kind: "unavailable"; reason: string | null };

interface CommandsValue {
  register(action: string, handler: CommandHandler): () => void;
  run(id: string, source: UseSource, input?: CommandInput): boolean;
  /** Counts a use of a command carried out elsewhere (a card's answer, by the Hub); only a command the tree has. */
  note(id: string, source: UseSource): void;
  /** `onScreen` is the set of commands whose standing entry is shown, read once by the caller; without it the page is read. */
  stateOf(command: Command, onScreen?: ReadonlySet<string>): CommandState;
  openPalette(): void;
  tree: CommandTree;
  usage: CommandUsage;
}

const CommandsContext = createContext<CommandsValue | null>(null);

/** How long a command waits for the surface it opened to register its action or show its entry. */
const PENDING_MS = 8000;
const LOOK_MS = 150;

const storage = () => { try { return window.localStorage; } catch { return null; } };

/** The command's standing entry, when one is on screen. */
function entryOf(id: string): HTMLElement | null {
  const element = document.querySelector<HTMLElement>(`[data-command="${CSS.escape(id)}"]`);
  return element && element.getClientRects().length ? element : null;
}

/** Every command with a standing entry on screen, in one pass over the page. */
export function entriesOnScreen(): Set<string> {
  const ids = new Set<string>();
  for (const element of document.querySelectorAll<HTMLElement>("[data-command]")) {
    const id = element.getAttribute("data-command");
    if (id && element.getClientRects().length) ids.add(id);
  }
  return ids;
}

/** An entry that cannot act now (disabled, or marked aria-disabled as menu items are) is not a use. */
function inactive(entry: Element): boolean {
  return (entry as HTMLButtonElement).disabled === true || entry.closest('[aria-disabled="true"]') !== null;
}

/** Bring a standing entry into view, focus it and mark it for a moment, without pressing it. */
function pointAt(element: HTMLElement): void {
  element.scrollIntoView({ block: "nearest", inline: "nearest" });
  element.focus({ preventScroll: true });
  element.classList.remove("command-located");
  void element.offsetWidth;
  element.classList.add("command-located");
  window.setTimeout(() => element.classList.remove("command-located"), 1800);
}

export function CommandProvider({ children, tree = COMMAND_TREE }: { children: ReactNode; tree?: CommandTree }) {
  const { language } = usePreferences();
  const handlers = useRef(new Map<string, CommandHandler>());
  const pending = useRef<{ action: string; input: CommandInput; until: number } | null>(null);
  const looking = useRef<number | null>(null);
  const [version, setVersion] = useState(0);
  const [open, setOpen] = useState(false);
  const [usage, setUsage] = useState(() => readUsage(storage()));
  const byId = useMemo(() => new Map(tree.commands.map((command) => [command.id, command])), [tree]);
  const byShortcut = useMemo(() => new Map(tree.commands.filter((command) => command.shortcut)
    .map((command) => [normalizeShortcut(command.shortcut!), command])), [tree]);

  const register = useCallback((action: string, handler: CommandHandler) => {
    handlers.current.set(action, handler);
    setVersion((n) => n + 1);
    const waiting = pending.current;
    if (waiting && waiting.action === action) {
      pending.current = null;
      if (looking.current !== null) { window.clearInterval(looking.current); looking.current = null; }
      if (Date.now() <= waiting.until && (handler.available?.() ?? true) === true) void handler.run(waiting.input);
    }
    return () => {
      if (handlers.current.get(action) === handler) { handlers.current.delete(action); setVersion((n) => n + 1); }
    };
  }, []);

  // `version` is a dependency only so the states are read again when a surface registers or leaves.
  const stateOf = useCallback((command: Command, onScreen?: ReadonlySet<string>): CommandState => {
    const handler = handlers.current.get(command.id);
    if (handler) {
      const available = handler.available?.() ?? true;
      return available === true ? { kind: "ready" } : { kind: "unavailable", reason: available };
    }
    if (onScreen ? onScreen.has(command.id) : entryOf(command.id)) return { kind: "locate" };
    if (command.surface !== "hub" && handlers.current.has(openSurfaceAction(command.surface))) return { kind: "opens", surface: command.surface };
    return { kind: "unavailable", reason: null };
  }, [version]);

  const run = useCallback((id: string, source: UseSource, input: CommandInput = {}) => {
    const command = byId.get(id);
    if (!command) return false;
    const state = stateOf(command);
    if (state.kind === "unavailable") return false;
    setUsage(recordUse(storage(), id, source));
    if (state.kind === "ready") {
      void handlers.current.get(command.id)!.run(input);
      return true;
    }
    if (state.kind === "locate") {
      pointAt(entryOf(command.id)!);
      return true;
    }
    // Open the surface; the command runs when the surface registers it, or its entry is pointed at once shown.
    const until = Date.now() + PENDING_MS;
    pending.current = { action: command.id, input, until };
    if (looking.current !== null) window.clearInterval(looking.current);
    looking.current = window.setInterval(() => {
      const entry = pending.current?.action === command.id ? entryOf(command.id) : null;
      if (entry || Date.now() > until || pending.current?.action !== command.id) {
        window.clearInterval(looking.current!); looking.current = null;
        if (pending.current?.action === command.id) pending.current = null;
        if (entry) pointAt(entry);
      }
    }, LOOK_MS);
    void handlers.current.get(openSurfaceAction(command.surface))!.run({});
    return true;
  }, [byId, stateOf]);

  useEffect(() => {
    // A standing entry that names its command (data-command) is counted with the palette's uses.
    const onClick = (event: MouseEvent) => {
      const entry = (event.target as Element | null)?.closest?.("[data-command]");
      const id = entry?.getAttribute("data-command");
      if (!entry || !id || !byId.has(id) || inactive(entry)) return;
      // This listener runs before React handles the click. An urgent update here would re-render before then,
      // and a controlled checkbox in that render takes back the change the click just made, so onChange never
      // fires. The use is stored now; the page sees it after the click is handled.
      const next = recordUse(storage(), id, "button");
      startTransition(() => setUsage(next));
    };
    window.addEventListener("click", onClick, true);
    return () => window.removeEventListener("click", onClick, true);
  }, [byId]);

  useEffect(() => {
    // Capture phase on the window: the Hub sees Ctrl+K before a canvas (Excalidraw binds it to links).
    const onKey = (event: KeyboardEvent) => {
      const shortcut = shortcutOf(event);
      if (!shortcut || !hubMayTake(event, shortcut, event.target)) return;
      if (shortcut === PALETTE_SHORTCUT) {
        event.preventDefault(); event.stopPropagation();
        setOpen((was) => !was);
        return;
      }
      const command = byShortcut.get(shortcut);
      if (!command || stateOf(command).kind === "unavailable") return;
      event.preventDefault(); event.stopPropagation();
      run(command.id, "shortcut");
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [byShortcut, run, stateOf]);

  const note = useCallback((id: string, source: UseSource) => { if (byId.has(id)) setUsage(recordUse(storage(), id, source)); }, [byId]);
  const value = useMemo<CommandsValue>(() => ({ register, run, note, stateOf, openPalette: () => setOpen(true), tree, usage }),
    [register, run, note, stateOf, tree, usage]);
  return <CommandsContext.Provider value={value}>
    {children}
    {open && <CommandPalette language={language} onClose={() => setOpen(false)}
      askAI={(handlers.current.get(ASK_AI_ACTION)?.available?.() ?? handlers.current.has(ASK_AI_ACTION)) === true
        ? (text) => { setOpen(false); window.setTimeout(() => void handlers.current.get(ASK_AI_ACTION)?.run({ text }), 0); } : null} />}
  </CommandsContext.Provider>;
}

export function useCommands(): CommandsValue {
  const value = useContext(CommandsContext);
  if (value === null) throw new Error("useCommands must be used inside CommandProvider.");
  return value;
}

/**
 * Register the actions this component performs while it is mounted. The latest closures
 * always run; the registration itself changes only when the set of action names does.
 */
export function useCommandActions(actions: Readonly<Record<string, CommandHandler>>): void {
  const value = useContext(CommandsContext);
  const latest = useRef(actions);
  latest.current = actions;
  const names = Object.keys(actions).sort().join("\n");
  useEffect(() => {
    if (!value) return;
    const releases = names.split("\n").filter(Boolean).map((name) => value.register(name, {
      run: (input) => latest.current[name]?.run(input),
      available: () => latest.current[name]?.available?.() ?? true,
    }));
    return () => releases.forEach((release) => release());
  }, [value?.register, names]);
}
