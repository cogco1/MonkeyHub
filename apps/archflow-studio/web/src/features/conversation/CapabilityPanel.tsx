/**
 * What the selected thing can be asked, read from the catalog and nothing else.
 *
 * The tree says a component exists and how much sits under it; it does not say
 * that `height` is a field, that it is 9.798, that it may be moved, or that it
 * is derived from a level. Without that an architect types "set height to 2"
 * into the dark and reads the grammar's question as a refusal. This panel is
 * the directory: one row per capability the catalog derived, with its value,
 * its status word, where it came from, and — when the row is editable — a
 * button that puts the grammar's own sentence in the box, so the sentence sent
 * is a sentence the server already agreed exists.
 *
 * It reads `projection.catalog` and the selection; it holds no selection of its
 * own and calls nothing. `derived` and `locked` rows offer no edit: the panel
 * never invites a change the record would refuse, and never recommends a
 * neighbouring field in place of a missing one — a component the model shows
 * with no row under it gets the catalog-missing sentence and a button that
 * asks for the control to be authored, which is what the clarification seam
 * reads as `declare_missing_control`.
 */

import { useMemo, useState } from "react";

import type { CapabilityDto, StateProjectionDto } from "../../api/generated";
import { useT, type TFunction } from "../../i18n/useT";
import type { Selection } from "./Composer";

/** At most this many rows for a whole component before the panel says "and N more". */
const ROW_LIMIT = 12;

const EDITABLE = "editable";

/** The catalog's own status words, from the table the tree already uses. */
const STATE_KEYS = {
  editable: "tree.state.editable",
  locked: "tree.state.locked",
  derived: "tree.state.derived",
  missing: "tree.state.missing",
} as const;

function statusLabel(status: string, t: TFunction): string {
  const key = (STATE_KEYS as Record<string, (typeof STATE_KEYS)[keyof typeof STATE_KEYS] | undefined>)[status];
  return key ? t(key) : status;
}

/**
 * Where a value came from, as the DTO's `source` says it. A source that names
 * its reference (`derived:level-2`, `derived from level-2`) is printed with the
 * reference; a bare word is translated when the table knows it and shown as it
 * is otherwise. Nothing here invents a provenance the server did not send.
 */
function sourceLabel(source: string, t: TFunction): string {
  const bare = source.trim();
  if (bare === "authored") return t("capability.source.authored");
  if (bare === "derived") return t("capability.source.derived");
  if (bare === "reindexed") return t("capability.source.reindexed");
  const match = /^derived(?::|\s+from\s+|\s+)(.+)$/i.exec(bare);
  if (match) return t("capability.source.derivedFrom", { ref: match[1].trim() });
  return bare;
}

/** The shell's one way of shortening a server number for display. */
function num(value: number): string {
  return String(Number(value.toFixed(6)));
}

function isEditable(capability: CapabilityDto): boolean {
  return capability.status === EDITABLE;
}

function sameSelection(a: Selection, b: Selection): boolean {
  return a.componentId === b.componentId && a.elementId === b.elementId;
}

interface Group {
  readonly elementId: string;
  readonly capabilities: readonly CapabilityDto[];
}

type View =
  | { kind: "noCatalog" }
  | { kind: "catalogMissing"; componentId: string }
  | { kind: "empty"; scope: "element" | "component" }
  | { kind: "rows"; groups: Group[]; headings: boolean; hidden: number; total: number };

/** The rows a whole component shows, cut to ROW_LIMIT with the rest counted. */
function limited(groups: Group[]): { groups: Group[]; hidden: number; total: number } {
  const total = groups.reduce((sum, group) => sum + group.capabilities.length, 0);
  const shown: Group[] = [];
  let budget = ROW_LIMIT;
  for (const group of groups) {
    if (budget <= 0) break;
    const take = group.capabilities.slice(0, budget);
    budget -= take.length;
    shown.push({ elementId: group.elementId, capabilities: take });
  }
  const kept = shown.reduce((sum, group) => sum + group.capabilities.length, 0);
  return { groups: shown, hidden: total - kept, total };
}

function viewOf(projection: StateProjectionDto, selection: Selection): View {
  const catalog = projection.catalog ?? null;
  if (!catalog) return { kind: "noCatalog" };

  if (selection.elementId !== null) {
    const element = catalog.elements.find((row) => row.elementId === selection.elementId);
    if (!element || element.capabilities.length === 0) {
      return { kind: "empty", scope: "element" };
    }
    return {
      kind: "rows",
      groups: [{ elementId: element.elementId, capabilities: element.capabilities }],
      headings: false,
      hidden: 0,
      total: element.capabilities.length,
    };
  }

  const component = catalog.components.find((row) => row.componentId === selection.componentId);
  if (component && component.objectCount > 0 && component.descendantElementIds.length === 0) {
    return { kind: "catalogMissing", componentId: component.componentId };
  }
  if (!component) return { kind: "empty", scope: "component" };

  const under = new Set(component.descendantElementIds);
  const groups = catalog.elements
    .filter((row) => under.has(row.elementId) && row.capabilities.length > 0)
    .map((row) => ({ elementId: row.elementId, capabilities: row.capabilities }));
  if (groups.length === 0) return { kind: "empty", scope: "component" };
  // What can be moved comes first; the record's own order is kept inside a row.
  groups.sort((a, b) => {
    const rank = Number(!a.capabilities.some(isEditable)) - Number(!b.capabilities.some(isEditable));
    return rank !== 0 ? rank : a.elementId.localeCompare(b.elementId);
  });
  const cut = limited(groups);
  return { kind: "rows", groups: cut.groups, headings: true, hidden: cut.hidden, total: cut.total };
}

