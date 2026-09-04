/**
 * The component tree, nested, as the catalog derives it from the record.
 *
 * Each node is a component of the record with, under it, the elements the
 * record declares for it and, on the row, what the catalog says: how many
 * editable elements sit beneath it, how many capabilities they carry, how many
 * objects the model shows for it and how many of those no row produced. A
 * component whose objects are all unbound is marked as catalog-missing - the
 * model shows it, nothing can be asked of it yet - and is still selectable, so
 * the request that names it is answered with that fact rather than with a
 * neighbour's field.
 *
 * Choosing a node sets the selection from the projection's own ids; no server
 * call, because these are the server's names already. Without a catalog (no
 * reference run, or a record the kernel refused to read as a tree) the
 * components are listed flat, as before.
 */

import { useMemo, useState, type ReactElement } from "react";

import type { CatalogComponentDto, StateProjectionDto } from "../../api/generated";
import { BilingualText } from "../../i18n/BilingualText";
import { useT, type TFunction } from "../../i18n/useT";

interface Node {
  id: string;
  parentId: string | null;
  children: Node[];
  elementIds: string[];
  descendantElementIds: string[];
  capabilityCount: number;
  states: string[];
  objectCount: number;
  unboundObjectCount: number;
}

interface TreeMatch {
  componentId: string;
  elementId: string | null;
  score: number;
}

