/**
 * Is it safe? The candidate's validation, in design-review language.
 *
 * Three refusals are built into this card. It never says "validation passed":
 * it shows the receipt's own `passed` and every finding. It never computes
 * `advance`: the word is the server's boolean and `blockedBy` names the
 * clauses that refused — each review line below is a clause or two of the
 * server's verdict, named underneath in the server's own words. And it never
 * loses a state: the three-state chips are the candidate's own block, copied
 * through the validation DTO.
 *
 * The verdict is asked for only when the server says the job `succeeded`.
 */

import { useEffect, useState } from "react";

import { asStudioApiError, studio } from "../../../api/client";
import type { ValidationDto } from "../../../api/generated";
import { ErrorPanel } from "../../../app/ErrorPanel";
import type { EvidenceTab } from "../../../app/evidence";
import { RelationChips } from "../../../app/RelationChips";
import {
  failed,
  idle,
  loading,
  ready,
  type Loadable,
} from "../../../app/loadable";
import { BilingualText } from "../../../i18n/BilingualText";
import { useT } from "../../../i18n/useT";
import { Verbatim } from "./Verbatim";

const NOT_FINISHED = "CANDIDATE_NOT_FINISHED";

/**
 * The review lines, each standing on the clause names the server documents on
 * `blockedBy`. A name in `blockedBy` that no line knows is still shown, under
 * Unresolved — never dropped.
 */
const REVIEW = [
  {
    id: "geometry",
    titleKey: "verdict.review.geometry",
    clauses: ["runner.seat_execution_complete", "runner.exports_available"],
  },
  {
    id: "dependencies",
    titleKey: "verdict.review.dependencies",
    clauses: ["relations.held", "relations.fully_checked"],
  },
  {
    id: "receipt",
    titleKey: "verdict.review.receipt",
    clauses: ["validation.receipt"],
  },
] as const;

