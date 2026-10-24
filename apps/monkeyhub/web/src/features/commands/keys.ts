/**
 * The Hub's own keyboard shortcuts, read from the physical key (`event.code`) so they
 * work with a Chinese input method on and on any layout. Pure: the provider owns the
 * listener, this decides what a key press is and whether the Hub may take it.
 *
 * Ctrl+K opens the palette from anywhere, a text field included. Every other shortcut
 * waits until focus is out of a text field, so typing never triggers one.
 */

export interface KeyPress {
  readonly code: string;
  readonly ctrlKey: boolean;
  readonly metaKey: boolean;
  readonly altKey: boolean;
  readonly shiftKey: boolean;
  readonly isComposing?: boolean;
  readonly repeat?: boolean;
}

export const PALETTE_SHORTCUT = "Ctrl+K";

/** The key name a shortcut is written with: KeyK → K, Digit1 → 1; null for keys no shortcut uses. */
export function keyName(code: string): string | null {
  const letter = /^Key([A-Z])$/.exec(code);
  if (letter) return letter[1];
  const digit = /^(?:Digit|Numpad)([0-9])$/.exec(code);
  if (digit) return digit[1];
  if (/^F([1-9]|1[0-2])$/.test(code)) return code;
  return ({ Enter: "Enter", NumpadEnter: "Enter", Escape: "Esc", Space: "Space", Slash: "/",
    ArrowUp: "Up", ArrowDown: "Down", ArrowLeft: "Left", ArrowRight: "Right" } as Record<string, string>)[code] ?? null;
}

/** "Ctrl+Alt+Shift+K", in that order; Cmd counts as Ctrl. Null for a bare modifier or an unnamed key. */
export function shortcutOf(press: KeyPress): string | null {
  const key = keyName(press.code);
  if (!key) return null;
  return [press.ctrlKey || press.metaKey ? "Ctrl" : "", press.altKey ? "Alt" : "", press.shiftKey ? "Shift" : "", key]
    .filter(Boolean).join("+");
}

/** The written form of a shortcut, normalised so "alt+p" and "Alt+P" are one shortcut. */
export function normalizeShortcut(written: string): string {
  const parts = written.split("+").map((part) => part.trim()).filter(Boolean);
  const has = (name: string) => parts.some((part) => part.toLowerCase() === name.toLowerCase());
  const key = parts.filter((part) => !["ctrl", "alt", "shift", "cmd", "meta"].includes(part.toLowerCase())).at(-1) ?? "";
  if (!key) return "";
  const named = key[0].toUpperCase() + key.slice(1);
  return [has("ctrl") || has("cmd") || has("meta") ? "Ctrl" : "", has("alt") ? "Alt" : "", has("shift") ? "Shift" : "", named]
    .filter(Boolean).join("+");
}

/** Whether focus is somewhere a key press types text. */
export function typesText(target: unknown): boolean {
  if (!target || typeof target !== "object") return false;
  const element = target as { tagName?: string; type?: string; isContentEditable?: boolean };
  if (element.isContentEditable) return true;
  const tag = element.tagName?.toUpperCase();
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag !== "INPUT") return false;
  return !["button", "checkbox", "radio", "range", "color", "file", "submit", "reset", "image"].includes((element.type ?? "text").toLowerCase());
}

/** Whether the Hub takes this press for one of its shortcuts. */
export function hubMayTake(press: KeyPress, shortcut: string, target: unknown): boolean {
  if (press.isComposing || press.repeat) return false;
  if (shortcut === PALETTE_SHORTCUT) return true;
  return !typesText(target);
}
