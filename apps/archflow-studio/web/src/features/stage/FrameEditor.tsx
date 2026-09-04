/**
 * The frame: the levels and axes every element in this record is placed
 * against, and what changing one of them would move.
 *
 * No element in an ArchFlow record carries a coordinate — it names a `Level@1`
 * or a `GridAxis@1` — so one row here can move a whole side of a building. The
 * "what would move" toggle is that closure, as the server already computed it
 * and sent with the frame; opening it asks nothing.
 *
 * The prefill button writes a sentence into the composer and stops there. The
 * grammar has no rule for a level or an axis — they are not `Element@1`
 * params — so what comes back is the grammar's own refusal. The tooltip says
 * so first; nothing here pretends the sentence will be accepted, and nothing
 * here adds a rule that would make it so.
 */

import { useState } from "react";

import type {
  FrameAxisDto,
  FrameDto,
  FrameLevelDto,
  StateProjectionDto,
} from "../../api/generated";
import { ErrorPanel } from "../../app/ErrorPanel";
import type { Loadable } from "../../app/loadable";
import { BilingualText } from "../../i18n/BilingualText";
import { useT, type TFunction } from "../../i18n/useT";

const ENTITY = "entity:";

/** The sentence the composer is prefilled with, per the brief's exact wording. */
export function levelSentence(level: FrameLevelDto): string {
  return `set ${level.levelId} elevation to ${level.elevation}`;
}

export function axisSentence(axis: FrameAxisDto): string {
  return `set ${axis.axisId} position to ${axis.value}`;
}

/** The element ids in a closure, in the order the server sent them. */
function elementsIn(
  closure: readonly string[],
  componentOf: ReadonlyMap<string, string>,
): string[] {
  return closure
    .filter((ref) => ref.startsWith(ENTITY))
    .map((ref) => ref.slice(ENTITY.length))
    .filter((id) => componentOf.has(id));
}

