import { useState, type ReactNode } from "react";

import { useCommands } from "../../features/commands/CommandProvider";
import { usePreferences } from "../../features/settings/preferences";
import type { MessageKey } from "../../i18n/messages.en";
import { useT } from "../../i18n/useT";
import { openCard } from "../openChat";
import { dismissCard, laterCard, reopenCard, resolveCard, undoCard, type QueueFailure } from "../queue";
import { chosenOption, isPending, needsWords, orderedOptions, say } from "./cardText";
import type { AttentionCard, CardActionResult, ChangeItem, CommandRef } from "./types";
import "./cards.css";

/**
 * The attention queue's cards (1-4): DecisionCard for what needs a decision, MarkupCard for what waits on the
 * drawing, GuessCard for a question or a suggestion, NoticeCard for news. An answer goes to the Hub, whose
 * executor carries it out as the person; a command that only opens something (compare, a Board frame, the
 * conversation) runs here, through the command tree, and one the tree does not know yet is left out. Later, set
 * aside, bring back and undo are the queue's own commands (attention.queue). The answer and open buttons are rows
 * of what the card offers (data-command-view), as Ctrl K's rows are: an open counts as its command's use when it
 * runs, an answer when it is sent. A use is counted for a command the tree has; the queue's own are counted
 * once their group is registered (after 0-2).
 */

/** The most the Hub takes with an answer as the architect's own words (AttentionResolveRequest.note). */
const NOTE_MAX = 400;

interface CardProps {
  card: AttentionCard;
  /** The project a card belongs to, as the shell names it; left out where the page shows one project. */
  project?: string | null;
  /** Arrived while the list was on screen: marked for a moment. */
  fresh?: boolean;
}

/** One card's pending action and the Hub's refusal of the last one, kept with the card. */
function useCardActions() {
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<QueueFailure | null>(null);
  const act = (name: string, action: () => Promise<unknown>) => {
    setBusy(name); setFailure(null);
    void action().then((result) => {
      // Taking an answer back after the project moved on raises a card that asks instead: show that one.
      const followUp = (result as CardActionResult | null)?.followUp;
      if (followUp) openCard(followUp.id, "", followUp.projectDir);
    }, (cause: QueueFailure) => setFailure({ code: cause?.code ?? "HTTP_ERROR", detail: cause?.detail ?? String(cause) }))
      .finally(() => setBusy(null));
  };
  return { busy, failure, act };
}

function Opens({ refs }: { refs: readonly CommandRef[] }) {
  const { tree, run, stateOf } = useCommands();
  const { language } = usePreferences();
  const t = useT();
  const known = refs.flatMap((ref) => {
    const command = tree.commands.find((item) => item.id === ref.commandId);
    return command ? [{ ref, command }] : [];
  });
  if (!known.length) return null;
  return <div className="attention-card__opens">{known.map(({ ref, command }, index) => {
    const state = stateOf(command);
    const reason = state.kind === "unavailable" ? state.reason ?? t("commands.unavailable") : null;
    return <button key={`${ref.commandId}:${index}`} type="button" className="menu-command" data-command-view
      aria-disabled={reason ? true : undefined} title={reason ?? undefined}
      onClick={() => { if (!reason) run(ref.commandId, "card", { args: ref.args }); }}>{command.label[language]}</button>;
  })}</div>;
}

