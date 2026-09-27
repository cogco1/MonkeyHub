import { useLayoutEffect, type RefObject } from "react";

/**
 * #351: a bar's menus wrap onto another row instead of scrolling. A separator whose next item
 * wrapped would end its row, so it is marked `data-row-end` for the page to keep it out of sight
 * (hidden, not removed, so the rows do not reflow).
 */
export function useRowEndSeparators(root: RefObject<HTMLElement | null>): void {
  useLayoutEffect(() => {
    const items = root.current?.querySelector<HTMLElement>(".surface-bar__items");
    if (!items) return;
    const mark = () => {
      for (const separator of items.querySelectorAll<HTMLElement>(":scope > .menu-separator")) {
        const next = separator.nextElementSibling;
        separator.toggleAttribute("data-row-end", !next || next.getBoundingClientRect().top >= separator.getBoundingClientRect().bottom);
      }
    };
    mark();
    const observer = new ResizeObserver(mark);
    observer.observe(items);
    return () => observer.disconnect();
  });
}
