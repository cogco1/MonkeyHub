/**
 * The component tree, folded into a picker.
 *
 * Choosing here sets the selection from the projection's own ids — no server
 * call, because these are the server's names already. When the kernel refused
 * to build the tree, the components are listed as the elements name them and
 * the server's sentence about the tree is shown above the list.
 */

import { useMemo, useState } from "react";

import type { StateProjectionDto } from "../../api/generated";
import { BilingualText } from "../../i18n/BilingualText";
import { useT, type TFunction } from "../../i18n/useT";

interface Row {
  key: string;
  componentId: string;
  elementId: string | null;
  primary: string;
  secondary: string;
}

function rowsOf(projection: StateProjectionDto, t: TFunction): Row[] {
  const rows: Row[] = [];
  const elementsByComponent = new Map<string, typeof projection.elements>();
  for (const element of projection.elements) {
    const list = elementsByComponent.get(element.componentId) ?? [];
    list.push(element);
    elementsByComponent.set(element.componentId, list);
  }
  const components =
    projection.componentTree?.map((node) => ({
      id: node.componentId,
      secondary: `${node.semanticKind} · ${node.maturity} · r${node.revision}`,
    })) ??
    [...elementsByComponent.keys()].map((id) => ({
      id,
      secondary: t("selection.componentNamedByElements"),
    }));
  for (const component of components) {
    rows.push({
      key: `c:${component.id}`,
      componentId: component.id,
      elementId: null,
      primary: component.id,
      secondary: component.secondary,
    });
    for (const element of elementsByComponent.get(component.id) ?? []) {
      rows.push({
        key: `e:${element.elementId}`,
        componentId: component.id,
        elementId: element.elementId,
        primary: element.elementId,
        secondary: `${t("selection.element")} · ${element.producer}`,
      });
    }
  }
  return rows;
}

export function SelectionPicker({
  projection,
  onPick,
  onClose,
}: {
  projection: StateProjectionDto;
  onPick(componentId: string, elementId: string | null): void;
  onClose(): void;
}) {
  const t = useT();
  const [query, setQuery] = useState("");
  const rows = useMemo(() => rowsOf(projection, t), [projection, t]);
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? rows.filter(
        (row) =>
          row.primary.toLowerCase().includes(needle) ||
          row.secondary.toLowerCase().includes(needle),
      )
    : rows;

  return (
    <div className="picker" role="dialog" aria-label={t("selection.dialog.ariaLabel")}>
      <input
        className="picker__input"
        type="text"
        autoFocus
        placeholder={t("selection.placeholder")}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
          if (event.key === "Enter" && shown.length > 0) {
            const first = shown[0];
            onPick(first.componentId, first.elementId);
          }
        }}
      />
      {projection.componentTreeError && (
        <p className="picker__note">
          <BilingualText source={projection.componentTreeError} showSourceToggle />
        </p>
      )}
      <ul className="picker__list">
        {shown.length === 0 ? (
          <li className="picker__empty">{t("selection.empty")}</li>
        ) : (
          shown.map((row) => (
            <li key={row.key}>
              <button
                type="button"
                className={`picker__row${row.elementId ? " picker__row--element" : ""}`}
                onClick={() => onPick(row.componentId, row.elementId)}
              >
                <span className="mono">{row.primary}</span>
                <span className="picker__meta">{row.secondary}</span>
              </button>
            </li>
          ))
        )}
      </ul>
    </div>
  );
}