function CardFrame({ card, project, fresh = false, compact = false, items, blocked, children }: CardProps & {
  compact?: boolean; children?: ReactNode;
  /** The numbers ticked on a change list; the answer that runs them sends these (3-4). */
  items?: readonly number[];
  /** An answer that cannot be sent yet, with why. */
  blocked?: (optionId: string) => string | null;
}) {
  const { language } = usePreferences();
  const t = useT();
  const { busy, failure, act } = useCardActions();
  const { note } = useCommands();
  const [words, setWords] = useState("");
  const chosen = chosenOption(card);
  const pending = isPending(card);
  const reason = card.recommendation ? say(card.recommendation.reason, card.recommendation.reasonZh, language) : null;
  return <article className="attention-card" id={`attention-card-${card.id}`} tabIndex={-1} data-card={card.id} data-fresh={fresh || undefined} data-channel={card.channel}
    data-kind={card.kind} data-state={card.state} aria-labelledby={`attention-card-${card.id}-title`}>
    <header className="attention-card__head">
      <p className="attention-card__eyebrow">
        <span className="attention-card__channel">{t(`cards.channel.${card.channel}` as MessageKey)}</span>
        {project && <span className="attention-card__project">{project}</span>}
      </p>
      <h3 className="attention-card__title" id={`attention-card-${card.id}-title`}>{say(card.title, card.titleZh, language)}</h3>
      {card.summary && <p className="attention-card__summary">{say(card.summary, card.summaryZh, language)}</p>}
    </header>
    {!compact && card.media.some((item) => item.caption) && <ul className="attention-card__media">{card.media.filter((item) => item.caption).map((item, index) =>
      <li key={index} data-media={item.kind}>{say(item.caption!, item.captionZh, language)}</li>)}</ul>}
    {!compact && card.evidence.length > 0 && <dl className="attention-card__evidence" aria-label={t("cards.evidence")}>{card.evidence.map((row) =>
      <div key={row.key} data-status={row.status}><dt>{say(row.label, row.labelZh, language)}</dt><dd>{say(row.text, row.textZh, language)}</dd></div>)}</dl>}
    {children}
    {pending && card.options.some(needsWords) && <label className="attention-card__words">
      <span>{t("cards.words")}</span>
      <textarea rows={2} maxLength={NOTE_MAX} value={words} onChange={(event) => setWords(event.target.value)} />
    </label>}
    {pending && card.options.length > 0 && <div className="attention-card__answers" role="group" aria-label={t("cards.answers")}>
      {orderedOptions(card).map((option) => <button key={option.id} type="button" data-option={option.id} data-command-view
        className={option.recommended ? "btn btn--primary attention-card__answer" : "btn attention-card__answer"}
        disabled={busy !== null || (needsWords(option) && !words.trim()) || Boolean(blocked?.(option.id))} title={blocked?.(option.id) ?? undefined}
        aria-describedby={`attention-card-${card.id}-${option.id}`}
        onClick={() => act(option.id, async () => {
          const result = await resolveCard(card.id, option.id, needsWords(option) ? words.trim() : undefined, items && option.id === "run" ? items : undefined);
          note(option.action.commandId, "card");
          return result;
        })}>
        <span>{say(option.label, option.labelZh, language)}{option.recommended && <small className="attention-card__recommended">{t("cards.recommended")}</small>}</span>
        <small id={`attention-card-${card.id}-${option.id}`}>{say(option.consequence, option.consequenceZh, language)}</small>
      </button>)}
    </div>}
    {pending && reason && <p className="attention-card__reason">{reason}</p>}
    {card.state === "working" && <p className="attention-card__status" role="status">{t("cards.working")}</p>}
    {card.state === "done" && <p className="attention-card__status">{chosen
      ? t(card.resolution?.by === "machine" ? "cards.handledForYou" : "cards.chosen", { option: say(chosen.label, chosen.labelZh, language) })
      : t("cards.done")}</p>}
    {failure && <p className="attention-card__failure" role="alert">{failure.detail}</p>}
    <footer className="attention-card__foot">
      <Opens refs={card.opens} />
      <span className="attention-card__queue">
        {pending && <>
          <button type="button" className="menu-command" data-command="attention.queue.later" data-command-arg={card.id} disabled={busy !== null}
            onClick={() => act("later", () => laterCard(card.id))}>{t("cards.later")}</button>
          <button type="button" className="menu-command" data-command="attention.queue.dismiss" data-command-arg={card.id} disabled={busy !== null}
            onClick={() => act("dismiss", () => dismissCard(card.id))}>{t("cards.dismiss")}</button>
        </>}
        {card.state === "done" && card.resolution?.undo && <button type="button" className="menu-command" data-command="attention.queue.undo" data-command-arg={card.id} disabled={busy !== null}
          onClick={() => act("undo", () => undoCard(card.id))}>{t("cards.undo")}</button>}
        {(card.state === "dismissed" || card.state === "later") && <button type="button" className="menu-command" data-command="attention.queue.reopen" data-command-arg={card.id} disabled={busy !== null}
          onClick={() => act("reopen", () => reopenCard(card.id))}>{t("cards.reopen")}</button>}
      </span>
    </footer>
  </article>;
}

