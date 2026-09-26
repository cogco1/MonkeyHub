/**
 * #337: the chrome every surface shares, drawn as words and lines. A surface has one bar
 * (L2): what it shows and does, as words, with its state quietly at the right end; and one
 * status line (L5): what is selected, whether it is saved, how to work it. Instruments, a
 * surface's own tool palette or a card that asks for action, stay the surface's own.
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";
import "./chrome.css";

export function SurfaceBar({ label, children, end }: { label: string; children?: ReactNode; end?: ReactNode }) {
  return <div className="surface-bar" role="group" aria-label={label}>
    <div className="surface-bar__items">{children}</div>
    {end != null && end !== false && <div className="surface-bar__end">{end}</div>}
  </div>;
}

/** Parallel views or modes as words; the one showing is underlined. */
export function MenuTabs<T extends string>({ label, value, options, onChange }: {
  label: string; value: T; options: readonly { value: T; label: string }[]; onChange(value: T): void;
}) {
  return <div className="menu-tabs" role="group" aria-label={label}>
    {options.map((option) => <button key={option.value} type="button" className="menu-tab" aria-pressed={option.value === value}
      onClick={() => onChange(option.value)}>{option.label}</button>)}
  </div>;
}

/** A command as a word. */
export function MenuCommand({ className, children, ...button }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" className={className ? `menu-command ${className}` : "menu-command"} {...button}>{children}</button>;
}

export function MenuSeparator() {
  return <span className="menu-separator" aria-hidden="true" />;
}

export function StatusLine({ children, end }: { children?: ReactNode; end?: ReactNode }) {
  return <div className="status-line">
    <span className="status-line__start">{children}</span>
    {end != null && end !== false && <span className="status-line__end">{end}</span>}
  </div>;
}