function CapabilityRow({
  capability,
  t,
  onPrefill,
}: {
  capability: CapabilityDto;
  t: TFunction;
  onPrefill(sentence: string): void;
}) {
  const value = num(capability.value);
  return (
    <li className="capability__row">
      <span className="mono capability__key">{capability.key}</span>
      <span className="capability__value">
        {value}
        {capability.unit ? ` ${capability.unit}` : ""}
      </span>
      <span
        className={`tree__chip tree__chip--${capability.status}`}
        title={capability.status === "locked" ? t("capability.lockedHint") : undefined}
      >
        {statusLabel(capability.status, t)}
      </span>
      <span className="capability__source">{sourceLabel(capability.source, t)}</span>
      {capability.confidence < 1 && (
        <span className="tree__chip">
          {t("capability.confidence", { value: num(capability.confidence) })}
        </span>
      )}
      {capability.bounds && (
        <span className="tree__chip">
          {t("capability.bounds", {
            min: num(capability.bounds[0]),
            max: num(capability.bounds[1]),
          })}
        </span>
      )}
      {capability.validatorRefs.length > 0 && (
        <span className="tree__chip">
          {capability.validatorRefs.length === 1
            ? t("capability.validatorsOne")
            : t("capability.validators", { n: capability.validatorRefs.length })}
        </span>
      )}
      {isEditable(capability) && (
        <button
          type="button"
          className="btn btn--link capability__set"
          aria-label={t("capability.setAria", { key: capability.key, value })}
          onClick={() => onPrefill(`set ${capability.key} to ${value}`)}
        >
          {t("capability.set")}
        </button>
      )}
    </li>
  );
}

export function CapabilityPanel({
  projection,
  selection,
  onPrefill,
}: {
  projection: StateProjectionDto;
  selection: Selection;
  /** Put a sentence in the composer's box; the architect still sends it. */
  onPrefill(sentence: string): void;
}) {
  const t = useT();
  const view = useMemo(() => viewOf(projection, selection), [projection, selection]);
  // Open by default while an element is selected, and open again whenever the
  // selection moves; a fold the architect made on this selection is kept.
  const [fold, setFold] = useState<{ at: Selection; open: boolean }>(() => ({
    at: selection,
    open: selection.elementId !== null,
  }));
  const open = sameSelection(fold.at, selection) ? fold.open : selection.elementId !== null;

  return (
    <div className="capability" role="group" aria-label={t("capability.ariaLabel")}>
      <div className="capability__head">
        <span className="capability__title">{t("capability.title")}</span>
        <span className="mono capability__subject">
          {selection.elementId ?? selection.componentId}
        </span>
        {view.kind === "rows" && (
          <span className="tree__chip">{t("tree.capabilities", { n: view.total })}</span>
        )}
        <button
          type="button"
          className="btn btn--link capability__toggle"
          aria-expanded={open}
          onClick={() => setFold({ at: selection, open: !open })}
        >
          {open ? t("capability.hide") : t("capability.show")}
        </button>
      </div>
      {open && view.kind === "noCatalog" && (
        <p className="capability__note">{t("capability.noCatalog")}</p>
      )}
      {open && view.kind === "empty" && (
        <p className="capability__note">
          {view.scope === "element" ? t("capability.noneElement") : t("capability.noneComponent")}
        </p>
      )}
      {open && view.kind === "catalogMissing" && (
        <>
          <p className="capability__note">{t("capability.catalogMissing")}</p>
          <div className="capability__actions">
            <button
              type="button"
              className="btn btn--link"
              onClick={() => onPrefill(`补充 ${view.componentId} 字段`)}
            >
              {t("capability.declareControl")}
            </button>
          </div>
        </>
      )}
      {open && view.kind === "rows" && (
        <ul className="capability__list">
          {view.groups.map((group) => (
            <li key={group.elementId} className="capability__item">
              {view.headings && <span className="mono capability__groupName">{group.elementId}</span>}
              <ul className="capability__group">
                {group.capabilities.map((capability) => (
                  <CapabilityRow
                    key={capability.capabilityId}
                    capability={capability}
                    t={t}
                    onPrefill={onPrefill}
                  />
                ))}
              </ul>
            </li>
          ))}
          {view.hidden > 0 && (
            <li className="capability__more">{t("capability.andMore", { n: view.hidden })}</li>
          )}
        </ul>
      )}
    </div>
  );
}
