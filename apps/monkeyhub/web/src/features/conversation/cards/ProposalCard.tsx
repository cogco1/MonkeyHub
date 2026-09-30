/**
 * A proposed change, in design-review language.
 *
 * The proposed result stays visible. Compiler metadata and the kernel's
 * detailed closure remain available on demand; neither becomes a form the
 * architect must complete. Apply runs a candidate, not a project issue.
 */

import { useEffect, useRef, useState } from "react";

import type { AgentReadingDto, ProposalDto } from "../../../api/generated";
import type { EvidenceTab } from "../../../app/evidence";
import { BilingualText } from "../../../i18n/BilingualText";
import { useT } from "../../../i18n/useT";
import { Verbatim } from "./Verbatim";

/** A release is sent this long after the last move, so a drag is one request. */
const REFINE_DEBOUNCE_MS = 250;

/**
 * The hand's range around the record's number: half to one-and-a-half of the
 * old value, never below zero, in steps of one percent of it, widened to hold
 * the proposal's own number when the sentence went further than that. The
 * domain is the record's own units, whatever they are; the grammar rounds.
 */
function refineDomain(
  old: number,
  current: number,
): { min: number; max: number; step: number } {
  const step = Math.abs(old) / 100;
  return {
    min: Math.min(Math.max(old * 0.5, step), current),
    max: Math.max(old * 1.5, current),
    step,
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** A kernel ref without its prefix, for the eye; the ref itself is in the title attribute. */
function shortRef(ref: string): string {
  const colon = ref.indexOf(":");
  return colon === -1 ? ref : ref.slice(colon + 1);
}

export function ProposalCard({
  proposal,
  agent,
  ghostShown,
  refinements,
  refining,
  busy,
  inactive,
  inactiveReason = inactive ? "otherBase" : null,
  onRun,
  onAdjust,
  onRefine,
  onEvidence,
}: {
  proposal: ProposalDto;
  agent: AgentReadingDto | null;
  /** Whether this proposal is the one drawn as a ghost in the model right now. */
  ghostShown: boolean;
  /** How many times the hand has refined this entry's number. */
  refinements: number;
  /** Whether a refinement of this entry is on the wire. */
  refining: boolean;
  busy: boolean;
  inactive: boolean;
  /**
   * Why an inactive proposal cannot be applied: it was made on another editing
   * base, or its base is not the model on screen. Null says nothing.
   */
  inactiveReason?: "otherBase" | "viewing" | null;
  onRun(): void;
  onAdjust(utterance: string): void;
  onRefine(value: number): void;
  onEvidence(tab: EvidenceTab): void;
}) {
  const t = useT();
  const { target, change, impact } = proposal;
  const subject = target.elementId ?? target.componentId;
  const compiled = change.kind === "edit_components"
    ? proposal.utterance
    : agent?.compiledUtterance ?? proposal.utterance;
  const readByAgent = agent !== null && agent.provider !== "deterministic";
  return (
    <article className="card card--proposal">
      <div className="card__row">
        <p className="label">{t("proposal.title")}</p>
        {change.kind === "edit_components" ? (
          <p className="verbatim-line">
            <BilingualText source={change.summary} />
          </p>
        ) : readByAgent && agent.why && (
          <p className="verbatim-line">
            <BilingualText source={agent.why} />
          </p>
        )}
      </div>
      {(change.kind !== "edit_components" || change.kept.length > 0) && <div className="card__row">
        <dl className="kv kv--review">
          {change.kind === "edit_components" ? (
            change.kept.length > 0 && (
              <>
                <dt>{t("proposal.keep")}</dt>
                <dd>
                  <ul className="reflist">
                    {change.kept.map((line) => (
                      <li key={line}><BilingualText source={line} /></li>
                    ))}
                  </ul>
                </dd>
              </>
            )
          ) : (
            <>
              <dt>{t("proposal.change")}</dt>
              <dd className="delta">
                {target.key} {String(change.old)} → <b>{String(change.new)}</b>
                {change.unit ? ` ${change.unit}` : ""}
                {change.unit === null && (
                  <span className="quiet"> {t("proposal.recordUnits")}</span>
                )}
              </dd>
            </>
          )}
        </dl>
      </div>}
      <details className="card__row card__details">
        <summary>{t("common.technicalDetails")}</summary>
        <p className="card__title">
          <span className="mono">{subject}</span>
          {target.elementId && (
            <span className="quiet"> · {target.componentId}</span>
          )}
        </p>
        {readByAgent && (
          <div className="card__agent">
            <p className="label">
              {t("proposal.readBy")} {" "}
              <span className="mono">{agent.provider}</span>
              {agent.model ? ` · ${agent.model}` : ""} ·{" "}
              {(agent.latencyMs / 1000).toFixed(1)} s
            </p>
            <p className="quiet">
              {t("proposal.compiledTo")} {" "}
              <code>{agent.compiledUtterance}</code>
            </p>
          </div>
        )}
        <dl className="kv kv--review">
          <dt>{t("proposal.willUpdate")}</dt>
          <dd>
            {impact.propagated.length === 0 ? (
              <span className="quiet">{t("proposal.noDownstream")}</span>
            ) : (
              <ul className="reflist">
                {impact.propagated.map((ref) => (
                  <li key={ref} className="mono" title={ref}>
                    {shortRef(ref)}
                  </li>
                ))}
              </ul>
            )}
            {impact.unknownCoverage.count > 0 && (
              <span className="quiet">
                {" "}
                · {t("proposal.unknownCoverage", {
                  count: impact.unknownCoverage.count,
                })}{" "}
                <button
                  type="button"
                  className="btn btn--link"
                  onClick={() => onEvidence("honesty")}
                >
                  {t("proposal.why")}
                </button>
              </span>
            )}
          </dd>
          <dt>{t("proposal.keep")}</dt>
          <dd>
            {proposal.protected.length === 0 ? (
              <span className="quiet">{t("proposal.nothingNamed")}</span>
            ) : (
              <ul className="reflist">
                {proposal.protected.map((ref) => (
                  <li key={ref} className="mono" title={ref}>
                    {shortRef(ref)}
                  </li>
                ))}
              </ul>
            )}
          </dd>
        </dl>
        <Verbatim lines={impact.honesty} />
        {change.kind === "edit_components" && (
          <>
            <ul className="reflist">
              {change.changes.map((item, index) => (
                <li key={`${item.entityId}:${index}`}>
                  <BilingualText source={item.label} />
                  {" · "}<BilingualText source={item.description} />
                </li>
              ))}
            </ul>
            <pre>{JSON.stringify(change.edits, null, 2)}</pre>
          </>
        )}
      </details>
      {proposal.status === "conflict" && (
        <div className="card__row">
          <p className="card__conflict">
            {t("proposal.conflict")} — {" "}
            <span className="mono">{impact.conflicts.join(", ")}</span>
          </p>
        </div>
      )}
      {inactive && inactiveReason && <p className="card__row quiet" data-inactive-reason={inactiveReason}>
        {t(inactiveReason === "viewing" ? "stage.base.proposalViewing" : "proposal.otherBase")}</p>}
      {!inactive && change.kind !== "edit_components" && target.key !== null &&
        typeof change.old === "number" &&
        typeof change.new === "number" &&
        change.old > 0 && (
          <details className="card__row card__details">
            <summary>{t("proposal.refine.ariaLabel")}</summary>
            <Refine
              fieldKey={target.key}
              old={change.old}
              current={change.new}
              refinements={refinements}
              refining={refining}
              onRefine={onRefine}
            />
          </details>
        )}
      {ghostShown && change.kind !== "edit_components" && (
        <div className="card__row">
          <p className="quiet">
            <span className="ghost-mark">{t("proposal.ghostShown")}</span> · {" "}
            {t("proposal.approximate")}
            {target.key === "height" && (
              <> · {t("proposal.verticalStretch")}</>
            )}{" "}
            — {t("proposal.applyForExact")}
          </p>
        </div>
      )}
      <div className="card__row actions">
        <button
          type="button"
          className="btn btn--primary"
          disabled={busy || inactive || proposal.status === "conflict"}
          onClick={onRun}
        >
          {t("common.apply")}
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => onAdjust(compiled)}
        >
          {t("proposal.adjust")}
        </button>
        <span className="quiet">{t("candidate.harness")}</span>
      </div>
    </article>
  );
}

/**
 * The agent found the variable; the hand moves it. A slider from half to
 * one-and-a-half of the record's number with one-percent steps, a minus and a
 * plus, and the number it will send. Every release goes to the server as the
 * exact sentence ``set <key> to <n>``, which the grammar answers without an
 * agent, and the card is replaced by the proposal that comes back: the card
 * never shows a number the record did not type.
 */
function Refine({
  fieldKey,
  old,
  current,
  refinements,
  refining,
  onRefine,
}: {
  fieldKey: string;
  old: number;
  current: number;
  refinements: number;
  refining: boolean;
  onRefine(value: number): void;
}) {
  const t = useT();
  const { min, max, step } = refineDomain(old, current);
  const [value, setValue] = useState(current);
  const timer = useRef<number | null>(null);

  // The server's number wins whenever it changes: a refinement came back, or
  // the entry was replaced. The slider follows it - and when a refinement
  // ends with the number unchanged (a question, a refusal), the slider snaps
  // back to it rather than keep showing a number the record never typed.
  useEffect(() => {
    setValue(current);
  }, [current]);
  useEffect(() => {
    if (!refining) setValue(current);
  }, [refining, current]);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  const send = (next: number) => {
    const bounded = clamp(next, min, max);
    setValue(bounded);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      if (bounded !== current) onRefine(bounded);
    }, REFINE_DEBOUNCE_MS);
  };

  const percent = ((value - old) / old) * 100;
  const sign = percent > 0 ? "+" : "";
  const status = refining
    ? t("proposal.refine.asking")
    : refinements > 0
      ? t("proposal.refine.count", { count: refinements })
      : t("proposal.refine.byHand");
  const originPercent = max > min ? ((old - min) / (max - min)) * 100 : 50;
  return (
    <div
      className="card__row refine"
      aria-label={t("proposal.refine.ariaLabel")}
    >
      <div className="refine__head">
        <span className="label">{t("proposal.refine.label")}</span>
        <span className={"mono refine__readout" + (refining ? " refine__readout--asking" : "")}>
          {fieldKey} {Number(value.toFixed(6))}
          <span className="quiet">
            {" "}
            · {sign}
            {percent.toFixed(1)} %
          </span>
        </span>
        <span className="quiet refine__meta">{status}</span>
      </div>
      <div className="refine__row">
        <button
          type="button"
          className="btn btn--small"
          aria-label={t("proposal.refine.less")}
          onClick={() => send(value - step)}
        >
          −
        </button>
        <input
          type="range"
          className="refine__slider"
          min={min}
          max={max}
          step={step}
          value={value}
          aria-label={t("proposal.refine.sliderAria")}
          onChange={(event) => send(Number(event.currentTarget.value))}
        />
        <button
          type="button"
          className="btn btn--small"
          aria-label={t("proposal.refine.more")}
          onClick={() => send(value + step)}
        >
          +
        </button>
      </div>
      <div className="refine__scale mono">
        <span>{Number(min.toFixed(6))}</span>
        <span
          className="refine__origin"
          title={t("proposal.refine.recordNumber")}
          style={{ left: originPercent + "%" }}
        >
          {Number(old.toFixed(6))}
        </span>
        <span>{Number(max.toFixed(6))}</span>
      </div>
      <p className="quiet refine__note">
        {t("proposal.refine.note")}
      </p>
    </div>
  );
}
