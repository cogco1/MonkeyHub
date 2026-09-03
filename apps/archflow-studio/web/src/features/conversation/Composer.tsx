/**
 * Where an intent is typed: one line in the grammar, against the selection
 * the context chip shows. The chip is the server's resolution or the
 * projection's own id, never a guess; the grammar hint names the four forms
 * the server understands today.
 */

import { useState, type FormEvent } from "react";

import type { GestureDto, StateProjectionDto } from "../../api/generated";
import { SelectionPicker } from "./SelectionPicker";

const MARK_GLYPH: Record<GestureDto["kind"], string> = {
  circle: "◯",
  arrow: "↗",
  keep: "✓",
  remove: "✗",
};

/** One mark as a chip: its kind and how many of the file's objects it touched. */
function markLabel(gesture: GestureDto): string {
  const hits = gesture.hits ?? [];
  if (gesture.kind === "keep" || gesture.kind === "remove") {
    return gesture.kind + " · " + (hits[0]?.objectName ?? "nothing under the mark");
  }
  return gesture.kind + " · " + String(hits.length) + (hits.length === 1 ? " object" : " objects");
}

export interface Selection {
  readonly componentId: string;
  readonly elementId: string | null;
}

/**
 * The examples an architect would actually say. The studio's round-1 grammar
 * answers most of them with a question — which field, which number — and the
 * hint under the box says so rather than teaching the grammar first.
 */
const PLACEHOLDER =
  "make the west portico a little taller · open up the entry · keep the roofline";

export function Composer({
  selection,
  projection,
  disabledReason,
  busy,
  gestures,
  onRemoveGesture,
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
  /** Marks drawn on the model, sent with the sentence; the server reads them. */
  gestures: readonly GestureDto[];
  onRemoveGesture(index: number): void;
  draft: string;
  onDraft(text: string): void;
  onSubmit(utterance: string): void;
  onSelect(componentId: string, elementId: string | null): void;
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const disabled = disabledReason !== null || busy;

  const send = () => {
    const utterance = draft.trim();
    if (disabled || utterance === "") return;
    onSubmit(utterance);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    send();
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
      {gestures.length > 0 && (
        <div className="marks" aria-label="marks on the model">
          <span className="quiet">with</span>
          {gestures.map((gesture, index) => (
            <span key={index} className={"mark mark--" + gesture.kind}>
              {MARK_GLYPH[gesture.kind]} {markLabel(gesture)}
              <button
                type="button"
                className="mark__x"
                aria-label={"remove this " + gesture.kind + " mark"}
                onClick={() => onRemoveGesture(index)}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="composer__box">
        <input
          type="text"
          aria-label="intent"
          placeholder={PLACEHOLDER}
          value={draft}
          disabled={disabled}
          onChange={(event) => onDraft(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends; the form's own submission covers the button, and
            // this covers a keyboard event that never reaches it.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              send();
            }
          }}
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
          Say it in your words, about the thing you picked. An agent reads it
          against the record and proposes one exact change; if the record does
          not carry what you asked for, it asks. Marks on the model go with the
          sentence.
        </p>
      )}
    </form>
  );
}