export function VerdictCard({
  candidateId,
  protectedRefs,
  onValidation,
  onEvidence,
}: {
  candidateId: string;
  /** What the sentence asked to keep; the proposal's refs, verbatim. */
  protectedRefs: readonly string[];
  onValidation(validation: ValidationDto): void;
  onEvidence(tab: EvidenceTab, candidateId?: string): void;
}) {
  const t = useT();
  const [validation, setValidation] = useState<Loadable<ValidationDto>>(idle);

  useEffect(() => {
    let cancelled = false;
    setValidation(loading);
    void (async () => {
      try {
        const value = await studio.validation(candidateId);
        if (cancelled) return;
        setValidation(ready(value));
        onValidation(value);
      } catch (cause) {
        if (!cancelled) setValidation(failed(asStudioApiError(cause)));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [candidateId, onValidation]);

  if (validation.status === "loading" || validation.status === "idle") {
    return (
      <article className="card">
        <div className="card__row">
          <p className="quiet">{t("verdict.checking")}</p>
        </div>
      </article>
    );
  }
  if (validation.status === "failed") {
    return (
      <article className="card card--refusal">
        <div className="card__row">
          {validation.error.code === NOT_FINISHED && (
            <p className="quiet">
              {t("verdict.notFinished")}
            </p>
          )}
          <ErrorPanel
            error={validation.error}
            what={`GET /api/candidates/${candidateId}/validation`}
          />
        </div>
      </article>
    );
  }

  const value = validation.value;
  const refused = new Set(value.blockedBy);
  const known = new Set<string>(REVIEW.flatMap((line) => line.clauses));
  const unresolved = value.blockedBy.filter((name) => !known.has(name));
  return (
    <article className="card card--verdict">
      <div className="card__row verdict">
        <p className="label">{t("verdict.safeQuestion")}</p>
        <p
          className={`card__word ${
            value.advance ? "card__word--go" : "card__word--no"
          }`}
        >
          {value.advance ? t("verdict.mayAdvance") : t("verdict.blocked")}
        </p>
      </div>
      <div className="card__row">
        <dl className="kv kv--review">
          {REVIEW.map((line) => {
            const refusing = line.clauses.filter((name) => refused.has(name));
            const ok = refusing.length === 0;
            return (
              <ReviewLine
                key={line.id}
                title={t(line.titleKey)}
                ok={ok}
                clauses={line.clauses}
                refusing={refusing}
              >
                {line.id === "dependencies" && (
                  <RelationChips checks={value.relationChecks} />
                )}
                {line.id === "receipt" && (
                  <span className="quiet">
                    {value.receipt.passed
                      ? t("verdict.passed")
                      : t("verdict.didNotPass")} · {t("verdict.effective")}: {" "}
                    {value.effectiveChecks.length === 0
                      ? t("evidence.common.none")
                      : value.effectiveChecks.join(", ")}
                  </span>
                )}
              </ReviewLine>
            );
          })}
          <dt>{t("verdict.protected")}</dt>
          <dd>
            {protectedRefs.length === 0 ? (
              <span className="quiet">{t("verdict.nothingProtected")}</span>
            ) : (
              <>
                <ul className="reflist">
                  {protectedRefs.map((ref) => (
                    <li key={ref} className="mono" title={ref}>
                      {ref.includes(":") ? ref.slice(ref.indexOf(":") + 1) : ref}
                    </li>
                  ))}
                </ul>
                <span className="quiet">
                  {t("verdict.protectionExplanation")}
                </span>
              </>
            )}
          </dd>
          <dt>{t("verdict.unresolved")}</dt>
          <dd>
            {value.blockedBy.length === 0 &&
            value.receipt.findings.length === 0 &&
            value.honesty.length === 0 ? (
              <span className="quiet">{t("verdict.nothingUnresolved")}</span>
            ) : (
              <>
                {unresolved.length > 0 && (
                  <p className="mono">{unresolved.join(" · ")}</p>
                )}
                {value.receipt.findings.length > 0 && (
                  <ul className="findings">
                    {value.receipt.findings.map((finding, index) => (
                      <li key={`${finding.code}:${index}`}>
                        <span className="mono">{finding.code}</span> ·{" "}
                        <span className="mono">{finding.severity}</span> · {" "}
                        <BilingualText source={finding.message} />
                      </li>
                    ))}
                  </ul>
                )}
                <Verbatim lines={value.honesty} />
                {value.blockedBy.length > 0 && unresolved.length === 0 && (
                  <p className="quiet">
                    {t("verdict.refused")}: {" "}
                    <span className="mono">{value.blockedBy.join(" · ")}</span>
                  </p>
                )}
              </>
            )}
          </dd>
        </dl>
      </div>
      <div className="card__row actions">
        <span className="quiet">
          {t("verdict.serverSource")}
        </span>
        <button
          type="button"
          className="btn btn--link"
          onClick={() => onEvidence("receipts", candidateId)}
        >
          {t("verdict.readReceipt")}
        </button>
      </div>
    </article>
  );
}

function ReviewLine({
  title,
  ok,
  clauses,
  refusing,
  children,
}: {
  title: string;
  ok: boolean;
  clauses: readonly string[];
  refusing: readonly string[];
  children?: React.ReactNode;
}) {
  const t = useT();
  return (
    <>
      <dt>{title}</dt>
      <dd>
        <span className={`mark ${ok ? "mark--ok" : "mark--no"}`}>
          {ok ? "✓" : "△"}
        </span>{" "}
        {ok ? (
          t("verdict.held")
        ) : (
          <>
            {t("verdict.refused")}: {" "}
            <span className="mono">{refusing.join(", ")}</span>
          </>
        )}
        <span className="quiet mono review__clauses"> {clauses.join(" · ")}</span>
        {children && <div className="review__detail">{children}</div>}
      </dd>
    </>
  );
}
