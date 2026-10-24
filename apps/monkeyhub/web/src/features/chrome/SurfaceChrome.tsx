/**
 * #337: the chrome every surface shares, drawn as words and lines. A surface has one bar
 * (L2): what it shows and does, as words, with its state quietly at the right end; and one
 * status line (L5): what is selected, whether it is saved, how to work it. Instruments, a
 * surface's own tool palette or a card that asks for action, stay the surface's own.
 */
import { createContext, useContext, useMemo, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { COMMAND_TREE } from "../commands/tree.generated";
import "./chrome.css";

export function SurfaceBar({ label, children, end }: { label: string; children?: ReactNode; end?: ReactNode }) {
  return <div className="surface-bar" role="group" aria-label={label}>
    <div className="surface-bar__items">{children}</div>
    {end != null && end !== false && <div className="surface-bar__end">{end}</div>}
  </div>;
}

const ProjectBarSlots = createContext<{
  readonly menus: HTMLElement | null;
  readonly words: HTMLElement | null;
  /** The commands the bar's menus may show, or null for all of them. */
  readonly front: ReadonlySet<string> | null;
} | null>(null);
/** Set inside a surface's menus, where a MenuCommand the bar does not show is left out. */
const InBar = createContext(false);
const FRONT = new Set(COMMAND_TREE.commands.filter((command) => command.channel === "front").map((command) => command.id));

/**
 * Which of a surface's own entries its project bar shows (G1). In the attention shell the Board and Modeling bars
 * show only the command tree's front commands: the rest are one press away in the advanced tools and Ctrl K. A field
 * that is not a command is then left out too. Elsewhere every entry shows.
 */
export function useBarEntries(): { readonly frontOnly: boolean; shown(command: string): boolean } {
  const front = useContext(ProjectBarSlots)?.front ?? null;
  return useMemo(() => ({ frontOnly: front !== null, shown: (command: string) => front === null || front.has(command) }), [front]);
}

/**
 * The project's one bar over all its surfaces: the surface on screen puts its menus on the
 * left (`SurfaceMenus`), and the project's position stays at the right end, mounted once for
 * every surface. With nothing to show, the bar takes no room.
 */
export function ProjectBar({ label, lead, position, frontOnly = false, children }: {
  label: string;
  /** Menus the project itself shows on the left, such as the Board | Layout switch. */
  lead?: ReactNode;
  position?: ReactNode;
  /** The surface's menus show only the command tree's front commands (useBarEntries). */
  frontOnly?: boolean;
  children?: ReactNode;
}) {
  const [menus, setMenus] = useState<HTMLElement | null>(null);
  const [words, setWords] = useState<HTMLElement | null>(null);
  const slots = useMemo(() => ({ menus, words, front: frontOnly ? FRONT : null }), [menus, words, frontOnly]);
  return <>
    <div className="surface-bar project-bar" role="group" aria-label={label}>
      <div className="surface-bar__items"><span className="project-bar__lead">{lead}</span><span className="menu-separator project-bar__gap" aria-hidden="true" />
        <span className="project-bar__menus" ref={setMenus} /></div>
      <div className="surface-bar__end"><span className="project-bar__words" ref={setWords} />{position}</div>
    </div>
    <ProjectBarSlots.Provider value={slots}>{children}</ProjectBarSlots.Provider>
  </>;
}

/**
 * A surface's menus and quiet words: in the project bar while the surface is on screen, or in
 * a bar of its own where there is no project bar.
 */
export function SurfaceMenus({ label, active, children, end }: { label: string; active: boolean; children?: ReactNode; end?: ReactNode }) {
  const slots = useContext(ProjectBarSlots);
  if (!slots) return <SurfaceBar label={label} end={end}>{children}</SurfaceBar>;
  if (!active) return null;
  return <InBar.Provider value>
    {slots.menus && createPortal(children, slots.menus)}
    {slots.words && end != null && end !== false && createPortal(end, slots.words)}
  </InBar.Provider>;
}

/** Parallel views or modes as words; the one showing is underlined. */
export function MenuTabs<T extends string>({ label, value, options, onChange }: {
  label: string; value: T; options: readonly { value: T; label: string; command?: string }[]; onChange(value: T): void;
}) {
  return <div className="menu-tabs" role="group" aria-label={label}>
    {options.map((option) => <button key={option.value} type="button" className="menu-tab" data-command={option.command} aria-pressed={option.value === value}
      onClick={() => onChange(option.value)}>{option.label}</button>)}
  </div>;
}

/** A command as a word. */
export function MenuCommand({ className, children, ...button }: ButtonHTMLAttributes<HTMLButtonElement>) {
  const inBar = useContext(InBar);
  const { shown } = useBarEntries();
  const command = (button as { "data-command"?: string })["data-command"];
  // A menu's own toggle shows whenever its surface renders the menu, which it does only with an entry to show.
  if (inBar && command && command !== "view.menu.toggle" && !shown(command)) return null;
  return <button type="button" className={className ? `menu-command ${className}` : "menu-command"} {...button}>{children}</button>;
}

export function MenuSeparator() {
  return <span className="menu-separator" aria-hidden="true" />;
}

export function StatusLine({ children, end, className }: { children?: ReactNode; end?: ReactNode; className?: string }) {
  return <div className={className ? `status-line ${className}` : "status-line"}>
    <span className="status-line__start">{children}</span>
    {end != null && end !== false && <span className="status-line__end">{end}</span>}
  </div>;
}
