/**
 * Where an intent is typed: one line in the grammar, against the selection
 * the context chip shows. The chip is the server's resolution or the
 * projection's own id, never a guess; the grammar hint names the four forms
 * the server understands today.
 */

import { useState, type FormEvent } from "react";

import type { GestureDto, StateProjectionDto } from "../../api/generated";
import { useT, type TFunction } from "../../i18n/useT";
import { SelectionPicker } from "./SelectionPicker";

const MARK_GLYPH: Record<GestureDto["kind"], string> = {
  circle: "◯",
  arrow: "↗",
  keep: "✓",
  remove: "✗",
};

/** One mark as a chip: its kind and how many of the file's objects it touched. */
function markLabel(gesture: GestureDto, t: TFunction): string {
  const hits = gesture.hits ?? [];
  const kind = t(`composer.mark.${gesture.kind}`);
  if (gesture.kind === "keep" || gesture.kind === "remove") {
    return kind + " · " + (hits[0]?.objectName ?? t("composer.mark.empty"));
  }
  return `${kind} · ${t(
    hits.length === 1 ? "composer.mark.oneObject" : "composer.mark.manyObjects",
    { count: hits.length },
  )}`;
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
export function Composer({
  selection,
  projection,
  disabledReason,
  busy,
  gestures,
  onRemoveGesture,
  intentProvider,
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
  /** Who reads sentences here, as the server said; the hint is worded from it. */
  intentProvider: string | null;
  draft: string;
  onDraft(text: string): void;
  onSubmit(utterance: string): void;
  onSelect(componentId: string, elementId: string | null): void;
}) {
  const t = useT();
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
        <span>{t("composer.context.talkingAbout")}</span>
        {selection ? (
          <span className="pill pill--accent mono">
            {selection.elementId ?? selection.componentId}
          </span>
        ) : (
          <span className="quiet">{t("composer.context.nothingSelected")}</span>
        )}
        <button
          type="button"
          className="btn btn--link"
          disabled={projection === null}
          onClick={() => setPickerOpen((open) => !open)}
        >
          {selection
            ? t("composer.context.change")
            : t("composer.context.chooseComponent")}
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
        <div className="marks" aria-label={t("composer.marks.ariaLabel")}>
          <span className="quiet">{t("composer.marks.with")}</span>
          {gestures.map((gesture, index) => (
            <span key={index} className={"gesture-chip gesture-chip--" + gesture.kind}>
              {MARK_GLYPH[gesture.kind]} {markLabel(gesture, t)}
              <button
                type="button"
                className="gesture-chip__x"
                aria-label={t("composer.mark.removeAria", {
                  kind: t(`composer.mark.${gesture.kind}`),
                })}
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
          aria-label={t("composer.intent.ariaLabel")}
          placeholder={t("composer.placeholder")}
          value={draft}
          disabled={busy}
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
          {busy ? t("composer.proposing") : t("composer.propose")}
        </button>
      </div>
      {disabledReason ? (
        <p className="composer__hint composer__hint--why">{disabledReason}</p>
      ) : (
        <p className="composer__hint">
          {intentProvider === "codex" || intentProvider === "anthropic" ? (
            <>
              {t("composer.hint.agent", { provider: intentProvider })}
            </>
          ) : (
            <>
              {t("composer.hint.exactPrefix")} {" "}
              <code>set … to …</code>, <code>set … = …</code>,{" "}
              <code>increase … by … %</code>, <code>decrease … by … %</code>,{" "}
              {t("composer.hint.exactOptional")} <code>keep …</code>{" "}
              {t("composer.hint.exactSuffix")}
            </>
          )}
        </p>
      )}
    </form>
  );
}
