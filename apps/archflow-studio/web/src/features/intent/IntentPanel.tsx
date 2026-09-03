/**
 * One sentence, against the selection the user already made.
 *
 * The selection travels in the request, never in the utterance: the component
 * and element come from what was clicked, so nothing the user types can change
 * *what* is changed.
 *
 * The four forms listed before submitting are a hint. The authoritative list is
 * the one the server sends back with `BLOCKED_NEEDS_HUMAN`, and when it does,
 * that list replaces the hint verbatim and the question is rendered in place —
 * inside the panel, not as a toast.
 */

import { useState } from "react";

import { ErrorPanel } from "../../app/ErrorPanel";
import type { StudioApiError } from "../../api/client";

/** Shown before the server has had a reason to state the grammar itself. */
const GRAMMAR_HINT: readonly string[] = [
  "set <field> to <number>[ <unit>]",
  "set <field> = <number>[ <unit>]",
  "increase <field> by <number> %",
  "decrease <field> by <number> %",
];

export function IntentPanel({
  selectedComponentId,
  selectedElementId,
  busy,
  error,
  onSubmit,
}: {
  selectedComponentId: string | null;
  selectedElementId: string | null;
  busy: boolean;
  error: StudioApiError | null;
  onSubmit(utterance: string): void;
}) {
  const [utterance, setUtterance] = useState("");
  const blocked = error?.code === "BLOCKED_NEEDS_HUMAN";
  const forms =
    blocked && error && error.acceptedForms.length > 0
      ? error.acceptedForms
      : GRAMMAR_HINT;

  return (
    <div className="intent">
      <p className="panel__note">
        target{" "}
        <span className="mono">{selectedComponentId ?? "no component"}</span>
        {selectedElementId && (
          <>
            {" · element "}
            <span className="mono">{selectedElementId}</span>
          </>
        )}
      </p>
      <ul className="forms">
        {forms.map((form) => (
          <li key={form}>
            <code>{form}</code>
          </li>
        ))}
      </ul>
      <p className="panel__note">
        any of them may end with <code>keep &lt;ref&gt;[, &lt;ref&gt;…]</code> to
        name what the change must not disturb.
      </p>
      <form
        className="intent__form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!utterance.trim()) return;
          onSubmit(utterance.trim());
        }}
      >
        <input
          className="input"
          type="text"
          value={utterance}
          placeholder="set height to 2.2"
          disabled={selectedComponentId === null || busy}
          onChange={(event) => setUtterance(event.target.value)}
        />
        <button
          className="button button--primary"
          type="submit"
          disabled={
            selectedComponentId === null || busy || utterance.trim() === ""
          }
        >
          {busy ? "proposing…" : "Propose"}
        </button>
      </form>
      {selectedComponentId === null && (
        <p className="panel__note">
          select a component before proposing: the server refuses a proposal
          that names no target.
        </p>
      )}
      {error && <ErrorPanel error={error} what="POST /api/proposals" />}
    </div>
  );
}
