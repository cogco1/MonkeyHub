import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";

/** #337: one entry of a Hub menu. A menu is short words; the few choices that are states carry a mark. */
export type HubMenuItem =
  | { kind: "command"; id: string; label: string; onSelect: () => void; disabled?: boolean; hint?: string }
  | { kind: "check"; id: string; label: string; checked: boolean; onSelect: () => void }
  | { kind: "choice"; id: string; label: string; checked: boolean; onSelect: () => void }
  | { kind: "heading"; id: string; label: string }
  | { kind: "separator"; id: string };
export type HubMenu = { id: string; label: string; items: readonly HubMenuItem[] };

const actionable = (item: HubMenuItem) => (item.kind === "command" && !item.disabled) || item.kind === "check" || item.kind === "choice";

/**
 * #337 L0: the Hub's text menu row, the way desktop apps have one: words that open short
 * menus. A menubar by keyboard: Left and Right walk the words, Down or Enter opens one,
 * arrows walk its entries, Enter picks, Escape closes back to the word.
 */
export function HubMenuBar({ label, menus, before }: { label: string; menus: readonly HubMenu[]; before?: ReactNode }) {
  const [open, setOpen] = useState<string | null>(null);
  const [focusIndex, setFocusIndex] = useState(0);
  const bar = useRef<HTMLDivElement>(null);
  // The element that had focus before a menu opened: Edit's entries act on it.
  const returnFocus = useRef<Element | null>(null);

  useEffect(() => {
    if (open === null) return;
    const close = (event: PointerEvent) => { if (!bar.current?.contains(event.target as Node)) setOpen(null); };
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [open]);
  useEffect(() => {
    // Opening a menu puts focus on its first entry that can act.
    if (open === null) return;
    bar.current?.querySelector<HTMLElement>(`#hub-menu-list-${open} [data-actionable="true"]`)?.focus();
  }, [open]);

  const topButton = (index: number) => bar.current?.querySelectorAll<HTMLButtonElement>(".hub-menu__button")[index];
  const openMenu = (index: number) => {
    const wrapped = (index + menus.length) % menus.length;
    if (open === null) returnFocus.current = document.activeElement;
    setFocusIndex(wrapped); setOpen(menus[wrapped].id);
  };
  const closeMenu = (focusTop = true) => {
    setOpen(null);
    if (focusTop) topButton(focusIndex)?.focus();
  };
  const onTopKey = (event: ReactKeyboardEvent<HTMLButtonElement>, index: number) => {
    const move = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (move) {
      event.preventDefault();
      const next = (index + move + menus.length) % menus.length;
      if (open !== null) openMenu(next); else { setFocusIndex(next); topButton(next)?.focus(); }
    } else if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault(); openMenu(index);
    } else if (event.key === "Escape" && open !== null) {
      event.preventDefault(); closeMenu();
    }
  };
  const onListKey = (event: ReactKeyboardEvent<HTMLDivElement>, index: number) => {
    const entries = [...event.currentTarget.querySelectorAll<HTMLElement>('[data-actionable="true"]')];
    const current = entries.indexOf(document.activeElement as HTMLElement);
    const target = event.key === "ArrowDown" ? current + 1 : event.key === "ArrowUp" ? current - 1
      : event.key === "Home" ? 0 : event.key === "End" ? entries.length - 1 : null;
    if (target !== null && entries.length) {
      event.preventDefault(); entries[(target + entries.length) % entries.length].focus(); return;
    }
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") { event.preventDefault(); openMenu(index + (event.key === "ArrowRight" ? 1 : -1)); }
    else if (event.key === "Escape") { event.preventDefault(); closeMenu(); }
    else if (event.key === "Tab") setOpen(null);
  };
  const pick = (item: HubMenuItem) => {
    if (!actionable(item) || item.kind === "heading" || item.kind === "separator") return;
    setOpen(null);
    // Give focus back first, so an entry that acts on the focused field (Edit) finds it.
    const back = returnFocus.current;
    if (back instanceof HTMLElement && back.isConnected) back.focus(); else topButton(focusIndex)?.focus();
    item.onSelect();
  };

  return <div className="hub-menubar" ref={bar}>
    {before}
    <div className="hub-menubar__menus" role="menubar" aria-label={label}>
      {menus.map((menu, index) => <div className="hub-menu" key={menu.id}>
        <button type="button" role="menuitem" id={`hub-menu-${menu.id}`} className="hub-menu__button" aria-haspopup="menu"
          aria-expanded={open === menu.id} aria-controls={open === menu.id ? `hub-menu-list-${menu.id}` : undefined}
          tabIndex={index === focusIndex ? 0 : -1} onClick={() => (open === menu.id ? closeMenu(false) : openMenu(index))}
          onKeyDown={(event) => onTopKey(event, index)}>{menu.label}</button>
        {open === menu.id && <div className="hub-menu__list" role="menu" id={`hub-menu-list-${menu.id}`} aria-labelledby={`hub-menu-${menu.id}`}
          onKeyDown={(event) => onListKey(event, index)}>
          {menu.items.map((item) => item.kind === "separator" ? <div key={item.id} className="hub-menu__separator" role="separator" />
            : item.kind === "heading" ? <div key={item.id} className="hub-menu__heading" role="presentation">{item.label}</div>
            : <button key={item.id} type="button" className="hub-menu__item" tabIndex={-1} data-actionable={actionable(item)}
              role={item.kind === "choice" ? "menuitemradio" : item.kind === "check" ? "menuitemcheckbox" : "menuitem"}
              aria-checked={item.kind === "choice" || item.kind === "check" ? item.checked : undefined}
              aria-disabled={item.kind === "command" && item.disabled ? true : undefined} onClick={() => pick(item)}>
              <span className="hub-menu__mark" aria-hidden="true">{(item.kind === "choice" || item.kind === "check") && item.checked ? "✓" : ""}</span>
              <span>{item.label}</span>
              {item.kind === "command" && item.hint && <span className="hub-menu__hint">{item.hint}</span>}
            </button>)}
        </div>}
      </div>)}
    </div>
  </div>;
}

/** Edit entries act on whatever had focus: a text field edits itself; a canvas gets its own shortcut. */
export function editFocused(kind: "undo" | "redo" | "cut" | "copy" | "paste" | "selectAll") {
  const target = document.activeElement;
  const editable = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement
    || (target instanceof HTMLElement && target.isContentEditable);
  if (kind === "paste") {
    // Pages may not paste by command; the clipboard is read and typed in, so the field's own undo still works.
    if (editable) void navigator.clipboard?.readText().then((text) => { if (text) document.execCommand("insertText", false, text); }, () => undefined);
    return;
  }
  if (editable || kind === "copy" || kind === "cut" || kind === "selectAll") {
    // execCommand is deprecated but still the one path that edits a focused field with its own undo history.
    document.execCommand(kind);
    return;
  }
  const key = { key: kind === "redo" ? "y" : "z", code: kind === "redo" ? "KeyY" : "KeyZ", ctrlKey: true, bubbles: true, cancelable: true };
  (target ?? document.body).dispatchEvent(new KeyboardEvent("keydown", key));
}
