/**
 * The architect's words, with an optional selection and marks. An agent can
 * resolve a subject from the record; the manual grammar still needs one.
 *
 * Beside the tree that chooses the selection sits the capability panel, which
 * says what that selection can be asked: the box is no longer typed into the
 * dark, and a row's "set…" button fills it with a sentence the catalog already
 * agreed exists. The panel writes only the draft — the same text state the
 * transcript's own reply and adjust buttons write.
 */

import { useState, type FormEvent } from "react";

import type { GestureDto, StateProjectionDto } from "../../api/generated";
import { designObjectLabel } from "../../app/format";
import { useT, type TFunction } from "../../i18n/useT";
import { CapabilityPanel } from "./CapabilityPanel";
import { ComponentTree } from "./ComponentTree";

const MARK_GLYPH: Record<GestureDto["kind"], string> = {
  circle: "◯",
  arrow: "↗",
  freehand: "✎",
  line: "╱",
  ruler: "↔",
  arc: "⌒",
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
 * The examples an architect would actually say, without requiring the person
 * to name an internal parameter or construction operation.
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
  const hasAgent = intentProvider === "codex" || intentProvider === "anthropic";
  const controls = projection && selection && (
    <CapabilityPanel projection={projection} selection={selection} onPrefill={onDraft} />
  );

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
            {designObjectLabel(selection.elementId ?? selection.componentId)}
          </span>
        ) : (
          <span className="quiet">
            {t(hasAgent ? "composer.context.describe" : "composer.context.nothingSelected")}
          </span>
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
        <ComponentTree
          projection={projection}
          selectedComponentId={selection?.componentId ?? null}
          selectedElementId={selection?.elementId ?? null}
          onPick={(componentId, elementId) => {
            onSelect(componentId, elementId);
            setPickerOpen(false);
          }}
          onClose={() => setPickerOpen(false)}
        />
      )}
      {controls && (hasAgent ? (
        <details className="card__details">
          <summary>{t("composer.controls")}</summary>
          {controls}
        </details>
      ) : controls)}
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
          {hasAgent ? (
            <>
              {t("composer.hint.agent")}
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
