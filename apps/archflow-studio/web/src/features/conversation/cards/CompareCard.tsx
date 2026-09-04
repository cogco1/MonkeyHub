/**
 * Before / After / Why: what a candidate's exports changed against another
 * run's, in the record's own counts.
 *
 * The sentence ArchFlow sells is on this card: "<component> · changed because
 * "<what was said>" · affected N · unchanged M". Every number is the
 * inspection records' join, never the page's estimate; the "why" is the
 * sentence the candidate was made from when this process still holds it, and
 * the card says when it does not.
 */

import type { CompareDto, CompareObjectDto } from "../../../api/generated";
import type { TFunction } from "../../../i18n/useT";
import { useT } from "../../../i18n/useT";

function affected(row: { changed: number; added: number; removed: number }): number {
  return row.changed + row.added + row.removed;
}

function topOf(box: { min: [number, number, number]; max: [number, number, number] } | null) {
  return box ? box.max[2] : null;
}

function objectLine(row: CompareObjectDto, t: TFunction) {
  const before = topOf(row.before);
  const after = topOf(row.after);
  if (row.status === "changed" && before !== null && after !== null) {
    const delta = after - before;
    if (Math.abs(delta) > 1e-6) {
      return (
        <>
          {t("compare.top")} {Number(before.toFixed(4))} → {Number(after.toFixed(4))} ({
            delta > 0 ? "+" : ""
          }
          {Number(delta.toFixed(4))})
        </>
      );
    }
    return t("compare.sameExtentDifferentGeometry");
  }
  return row.status;
}

export function CompareCard({
  comparison,
  onCompareInModel,
}: {
  comparison: CompareDto;
  /** Cross-fade the two exports in the viewer, when both can be shown. */
  onCompareInModel: (() => void) | null;
}) {
  const t = useT();
  const hasProposalWhy = comparison.whySource === "proposal" && comparison.why;
  const total = comparison.changed + comparison.unchanged + comparison.added + comparison.removed;
  const changedComponents = comparison.components.filter((row) => affected(row) > 0);
  const quietComponents = comparison.components.filter((row) => affected(row) === 0);
  return (
    <article className="card card--compare">
      <div className="card__row">
        <p className="label">{t("compare.title")}</p>
        <p className="card__title">
          {affected(comparison) === 0 ? (
            <>{t("compare.nothingChanged", { total })}</>
          ) : (
            <>{t("compare.changedSummary", {
              affected: affected(comparison),
              total,
              unchanged: comparison.unchanged,
            })}</>
          )}
        </p>
        <p className="quiet">
          {t("compare.against")} {" "}
          <span className="mono">{comparison.against}</span> · {" "}
          {t("compare.countedFromInspections")}
        </p>
      </div>
      {changedComponents.length > 0 && (
        <div className="card__row">
          <ul className="compare__list">
            {changedComponents.map((row) => (
              <li key={row.componentId} className="compare__component">
                <span className="mono compare__name">{row.componentId}</span> · {" "}
                {t("compare.changed")} {" "}
                {hasProposalWhy ? (
                  <>
                    {t("compare.because")} “<span>{comparison.why}</span>”
                  </>
                ) : (
                  t("compare.whyUnavailable")
                )}{" "}
                · {t("compare.affected")} {affected(row)} · {t("compare.unchanged")} {row.unchanged}
                {row.added > 0 && <> · {row.added} {t("compare.added")}</>}
                {row.removed > 0 && <> · {row.removed} {t("compare.removed")}</>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {quietComponents.length > 0 && (
        <div className="card__row">
          <p className="quiet">
            {t("compare.unchanged")}: {" "}
            <span className="mono">
              {quietComponents.map((row) => row.componentId).join(", ")}
            </span>
          </p>
        </div>
      )}
      <div className="card__row">
        <details className="compare__objects">
          <summary className="quiet">
            {t("compare.everyObject", { count: comparison.objects.length })}
          </summary>
          <ul className="reflist">
            {comparison.objects
              .filter((row) => row.status !== "unchanged")
              .map((row) => (
                <li key={row.name} className="mono" title={row.seatId}>
                  {row.name} · {row.status}
                  {row.status === "changed" && (
                    <span className="quiet"> · {objectLine(row, t)}</span>
                  )}
                </li>
              ))}
          </ul>
        </details>
      </div>
      <div className="card__row actions">
        {onCompareInModel && (
          <button type="button" className="btn" onClick={onCompareInModel}>
            {t("compare.inModel")}
          </button>
        )}
        <span className="quiet">
          {t("compare.toleranceBefore")} {comparison.tolerance} {" "}
          {t("compare.toleranceAfter")}
        </span>
      </div>
    </article>
  );
}