/** Two results side by side, one question with a few answers, or a large change shown as a plan first. */
export function DecisionCard(props: CardProps) {
  return <CardFrame {...props} />;
}

/** A version waiting on its Board frame, or the changes the marks were read as (their list joins with 3-3). */
export function MarkupCard(props: CardProps) {
  return props.card.kind === "change_list" && props.card.changeList ? <ChangeListCard {...props} /> : <CardFrame {...props} />;
}

/**
 * The changes the marks were read as (3-4): each with what the agent understood and what it would touch, ticked unless
 * it was unsure; the architect unticks what is wrong, and the card runs the rest as one change.
 */
function ChangeListCard(props: CardProps) {
  const { language } = usePreferences();
  const t = useT();
  const { card } = props;
  const list = card.changeList!;
  const [ticked, setTicked] = useState<ReadonlySet<number>>(() => new Set(list.items.filter((item) => item.keep !== false && !item.unsure).map((item) => item.n)));
  const chosen = list.items.filter((item) => ticked.has(item.n)).map((item) => item.n);
  const toggle = (n: number) => setTicked((current) => { const next = new Set(current); if (!next.delete(n)) next.add(n); return next; });
  const understood = (item: ChangeItem) => (language === "zh-CN" ? item.understandingZh : null) ?? item.understanding;
  const pending = isPending(card);
  return <CardFrame {...props} items={chosen} blocked={(option) => (option === "run" && chosen.length === 0 ? t("cards.changes.none") : null)}>
    {card.changeWords && card.changeWords.length > 0 && <blockquote className="attention-card__words-said">{card.changeWords.join(" ")}</blockquote>}
    <ol className="attention-card__changes" aria-label={t("cards.changes.label")}>{list.items.map((item) => <li key={item.n} data-unsure={item.unsure || undefined} data-ticked={ticked.has(item.n) || undefined}>
      <label>
        <input type="checkbox" checked={ticked.has(item.n)} disabled={!pending} onChange={() => toggle(item.n)} />
        <span className="attention-card__change-n">{item.n}</span>
        <span className="attention-card__change-text">
          <b>{understood(item)}</b>
          <small>{[item.unsure ? t("cards.changes.unsure") : null, item.targets?.length ? t("cards.changes.touches", { count: item.targets.length }) : null,
            item.protected?.length ? t("cards.changes.keeps", { count: item.protected.length }) : null, item.extra ? t("cards.changes.extra") : null].filter(Boolean).join(" · ")}</small>
        </span>
      </label>
    </li>)}</ol>
  </CardFrame>;
}

/** A question with at most three answers, or something the system would do and has not done. */
export function GuessCard(props: CardProps) {
  return <CardFrame {...props} />;
}

/** Finished, failed, or handled for the person and open to undo. */
export function NoticeCard(props: CardProps) {
  return <CardFrame {...props} compact />;
}

/** The card for a queue entry, by the channel it belongs to. */
export function AttentionCardView(props: CardProps) {
  switch (props.card.channel) {
    case "decide": return <DecisionCard {...props} />;
    case "annotate": return <MarkupCard {...props} />;
    case "guess": return <GuessCard {...props} />;
    default: return <NoticeCard {...props} />;
  }
}
