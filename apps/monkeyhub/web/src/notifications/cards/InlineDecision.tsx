import type { ReactNode } from "react";

import "./cards.css";

/**
 * A decision a page asks where it is needed (ADR-014 B4, 6-2): which model the next edit starts from, whether
 * to record changes not yet recorded first, whether to continue from the version on screen. It is drawn as the
 * attention queue's DecisionCard is (the decide channel's attention-card), so the queue's cards and the page's
 * rows change style together and look the same in Modeling, the document page and the Design tree. It is the
 * page's own question, not a queue entry: its answers run the page's handlers, and each is counted as its
 * command (data-command); there is no later, set aside, bring back or undo. A page renders it only while the
 * question stands, and it draws nothing when it has nothing to ask or say.
 */

export interface InlineChoice {
  /** The command this answer is in the command tree, counted when it is clicked. */
  command: string;
  /** The command's argument where one command serves several places (data-command-arg). */
  arg?: string;
  /** Kept for the places a test or a style reads it by (data-action). */
  action?: string;
  label: ReactNode;
  /** The answer the page expects: drawn as the recommended one is. */
  primary?: boolean;
  autoFocus?: boolean;
  disabled?: boolean;
  /** A longer word on what the answer does, on hover. */
  title?: string;
  onSelect: () => void;
}

export interface InlineDecisionProps {
  /** The question's name for assistive technology. */
  label: string;
  /** The question, or the state it is about ("Viewing D · read-only"). */
  title?: ReactNode;
  /** The facts the question rests on. */
  summary?: ReactNode;
  /** The title and summary are announced as they change (a live status). */
  live?: boolean;
  /** Answers; a false or empty entry is an answer that does not apply now. */
  choices: readonly (InlineChoice | false | null | undefined)[];
  /** A quiet word after the answers (role status). */
  status?: ReactNode;
  /** What was refused or failed (role alert). */
  failure?: ReactNode;
  /** The page's own class, kept for its layout and the places that find the row by it. */
  className?: string;
  /** In a bar or a chip: one line, small answers. */
  compact?: boolean;
  /** The row's own data attributes, by name without data- ("source-match"). */
  data?: Readonly<Record<string, string | undefined>>;
  /** Anything else the row shows between the question and its answers (a name, a notice, an error panel). */
  children?: ReactNode;
}

export function InlineDecision({
  label, title, summary, live = false, choices, status, failure, className, compact = false, data, children,
}: InlineDecisionProps) {
  const answers = choices.filter((choice): choice is InlineChoice => Boolean(choice));
  if (!answers.length && !title && !summary && !status && !failure && !children) return null;
  const attributes = Object.fromEntries(Object.entries(data ?? {}).map(([name, value]) => [`data-${name}`, value]));
  const classes = ["attention-card", "attention-card--inline", compact && "attention-card--compact", className]
    .filter(Boolean).join(" ");
  return <article className={classes} data-channel="decide" data-kind="choice" role="group" aria-label={label} {...attributes}>
    {(title || summary) && <header className="attention-card__head" role={live ? "status" : undefined} aria-live={live ? "polite" : undefined}>
      {title && <p className="attention-card__title">{title}</p>}
      {summary && <p className="attention-card__summary">{summary}</p>}
    </header>}
    {children}
    {answers.length > 0 && <div className="attention-card__answers" role="group" aria-label={label}>
      {answers.map((choice, index) => <button key={`${choice.command}:${choice.arg ?? ""}:${index}`} type="button"
        data-command={choice.command} data-command-arg={choice.arg} data-action={choice.action}
        className={choice.primary ? "btn btn--primary attention-card__answer" : "btn attention-card__answer"}
        autoFocus={choice.autoFocus} disabled={choice.disabled} title={choice.title} onClick={choice.onSelect}>{choice.label}</button>)}
    </div>}
    {status && <p className="attention-card__status" role="status">{status}</p>}
    {failure && <p className="attention-card__failure" role="alert">{failure}</p>}
  </article>;
}