function catalogNodes(projection: StateProjectionDto): Node[] | null {
  const catalog = projection.catalog ?? null;
  if (!catalog || catalog.components.length === 0) return null;
  const byId = new Map<string, Node>();
  for (const component of catalog.components as CatalogComponentDto[]) {
    byId.set(component.componentId, {
      id: component.componentId,
      parentId: component.parentId,
      children: [],
      elementIds: component.elementIds,
      descendantElementIds: component.descendantElementIds,
      capabilityCount: component.capabilityCount,
      states: component.states,
      objectCount: component.objectCount,
      unboundObjectCount: component.unboundObjectCount,
    });
  }
  const roots: Node[] = [];
  for (const node of byId.values()) {
    const parent = node.parentId ? byId.get(node.parentId) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  const byName = (a: Node, b: Node) => a.id.localeCompare(b.id);
  for (const node of byId.values()) node.children.sort(byName);
  roots.sort(byName);
  return roots;
}

function flatNodes(projection: StateProjectionDto, t: TFunction): Node[] {
  const elementsByComponent = new Map<string, string[]>();
  for (const element of projection.elements) {
    const list = elementsByComponent.get(element.componentId) ?? [];
    list.push(element.elementId);
    elementsByComponent.set(element.componentId, list);
  }
  const ids =
    projection.componentTree?.map((node) => node.componentId) ?? [...elementsByComponent.keys()];
  void t;
  return ids.map((id) => ({
    id,
    parentId: null,
    children: [],
    elementIds: elementsByComponent.get(id) ?? [],
    descendantElementIds: elementsByComponent.get(id) ?? [],
    capabilityCount: 0,
    states: [],
    objectCount: 0,
    unboundObjectCount: 0,
  }));
}

/** Which nodes stay visible for a search: a match, or an ancestor of one. */
function matching(nodes: Node[], needle: string, keep: Set<string>): boolean {
  let any = false;
  for (const node of nodes) {
    const own =
      node.id.toLowerCase().includes(needle) ||
      node.elementIds.some((id) => id.toLowerCase().includes(needle));
    const below = matching(node.children, needle, keep);
    if (own || below) {
      keep.add(node.id);
      any = true;
    }
  }
  return any;
}

/** The deepest actual match, never an ancestor kept only to expose its path. */
function mostSpecificMatch(nodes: Node[], needle: string, depth = 0): TreeMatch | null {
  let best: TreeMatch | null = null;
  const consider = (match: TreeMatch) => {
    if (best === null || match.score > best.score) best = match;
  };
  for (const node of nodes) {
    const component = node.id.toLowerCase();
    if (component.includes(needle)) {
      consider({
        componentId: node.id,
        elementId: null,
        score: (component === needle ? 1_000_000 : 0) + depth * 2,
      });
    }
    for (const elementId of node.elementIds) {
      const element = elementId.toLowerCase();
      if (element.includes(needle)) {
        consider({
          componentId: node.id,
          elementId,
          score: (element === needle ? 1_000_000 : 0) + depth * 2 + 1,
        });
      }
    }
    const below = mostSpecificMatch(node.children, needle, depth + 1);
    if (below) consider(below);
  }
  return best;
}

const STATE_KEYS = {
  editable: "tree.state.editable",
  locked: "tree.state.locked",
  derived: "tree.state.derived",
  missing: "tree.state.missing",
} as const;

/** The catalog's state words, translated when the table knows them and shown as they are otherwise. */
function stateLabel(state: string, t: TFunction): string {
  const key = (STATE_KEYS as Record<string, (typeof STATE_KEYS)[keyof typeof STATE_KEYS] | undefined>)[state];
  return key ? t(key) : state;
}

function catalogMissing(node: Node): boolean {
  return node.objectCount > 0 && node.descendantElementIds.length === 0;
}

export function ComponentTree({
  projection,
  onPick,
  onClose,
  selectedComponentId = null,
  selectedElementId = null,
}: {
  projection: StateProjectionDto;
  onPick(componentId: string, elementId: string | null): void;
  onClose(): void;
  selectedComponentId?: string | null;
  selectedElementId?: string | null;
}) {
  const t = useT();
  const [query, setQuery] = useState("");
  const [folded, setFolded] = useState<Set<string>>(() => new Set());
  const roots = useMemo(() => catalogNodes(projection) ?? flatNodes(projection, t), [projection, t]);
  const producerOf = useMemo(() => {
    const map = new Map<string, string>();
    for (const element of projection.elements) map.set(element.elementId, element.producer);
    return map;
  }, [projection]);
  const needle = query.trim().toLowerCase();
  const kept = useMemo(() => {
    const keep = new Set<string>();
    if (needle) matching(roots, needle, keep);
    return keep;
  }, [roots, needle]);
  const enterMatch = useMemo(
    () =>
      needle
        ? mostSpecificMatch(roots, needle)
        : roots[0]
          ? { componentId: roots[0].id, elementId: null, score: 0 }
          : null,
    [roots, needle],
  );

  const toggle = (id: string) => {
    setFolded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const renderNode = (node: Node, depth: number): ReactElement | null => {
    if (needle && !kept.has(node.id)) return null;
    const isFolded = folded.has(node.id) && !needle;
    const missing = catalogMissing(node);
    const selected = selectedComponentId === node.id && selectedElementId === null;
    const hasBelow = node.children.length > 0 || node.elementIds.length > 0;
    const nodeMatches = node.id.toLowerCase().includes(needle);
    return (
      <li key={`c:${node.id}`} className="tree__item">
        <div className={`tree__row${selected ? " tree__row--selected" : ""}`} style={{ paddingLeft: `${depth * 14}px` }}>
          <button
            type="button"
            className="tree__fold"
            aria-label={isFolded ? t("tree.unfold") : t("tree.fold")}
            disabled={!hasBelow}
            onClick={() => toggle(node.id)}
          >
            {hasBelow ? (isFolded ? "▸" : "▾") : "·"}
          </button>
          <button
            type="button"
            className={`tree__name${missing ? " tree__name--missing" : ""}`}
            onClick={() => onPick(node.id, null)}
            title={missing ? t("tree.catalogMissing") : undefined}
          >
            <span className="mono">{node.id}</span>
          </button>
          <span className="tree__meta">
            {node.descendantElementIds.length > 0 && (
              <span className="tree__chip">{t("tree.elements", { n: node.descendantElementIds.length })}</span>
            )}
            {node.capabilityCount > 0 && (
              <span className="tree__chip">{t("tree.capabilities", { n: node.capabilityCount })}</span>
            )}
            {node.objectCount > 0 && (
              <span className={`tree__chip${node.unboundObjectCount > 0 ? " tree__chip--warn" : ""}`}>
                {node.unboundObjectCount > 0
                  ? t("tree.objectsUnbound", { n: node.objectCount, u: node.unboundObjectCount })
                  : t("tree.objects", { n: node.objectCount })}
              </span>
            )}
            {node.states.map((state) => (
              <span key={state} className={`tree__chip tree__chip--${state}`}>
                {stateLabel(state, t)}
              </span>
            ))}
            {missing && <span className="tree__chip tree__chip--missing">{t("tree.catalogMissingShort")}</span>}
          </span>
        </div>
        {!isFolded && (
          <ul className="tree__children">
            {node.elementIds
              .filter((id) => !needle || nodeMatches || id.toLowerCase().includes(needle))
              .map((id) => (
                <li key={`e:${id}`} className="tree__item">
                  <div
                    className={`tree__row tree__row--element${selectedElementId === id ? " tree__row--selected" : ""}`}
                    style={{ paddingLeft: `${(depth + 1) * 14}px` }}
                  >
                    <span className="tree__fold" aria-hidden="true">
                      ·
                    </span>
                    <button type="button" className="tree__name" onClick={() => onPick(node.id, id)}>
                      <span className="mono">{id}</span>
                    </button>
                    <span className="tree__meta">
                      <span className="tree__chip">{t("selection.element")}</span>
                      {producerOf.get(id) && <span className="tree__chip">{producerOf.get(id)}</span>}
                    </span>
                  </div>
                </li>
              ))}
            {node.children.map((child) => renderNode(child, depth + 1))}
          </ul>
        )}
      </li>
    );
  };

  return (
    <div className="picker tree" role="dialog" aria-label={t("selection.dialog.ariaLabel")}>
      <input
        className="picker__input"
        type="text"
        autoFocus
        placeholder={t("selection.placeholder")}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
          if (event.key === "Enter" && enterMatch) {
            onPick(enterMatch.componentId, enterMatch.elementId);
          }
        }}
      />
      {projection.componentTreeError && (
        <p className="picker__note">
          <BilingualText source={projection.componentTreeError} showSourceToggle />
        </p>
      )}
      {!projection.catalog && <p className="picker__note">{t("tree.noCatalog")}</p>}
      <ul className="picker__list tree__list">
        {roots.length === 0 || (needle && kept.size === 0) ? (
          <li className="picker__empty">{t("selection.empty")}</li>
        ) : (
          roots.map((node) => renderNode(node, 0))
        )}
      </ul>
    </div>
  );
}
