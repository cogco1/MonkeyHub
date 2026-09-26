import { useEffect, useRef, type ReactNode } from "react";
import "./DrawingMenu.css";

/**
 * #337: a word in the Drawing's bar whose panel opens under it (Draw another version, Reuse drawing recipes). The
 * panel closes on Escape, and when a pointer goes down or focus lands outside it. A control inside it turning
 * disabled while it loads, or a file dialog it opened, leaves it open.
 */
export function DrawingMenu({ label, className, panelClassName, open, onOpenChange, children }: {
  label: string; className: string; panelClassName: string; open: boolean; onOpenChange(open: boolean): void; children: ReactNode;
}) {
  const menu = useRef<HTMLDetailsElement>(null);
  const change = useRef(onOpenChange);
  change.current = onOpenChange;
  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => { if (!(event.target instanceof Node && menu.current?.contains(event.target))) change.current(false); };
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("focusin", outside, true);
    return () => { document.removeEventListener("pointerdown", outside, true); document.removeEventListener("focusin", outside, true); };
  }, [open]);
  return <details ref={menu} className={`drawing-menu ${className}`} open={open}
    onToggle={event => { if (event.currentTarget.open !== open) onOpenChange(event.currentTarget.open); }}
    onKeyDown={event => {
      if (event.key !== "Escape" || !open) return;
      event.preventDefault(); event.stopPropagation(); onOpenChange(false); menu.current?.querySelector("summary")?.focus();
    }}>
    <summary className="menu-command">{label}</summary>
    <div className={`drawing-menu__panel ${panelClassName}`}>{children}</div>
  </details>;
}
