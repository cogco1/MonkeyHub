import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

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
  | { kind: "unavailable"; reason: string | null };

interface CommandsValue {
  register(action: string, handler: CommandHandler): () => void;
  run(id: string, source: UseSource, input?: CommandInput): boolean;
  stateOf(command: Command): CommandState;
  openPalette(): void;
  tree: CommandTree;
  usage: CommandUsage;
}

const CommandsContext = createContext<CommandsValue | null>(null);

/** How long a command waits for the surface it opened to register its action. */
const PENDING_MS = 8000;

const storage = () => { try { return window.localStorage; } catch { return null; } };

export function CommandProvider({ children, tree = COMMAND_TREE }: { children: ReactNode; tree?: CommandTree }) {
  const { language } = usePreferences();
  const handlers = useRef(new Map<string, CommandHandler>());
  const pending = useRef<{ action: string; input: CommandInput; until: number } | null>(null);
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
      if (Date.now() <= waiting.until && (handler.available?.() ?? true) === true) void handler.run(waiting.input);
    }
    return () => {
      if (handlers.current.get(action) === handler) { handlers.current.delete(action); setVersion((n) => n + 1); }
    };
  }, []);

  // `version` is a dependency only so the states are read again when a surface registers or leaves.
  const stateOf = useCallback((command: Command): CommandState => {
    const handler = handlers.current.get(command.id);
    if (handler) {
      const available = handler.available?.() ?? true;
      return available === true ? { kind: "ready" } : { kind: "unavailable", reason: available };
    }
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
    pending.current = { action: command.id, input, until: Date.now() + PENDING_MS };
    void handlers.current.get(openSurfaceAction(command.surface))!.run({});
    return true;
  }, [byId, stateOf]);

  useEffect(() => {
    // A standing entry that names its command (data-command) is counted with the palette's uses.
    const onClick = (event: MouseEvent) => {
      const entry = (event.target as Element | null)?.closest?.("[data-command]");
      const id = entry?.getAttribute("data-command");
      if (!id || !byId.has(id) || (entry instanceof HTMLButtonElement && entry.disabled)) return;
      setUsage(recordUse(storage(), id, "button"));
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

  const value = useMemo<CommandsValue>(() => ({ register, run, stateOf, openPalette: () => setOpen(true), tree, usage }),
    [register, run, stateOf, tree, usage]);
  return <CommandsContext.Provider value={value}>
    {children}
    {open && <CommandPalette language={language} onClose={() => setOpen(false)}
      askAI={(handlers.current.get(ASK_AI_ACTION)?.available?.() ?? handlers.current.has(ASK_AI_ACTION)) === true
        ? (text) => { setOpen(false); void handlers.current.get(ASK_AI_ACTION)!.run({ text }); } : null} />}
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
