/**
 * The kernel's component tree, indented by the parents the server named.
 *
 * The order is the server's order and the depth is walked from
 * `parentComponentId` — the client arranges rows, it does not decide what is a
 * child of what. When the tree could not be built the API says so in
 * `componentTreeError`, and that sentence is what this panel shows: a record
 * whose tree will not resolve is not a project with no components.
 */

import type { ComponentNodeDto, StateProjectionDto } from "../../api/generated";

function depthOf(
  node: ComponentNodeDto,
  byId: Map<string, ComponentNodeDto>,
): number {
  let depth = 0;
  let parent = node.parentComponentId;
  const seen = new Set<string>([node.componentId]);
  while (parent && !seen.has(parent)) {
    seen.add(parent);
    depth += 1;
    parent = byId.get(parent)?.parentComponentId ?? null;
  }
  return depth;
}

export function ComponentTree({
  projection,
  selectedComponentId,
  onSelect,
}: {
  projection: StateProjectionDto;
  selectedComponentId: string | null;
  onSelect(componentId: string): void;
}) {
  const tree = projection.componentTree;
  if (tree === null || tree === undefined) {
    return (
      <div className="panel__note panel__note--refused">
        <p>the kernel could not build this record's component tree.</p>
        <p className="mono">
          {projection.componentTreeError ??
            "the server named no reason, which is itself the answer it gave"}
        </p>
      </div>
    );
  }
  const byId = new Map(tree.map((node) => [node.componentId, node]));
  return (
    <ul className="tree">
      {tree.map((node) => (
        <li key={node.componentId}>
          <button
            type="button"
            className={`tree__row${
              node.componentId === selectedComponentId ? " is-selected" : ""
            }`}
            style={{ paddingLeft: `${0.4 + depthOf(node, byId) * 0.9}rem` }}
            onClick={() => onSelect(node.componentId)}
          >
            <span className="tree__id">{node.componentId}</span>
            <span className="tree__kind">{node.semanticKind}</span>
            <span className="tree__meta">
              {node.maturity} · r{node.revision}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