function Closure({
  closure,
  componentOf,
  onPick,
  t,
}: {
  closure: readonly string[];
  componentOf: ReadonlyMap<string, string>;
  onPick(componentId: string, elementId: string | null): void;
  t: TFunction;
}) {
  if (closure.length === 0) {
    return <p className="frame__closure frame__closure--empty">{t("frame.closureEmpty")}</p>;
  }
  return (
    <ul className="frame__closure">
      {closure.map((ref) => {
        const elementId = ref.startsWith(ENTITY) ? ref.slice(ENTITY.length) : null;
        const componentId = elementId === null ? undefined : componentOf.get(elementId);
        return (
          <li key={ref}>
            {componentId !== undefined && elementId !== null ? (
              <button
                type="button"
                className="frame__ref mono"
                title={t("frame.selectTitle", { id: elementId })}
                onClick={() => onPick(componentId, elementId)}
              >
                {ref}
              </button>
            ) : (
              <span className="frame__ref frame__ref--plain mono">{ref}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function Row({
  id,
  role,
  value,
  unit,
  count,
  closure,
  sentence,
  componentOf,
  onPick,
  onPrefill,
  t,
}: {
  id: string;
  role: string;
  /** The number this datum stands at, or null when the record has none for it. */
  value: number | null;
  unit: string;
  count: number;
  closure: readonly string[];
  /** The sentence the prefill button writes, or null when there is no value. */
  sentence: string | null;
  componentOf: ReadonlyMap<string, string>;
  onPick(componentId: string, elementId: string | null): void;
  onPrefill(sentence: string): void;
  t: TFunction;
}) {
  const [open, setOpen] = useState(false);
  const moved = elementsIn(closure, componentOf).length;
  return (
    <li className="frame__item">
      <div className="frame__row">
        <span className="frame__role">{role}</span>
        <span className="frame__id mono">{id}</span>
        <span className="frame__value mono">
          {value === null ? t("frame.noValue") : `${value} ${unit}`}
        </span>
        <span className="frame__chip">{t("frame.elementsOn", { n: count })}</span>
        <button
          type="button"
          className="btn btn--link frame__toggle"
          aria-expanded={open}
          onClick={() => setOpen((current) => !current)}
        >
          {t(open ? "frame.hideMove" : "frame.whatMoves", { n: moved })}
        </button>
        <button
          type="button"
          className="btn btn--small frame__prefill"
          disabled={sentence === null}
          title={
            sentence === null ? t("frame.noValueTitle") : t("frame.notYetEditable")
          }
          onClick={() => sentence !== null && onPrefill(sentence)}
        >
          {t("frame.prefill")}
        </button>
      </div>
      {open && (
        <Closure closure={closure} componentOf={componentOf} onPick={onPick} t={t} />
      )}
    </li>
  );
}

export function FrameEditor({
  frame,
  projection,
  onPick,
  onPrefill,
  onClose,
}: {
  frame: Loadable<FrameDto>;
  /** Where an element id learns which component it belongs to; null before the projection loads. */
  projection: StateProjectionDto | null;
  onPick(componentId: string, elementId: string | null): void;
  onPrefill(sentence: string): void;
  onClose(): void;
}) {
  const t = useT();
  const componentOf = new Map<string, string>(
    (projection?.elements ?? []).map((element) => [
      element.elementId,
      element.componentId,
    ]),
  );
  return (
    <div className="frame" role="dialog" aria-label={t("frame.ariaLabel")}>
      <div className="frame__head">
        <span className="frame__title">{t("frame.title")}</span>
        <span className="quiet frame__note">{t("frame.subtitle")}</span>
        <button
          type="button"
          className="btn btn--link frame__close"
          onClick={onClose}
        >
          {t("common.close")}
        </button>
      </div>
      {frame.status === "loading" || frame.status === "idle" ? (
        <p className="frame__note quiet">{t("frame.loading")}</p>
      ) : frame.status === "failed" ? (
        <ErrorPanel error={frame.error} what="GET /api/state/frame" />
      ) : (
        <>
          <section className="frame__section">
            <h3 className="frame__heading">{t("frame.levels")}</h3>
            {frame.value.levels.length === 0 ? (
              <p className="frame__note quiet">{t("frame.noLevels")}</p>
            ) : (
              <ul className="frame__list">
                {frame.value.levels.map((level) => (
                  <Row
                    key={level.levelId}
                    id={level.levelId}
                    role={level.role}
                    value={level.elevation}
                    unit={t("frame.metres")}
                    count={level.elementsOn.length}
                    closure={level.closure}
                    sentence={levelSentence(level)}
                    componentOf={componentOf}
                    onPick={onPick}
                    onPrefill={onPrefill}
                    t={t}
                  />
                ))}
              </ul>
            )}
          </section>
          <section className="frame__section">
            <h3 className="frame__heading">{t("frame.axes")}</h3>
            {frame.value.axes.length === 0 ? (
              <p className="frame__note quiet">{t("frame.noAxes")}</p>
            ) : (
              <ul className="frame__list">
                {frame.value.axes.map((axis) => (
                  <Row
                    key={axis.axisId}
                    id={axis.axisId}
                    role={axis.role}
                    value={axis.value}
                    unit={
                      axis.const === null
                        ? t("frame.metres")
                        : t("frame.constant", { const: axis.const })
                    }
                    count={axis.elementsOn.length}
                    closure={axis.closure}
                    sentence={axis.value === null ? null : axisSentence(axis)}
                    componentOf={componentOf}
                    onPick={onPick}
                    onPrefill={onPrefill}
                    t={t}
                  />
                ))}
              </ul>
            )}
          </section>
          {frame.value.honesty.length > 0 && (
            <ul className="frame__honesty">
              {frame.value.honesty.map((line) => (
                <li key={line}>
                  <BilingualText source={line} showSourceToggle />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
