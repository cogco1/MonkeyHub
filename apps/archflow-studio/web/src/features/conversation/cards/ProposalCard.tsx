/**
 * A proposed change, in design-review language.
 *
 * Change is the record's number and the proposed one; Will update is the
 * kernel's closure, listed not re-derived; Keep is what the sentence protected.
 * When an agent compiled the sentence, its reading is printed above the card as
 * the agent's — provider, model, its own "why" — so the reader can tell the
 * agent's words from the record's answer. Apply runs the candidate; Adjust puts
 * the compiled sentence back in the composer.
 */

import { useEffect, useRef, useState } from "react";

import type { AgentReadingDto, ProposalDto } from "../../../api/generated";
import type { EvidenceTab } from "../../../app/evidence";
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

const HARNESS_SENTENCE =
  "a harness run beside the project; no stage advances, nothing is written to HEAD";

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
  onRun(): void;
  onAdjust(utterance: string): void;
  onRefine(value: number): void;
  onEvidence(tab: EvidenceTab): void;
}) {
  const { target, change, impact } = proposal;
  const subject = target.elementId ?? target.componentId;
  const compiled = agent?.compiledUtterance ?? proposal.utterance;
  const readByAgent = agent !== null && agent.provider !== "deterministic";
  return (
    <article className="card card--proposal">
      {readByAgent && (
        <div className="card__row card__agent">
          <p className="label">
            Read by {agent.provider}
            {agent.model ? ` · ${agent.model}` : ""} ·{" "}
            {(agent.latencyMs / 1000).toFixed(1)} s
          </p>
          {agent.why && <p className="verbatim-line">{agent.why}</p>}
          <p className="quiet">
            compiled to <code>{agent.compiledUtterance}</code>
          </p>
        </div>
      )}
      <div className="card__row">
        <p className="label">Proposed change</p>
        <p className="card__title">
          <span className="mono">{subject}</span>
          {target.elementId && (
            <span className="quiet"> · {target.componentId}</span>
          )}
        </p>
      </div>
      <div className="card__row">
        <dl className="kv kv--review">
          <dt>Change</dt>
          <dd className="delta">
            {target.key} {String(change.old)} → <b>{String(change.new)}</b>
            {change.unit ? ` ${change.unit}` : ""}
            {change.unit === null && (
              <span className="quiet"> (the record's own units)</span>
            )}
          </dd>
          <dt>Will update</dt>
          <dd>
            {impact.propagated.length === 0 ? (
              <span className="quiet">nothing downstream is declared</span>
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
                · {impact.unknownCoverage.count} components with unknown
                coverage{" "}
                <button
                  type="button"
                  className="btn btn--link"
                  onClick={() => onEvidence("honesty")}
                >
                  why
                </button>
              </span>
            )}
          </dd>
          <dt>Keep</dt>
          <dd>
            {proposal.protected.length === 0 ? (
              <span className="quiet">nothing was named</span>
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
      </div>
      {proposal.status === "conflict" && (
        <div className="card__row">
          <p className="card__conflict">
            Conflict: the change reaches what you asked to keep —{" "}
            <span className="mono">{impact.conflicts.join(", ")}</span>
          </p>
        </div>
      )}
      {typeof change.old === "number" &&
        typeof change.new === "number" &&
        change.old > 0 && (
          <Refine
            fieldKey={target.key}
            old={change.old}
            current={change.new}
            refinements={refinements}
            refining={refining}
            onRefine={onRefine}
          />
        )}
      {ghostShown && (
        <div className="card__row">
          <p className="quiet">
            <span className="ghost-mark">ghost shown in the model</span> · approximate
            {target.key === "height" && " · drawn as a vertical stretch of the picked objects"} — Apply
            for the exact geometry
          </p>
        </div>
      )}
      <div className="card__row actions">
        <button
          type="button"
          className="btn btn--primary"
          disabled={busy}
          onClick={onRun}
        >
          Apply
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => onAdjust(compiled)}
        >
          Adjust
        </button>
        <span className="quiet">{HARNESS_SENTENCE}</span>
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
    ? "asking the record…"
    : refinements > 0
      ? "refined ×" + String(refinements)
      : "by hand";
  const originPercent = max > min ? ((old - min) / (max - min)) * 100 : 50;
  return (
    <div className="card__row refine" aria-label={"refine " + fieldKey}>
      <div className="refine__head">
        <span className="label">Refine</span>
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
          aria-label="one percent less"
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
          onChange={(event) => send(Number(event.currentTarget.value))}
        />
        <button
          type="button"
          className="btn btn--small"
          aria-label="one percent more"
          onClick={() => send(value + step)}
        >
          +
        </button>
      </div>
      <div className="refine__scale mono">
        <span>{Number(min.toFixed(6))}</span>
        <span
          className="refine__origin"
          title="the record's number"
          style={{ left: originPercent + "%" }}
        >
          {Number(old.toFixed(6))}
        </span>
        <span>{Number(max.toFixed(6))}</span>
      </div>
      <p className="quiet refine__note">
        each move asks the record again - the number in Change is always the record's
      </p>
    </div>
  );
}
