/**
 * What the notices keep clear of (1-4), registered by whoever draws it: the workbench's rail and composer, the
 * attention shell's side and bottom, a workspace's floating toolbar. The stack sits left of what stands at the
 * right edge and above what lies along the bottom where they share columns (attention.ts, `clearance`).
 */

import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";

export type RegionEdge = "right" | "bottom";

export interface NoticeRegion {
  readonly element: Element;
  readonly edge: RegionEdge;
}

const regions = new Set<NoticeRegion>();
const listeners = new Set<() => void>();
const changed = () => { for (const listener of listeners) listener(); };

export const noticeRegions = (): readonly NoticeRegion[] => [...regions];

export function subscribeRegions(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** Keeps the notices clear of the element `ref` holds, for as long as it is mounted. */
export function useNoticeRegion(ref: RefObject<Element | null>, edge: RegionEdge): void {
  const registered = useRef<NoticeRegion | null>(null);
  // Checked after every render, registered again only when the element itself changes.
  useLayoutEffect(() => {
    const element = ref.current;
    if (registered.current?.element === element && registered.current?.edge === edge) return;
    if (registered.current) regions.delete(registered.current);
    registered.current = element ? { element, edge } : null;
    if (registered.current) regions.add(registered.current);
    changed();
  });
  useEffect(() => () => {
    if (!registered.current) return;
    regions.delete(registered.current);
    registered.current = null;
    changed();
  }, []);
}
