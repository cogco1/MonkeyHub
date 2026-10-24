/**
 * Which Hub shell is on screen (2-3c): the attention shell by default, the workbench while someone has gone back to
 * it on this machine. The choice is kept in localStorage (monkeyhub.shell), not in the user settings, so a Hub
 * that does not know it still reads its settings; D-1 removes it with the workbench. `?shell=attention|workbench`
 * picks one for tests and links without changing the choice.
 */

import { useSyncExternalStore } from "react";

export type ShellLayout = "attention" | "workbench";
export const SHELL_KEY = "monkeyhub.shell";

const listeners = new Set<() => void>();

function stored(): ShellLayout {
  try { return localStorage.getItem(SHELL_KEY) === "workbench" ? "workbench" : "attention"; } catch { return "attention"; }
}

function asked(): ShellLayout | null {
  const value = new URLSearchParams(window.location.search).get("shell");
  return value === "attention" || value === "workbench" ? value : null;
}

let current: ShellLayout = asked() ?? stored();

export const shellLayout = (): ShellLayout => current;

/** Go to the other shell: at once, with the same conversation, drafts and open surfaces, and kept for next time. */
export function chooseShell(next: ShellLayout): void {
  try { localStorage.setItem(SHELL_KEY, next); } catch { /* the choice lasts this page */ }
  if (asked()) {
    const url = new URL(window.location.href);
    url.searchParams.delete("shell");
    window.history.replaceState(window.history.state, "", url);
  }
  if (current === next) return;
  current = next;
  for (const listener of listeners) listener();
}

export function useShellLayout(): ShellLayout {
  return useSyncExternalStore((listener) => { listeners.add(listener); return () => { listeners.delete(listener); }; }, shellLayout);
}
