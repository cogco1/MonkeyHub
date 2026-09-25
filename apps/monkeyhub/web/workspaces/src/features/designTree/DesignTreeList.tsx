/**
 * The same tree as a list: the view for the keyboard, screen readers and
 * narrow screens, since the canvas is a picture to assistive technology.
 * The current line first, from the root to Current; under each point, the
 * options not taken there and whatever a later Continue left behind.
 */
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useT } from "../../i18n/useT";
import type { GrowthTree } from "./model";
import type { TreeWords } from "./words";

interface Row { readonly id: string; readonly depth: number; readonly group: "trunk" | "twig" | "earlier" }

export function listRows(tree: GrowthTree): Row[] {
  const rows: Row[] = [];
  const visit = (id: string, depth: number, group: Row["group"]) => {
    rows.push({ id, depth, group });
    for (const child of tree.children.get(id) ?? []) if (!tree.onTrunk.has(child)) visit(child, depth + 1, "earlier");
  };
  for (const id of tree.trunk) {
    rows.push({ id, depth: 0, group: "trunk" });
    for (const child of tree.children.get(id) ?? []) if (!tree.onTrunk.has(child)) visit(child, 1, "twig");
  }
  return rows;
}

export function DesignTreeList({ tree, words, selected, onSelect }: {
  tree: GrowthTree;
  words: TreeWords;
  selected: string | null;
  onSelect(node: string): void;
}) {
  const t = useT();
  const rows = useMemo(() => listRows(tree), [tree]);
  const [focus, setFocus] = useState<string | null>(selected);
  const items = useRef(new Map<string, HTMLLIElement>());
  const active = rows.some((row) => row.id === focus) ? focus : rows[0]?.id ?? null;
  useEffect(() => { if (selected) setFocus(selected); }, [selected]);
  const move = (event: KeyboardEvent<HTMLLIElement>, id: string) => {
    const index = rows.findIndex((row) => row.id === id);
    const target = event.key === "ArrowDown" ? rows[index + 1] : event.key === "ArrowUp" ? rows[index - 1]
      : event.key === "Home" ? rows[0] : event.key === "End" ? rows.at(-1) : undefined;
    if (target) {
      event.preventDefault();
      setFocus(target.id);
      items.current.get(target.id)?.focus();
      return;
    }
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(id); }
  };
  const group = { trunk: t("designTree.list.trunk"), twig: t("designTree.list.twig"), earlier: t("designTree.list.earlier") };
  return <div className="design-tree-list">
    <p className="design-tree-list__hint">{t("designTree.list.hint")}</p>
    <ul role="tree" aria-label={t("designTree.list.label")}>
      {rows.map((row) => {
        const node = tree.nodes.get(row.id)!;
        const review = node.candidate?.blockedBy.length ? t("designTree.review.short", { count: node.candidate.blockedBy.length }) : null;
        const status = [words.status(node), review].filter(Boolean).join(" · ");
        return <li key={row.id} role="treeitem" aria-level={row.depth + 1} aria-selected={selected === row.id} tabIndex={active === row.id ? 0 : -1}
          ref={(element) => { if (element) items.current.set(row.id, element); else items.current.delete(row.id); }}
          data-node={row.id} data-group={row.group} data-kind={node.kind} style={{ paddingInlineStart: `${10 + row.depth * 22}px` }}
          onClick={() => { setFocus(row.id); onSelect(row.id); }} onKeyDown={(event) => move(event, row.id)}>
          <span className="design-tree-list__mark" data-kind={node.kind} aria-hidden="true">
            {node.kind === "stage" ? `S${node.stage!.number}` : node.kind === "candidate" ? node.letter ?? "·" : node.kind === "current" ? "●" : node.kind === "pending" ? "…" : "○"}
          </span>
          <span className="design-tree-list__title">{words.title(node)}</span>
          <span className="design-tree-list__status">{[group[row.group], status].filter(Boolean).join(" · ")}</span>
        </li>;
      })}
    </ul>
  </div>;
}
