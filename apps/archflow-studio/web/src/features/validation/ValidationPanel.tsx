/**
 * The candidate's validation: the kernel's receipt, and the server's verdict.
 *
 * Three refusals are built into this panel.
 *
 * It never says "validation passed". It shows the receipt's own `passed`, every
 * finding, and — beside them — `validators`, `effectiveChecks`, `validatorNote`
 * and `canonicalFacts`, because a list of gates that ran is not a list of gates
 * that had anything to check.
 *
 * It never computes `advance`. The badge is the server's boolean and `blockedBy`
 * names the clauses that refused.
 *
 * And it never loses a state: the three-state chips are the candidate's own
 * block, copied through the validation DTO, so nothing unchecked is rounded up
 * to held on the way here.
 */

import { useEffect, useState } from "react";

import { asStudioApiError, studio } from "../../api/client";
import { ErrorPanel } from "../../app/ErrorPanel";
import { RelationChips } from "../../app/RelationChips";
import {
  failed,
  idle,
  loading,
  ready,
  type Loadable,
} from "../../app/loadable";
import type { ValidationDto } from "../../api/generated";
import { HonestyLines } from "../state/HonestyLines";
import { sha8 } from "../project/TopBar";

const NOT_FINISHED = "CANDIDATE_NOT_FINISHED";

export function ValidationPanel({
  candidateId,
  jobFinished,
}: {
  candidateId: string | null;
  jobFinished: boolean;
}) {
  const [validation, setValidation] = useState<Loadable<ValidationDto>>(idle);

  useEffect(() => {
    if (candidateId === null || !jobFinished) {
      setValidation(idle);
      return undefined;
    }
    let cancelled = false;
    setValidation(loading);
    void (async () => {
      try {
        const value = await studio.validation(candidateId);
        if (!cancelled) setValidation(ready(value));
      } catch (cause) {
        if (!cancelled) setValidation(failed(asStudioApiError(cause)));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [candidateId, jobFinished]);

  if (candidateId === null) {
    return (
      <p className="panel__note">
        no candidate to validate. A verdict is about a run, not about a
        proposal.
      </p>
    );
  }
  if (!jobFinished) {
    return (
      <p className="panel__note">
        the candidate is still running. The verdict is read once the job is
        over.
      </p>
    );
  }
  if (validation.status === "loading" || validation.status === "idle") {
    return <p className="panel__note">reading the validation…</p>;
  }
  if (validation.status === "failed") {
    if (validation.error.code === NOT_FINISHED) {
      return (
        <div>
          <p className="panel__note">
            still running — the server will not validate a candidate that has
            not finished. Watch the job, not the validation.
          </p>
          <ErrorPanel
            error={validation.error}
            what={`GET /api/candidates/${candidateId}/validation`}
          />
        </div>
      );
    }
    return (
      <ErrorPanel
        error={validation.error}
        what={`GET /api/candidates/${candidateId}/validation`}
      />
    );
  }

  const value = validation.value;
  return (
    <div className="validation">
      <div className="chips">
        <span
          className={`chip ${value.advance ? "chip--held" : "chip--violated"}`}
        >
          advance {String(value.advance)}
        </span>
        <span
          className={`chip ${
            value.receipt.passed ? "chip--held" : "chip--violated"
          }`}
        >
          receipt.passed {String(value.receipt.passed)}
        </span>
        <span
          className={`chip ${
            value.seatExecutionComplete ? "chip--held" : "chip--unchecked"
          }`}
        >
          seatExecutionComplete {String(value.seatExecutionComplete)}
        </span>
      </div>
      <RelationChips checks={value.relationChecks} />

      <p className="panel__subhead">blockedBy ({value.blockedBy.length})</p>
      {value.blockedBy.length === 0 ? (
        <p className="panel__note">the server named no refusing clause</p>
      ) : (
        <ul className="refs mono">
          {value.blockedBy.map((clause) => (
            <li key={clause} className="is-conflict">
              {clause}
            </li>
          ))}
        </ul>
      )}

      <p className="panel__subhead">
        receipt findings ({value.receipt.findings.length})
      </p>
      {value.receipt.findings.length === 0 ? (
        <p className="panel__note">
          the gates returned no findings. That is the receipt's answer, not a
          claim about what was not checked.
        </p>
      ) : (
        <ul className="rows">
          {value.receipt.findings.map((finding, index) => (
            <li
              key={`${finding.code}:${index}`}
              className={`row row--static${
                finding.severity === "error" ? " is-conflict" : ""
              }`}
            >
              <span className="row__id mono">{finding.code}</span>
              <span className="row__meta">{finding.severity}</span>
              <span className="row__fields">{finding.message}</span>
            </li>
          ))}
        </ul>
      )}

      <dl className="facts">
        <dt>receipt</dt>
        <dd className="mono">{value.receipt.receiptId}</dd>
        <dt>submission</dt>
        <dd className="mono">
          {value.receipt.submissionId} · {sha8(value.receipt.submissionDigest)}
        </dd>
        <dt>checked state</dt>
        <dd className="mono">
          v{value.receipt.checkedState.version} ·{" "}
          {sha8(value.receipt.checkedState.stateSha256)}
        </dd>
        <dt>validators</dt>
        <dd className="mono">{value.validators.join(", ")}</dd>
        <dt>effective checks</dt>
        <dd className="mono">{value.effectiveChecks.join(", ")}</dd>
      </dl>
      <p className="panel__note">{value.validatorNote}</p>
      <p className="panel__note">{value.canonicalFacts}</p>
      <HonestyLines lines={value.honesty} />
    </div>
  );
}
