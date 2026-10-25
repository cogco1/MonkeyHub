/**
 * S08: the one 「交给 AI」 control, the same on the Board's selection, the document page and the model's
 * tracing paper: one sentence, one button, and, where a surface has them, references folded beside it.
 */
import type { ReactNode } from "react";
import { usePreferences } from "../settings/preferences";
import { HAND_TO_AI_WORDS } from "./handToAi";
import "./handToAi.css";

export function HandToAiBar({ words, onWords, placeholder, disabled = false, sending = false, error = null, sent = false,
  onSend, references, referenceCount = 0, className }: {
  words: string;
  onWords(words: string): void;
  /** The words already on the surface (a selected text), sent when the box is left empty. */
  placeholder?: string;
  disabled?: boolean;
  sending?: boolean;
  error?: string | null;
  sent?: boolean;
  onSend(): void;
  /** F115: reference pages and their purpose, folded until opened. */
  references?: ReactNode;
  referenceCount?: number;
  className?: string;
}) {
  const { language } = usePreferences();
  const text = HAND_TO_AI_WORDS[language];
  const ready = !disabled && !sending && Boolean(words.trim() || placeholder?.trim());
  return <div className={`hand-to-ai${className ? ` ${className}` : ""}`} role="group" aria-label={text.action}>
    <div className="hand-to-ai__row">
      <input className="hand-to-ai__words" type="text" value={words} maxLength={2000} aria-label={text.label}
        placeholder={placeholder?.trim() || text.placeholder} disabled={disabled || sending}
        onChange={(event) => onWords(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
          event.preventDefault(); event.stopPropagation();
          if (ready) onSend();
        }} />
      <button type="button" className="hand-to-ai__send" data-command="markup.hand_to_ai.hand_over" disabled={!ready}
        onClick={onSend}>{sending ? text.sending : text.action}</button>
    </div>
    {references && <details className="hand-to-ai__references">
      <summary data-command="view.disclosure.toggle" data-command-arg="hand_to_ai_references">{text.referencesCount(referenceCount)}</summary>
      {references}
    </details>}
    {error && <p className="hand-to-ai__error" role="alert">{error}</p>}
    {!error && sent && <p className="hand-to-ai__sent" role="status">{text.sent}</p>}
  </div>;
}
