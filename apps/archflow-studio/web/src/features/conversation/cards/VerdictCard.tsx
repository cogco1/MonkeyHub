/**
 * The candidate's validation: the kernel's receipt, and the server's verdict.
 *
 * Three refusals are built into this card. It never says "validation passed":
 * it shows the receipt's own `passed` and every finding, beside
 * `effectiveChecks`, because a list of gates that ran is not a list of gates
 * that had anything to check. It never computes `advance`: the word is the
 * server's boolean and `blockedBy` names the clauses that refused. And it never
 * loses a state: the three-state chips are the candidate's own block, copied
 * through the validation DTO.
 *
 * The verdict is asked for only when the server says the job `succeeded`. A
 * job that finished by failing has no receipt to validate.
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
import { Verbatim } from "./Verbatim";

const NOT_FINISHED = "CANDIDATE_NOT_FINISHED";

/**
 * The five clause names the API documents on `blockedBy`, in the order the
 * server states them. A mirror, kept only to give the chips a fixed order; a
 * name in `blockedBy` that this list does not know is still shown, as refused.
 */
const CLAUSES: readonly string[] = [
  "validation.receipt",
  "runner.seat_execution_complete",
  "relations.held",
  "relations.fully_checked",
  "runner.exports_available",
];

export function VerdictCard({
  candidateId,
  onValidation,
  onEvidence,
}: {
  candidateId: string;
  onValidation(validation: ValidationDto): void;
  onEvidence(tab: EvidenceTab): void;
}) {
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
          <p className="quiet">reading the verdict…</p>
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
              the server will not validate a candidate that has not finished
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
  const clauses = [
    ...CLAUSES,
    ...value.blockedBy.filter((name) => !CLAUSES.includes(name)),
  ];
  return (
    <article className="card">
      <div className="card__row verdict">
        <p
          className={`card__word ${
            value.advance ? "card__word--go" : "card__word--no"
          }`}
        >
          {value.advance ? "May advance" : "Blocked"}
        </p>
        <p className="quiet">
          receipt {value.receipt.passed ? "passed" : "did not pass"} · effective:{" "}
          {value.effectiveChecks.length === 0
            ? "none"
            : value.effectiveChecks.join(", ")}
        </p>
      </div>
      <div className="card__row chips">
        {clauses.map((name) => (
          <span
            key={name}
            className={`pill ${refused.has(name) ? "pill--violated" : "pill--held"}`}
          >
            {name}
          </span>
        ))}
      </div>
      <div className="card__row">
        <RelationChips checks={value.relationChecks} />
      </div>
      {value.receipt.findings.length > 0 && (
        <div className="card__row">
          <ul className="findings">
            {value.receipt.findings.map((finding, index) => (
              <li key={`${finding.code}:${index}`}>
                <span className="mono">{finding.code}</span> ·{" "}
                {finding.severity} · {finding.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {value.honesty.length > 0 && (
        <div className="card__row">
          <Verbatim lines={value.honesty} />
        </div>
      )}
      <div className="card__row actions">
        <span className="quiet">
          the verdict is the server's, read once per candidate and HEAD
        </span>
        <button
          type="button"
          className="btn btn--link"
          onClick={() => onEvidence("receipts")}
        >
          Read the receipt
        </button>
      </div>
    </article>
  );
}
