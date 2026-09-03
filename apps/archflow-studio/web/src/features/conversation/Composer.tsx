/**
 * Where an intent is typed: one line in the grammar, against the selection
 * the context chip shows. The chip is the server's resolution or the
 * projection's own id, never a guess; the grammar hint names the four forms
 * the server understands today.
 */

import { useState, type FormEvent } from "react";

import type { StateProjectionDto } from "../../api/generated";
import { SelectionPicker } from "./SelectionPicker";

export interface Selection {
  readonly componentId: string;
  readonly elementId: string | null;
}

const PLACEHOLDER =
  "set height to 2.2 · increase height by 10 % · keep entity:…";

export function Composer({
  selection,
  projection,
  disabledReason,
  busy,
  draft,
  onDraft,
  onSubmit,
  onSelect,
}: {
  selection: Selection | null;
  projection: StateProjectionDto | null;
  /** Why nothing can be proposed right now, in words; null when it can. */
  disabledReason: string | null;
  busy: boolean;
  draft: string;
  onDraft(text: string): void;
  onSubmit(utterance: string): void;
  onSelect(componentId: string, elementId: string | null): void;
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const disabled = disabledReason !== null || busy;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const utterance = draft.trim();
    if (disabled || utterance === "") return;
    onSubmit(utterance);
  };

  return (
    <form className="composer" onSubmit={submit}>
      <div className="context">
        <span>talking about</span>
        {selection ? (
          <span className="pill pill--accent mono">
            {selection.elementId ?? selection.componentId}
          </span>
        ) : (
          <span className="quiet">nothing yet — pick in the model, or</span>
        )}
        <button
          type="button"
          className="btn btn--link"
          disabled={projection === null}
          onClick={() => setPickerOpen((open) => !open)}
        >
          {selection ? "change" : "choose a component"}
        </button>
      </div>
      {pickerOpen && projection && (
        <SelectionPicker
          projection={projection}
          onPick={(componentId, elementId) => {
            onSelect(componentId, elementId);
            setPickerOpen(false);
          }}
          onClose={() => setPickerOpen(false)}
        />
      )}
      <div className="composer__box">
        <input
          type="text"
          aria-label="intent"
          placeholder={PLACEHOLDER}
          value={draft}
          disabled={disabled}
          onChange={(event) => onDraft(event.target.value)}
        />
        <button
          type="submit"
          className="btn btn--primary"
          disabled={disabled || draft.trim() === ""}
        >
          {busy ? "Proposing…" : "Propose"}
        </button>
      </div>
      {disabledReason ? (
        <p className="composer__hint composer__hint--why">{disabledReason}</p>
      ) : (
        <p className="composer__hint">
          Four forms are understood today: <code>set … to …</code>,{" "}
          <code>set … = …</code>, <code>increase … by … %</code>,{" "}
          <code>decrease … by … %</code>, optionally ending with{" "}
          <code>keep …</code>.
        </p>
      )}
    </form>
  );
}
