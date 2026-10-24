import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import type { MessageKey } from "../../i18n/messages.en";
import { useT } from "../../i18n/useT";
import { bulkRecommended, cardsIn, queueSnapshot, subscribeQueue, undoBulk, type QueueFailure, type QueueSnapshot } from "../queue";
import { AttentionCardView } from "./AttentionCards";
import { isPending, queueOrder } from "./cardText";
import type { AttentionCard, BulkResult, CardState } from "./types";

/** How long a card that just arrived stays marked: the ring's own animation (cards.css). */
const FRESH_MS = 2400;

/** This window's copy of the attention queue, read once for every reader on the page. */
export function useAttentionQueue(): QueueSnapshot {
  return useSyncExternalStore(subscribeQueue, queueSnapshot);
}

/** The queue across projects: what needs the person first, then what is put off or under way. */
export function AttentionQueue({ projectName }: { projectName(projectDir: string): string | null }) {
  const queue = useAttentionQueue();
  const cards = queueOrder(queue.cards);
  // A card that arrives while the list is on screen is marked for a moment instead of raising a notice. The mark
  // follows readings of the queue only, so the shell redrawing for other reasons does not take it away early.
  const listed = useRef<Set<string> | null>(null);
  const [fresh, setFresh] = useState<ReadonlySet<string>>(() => new Set());
  useEffect(() => {
    const ids = queue.cards.map((card) => card.id);
    const arrived = listed.current ? ids.filter((id) => !listed.current!.has(id)) : [];
    listed.current = new Set([...(listed.current ?? []), ...ids]);
    if (arrived.length) setFresh(new Set(arrived));
  }, [queue.cards]);
  useEffect(() => {
    if (!fresh.size) return;
    const timer = window.setTimeout(() => setFresh(new Set()), FRESH_MS);
    return () => window.clearTimeout(timer);
  }, [fresh]);
  // An empty queue is said by the page's heading.
  if (!cards.length) return <AllAsRecommended cards={cards} />;
  return <div className="attention-queue">
    <AllAsRecommended cards={cards} />
    {cards.map((card) => <AttentionCardView key={card.id} card={card} project={projectName(card.projectDir)} fresh={fresh.has(card.id)} />)}
  </div>;
}

/** Two or more decisions with a recommendation can be answered at once, and taken back as one. */
function AllAsRecommended({ cards }: { cards: readonly AttentionCard[] }) {
  const t = useT();
  const [done, setDone] = useState<BulkResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<QueueFailure | null>(null);
  const recommended = cards.filter((card) => isPending(card) && card.channel === "decide" && card.recommendation);
  if (!done && recommended.length < 2) return null;
  const act = (action: () => Promise<BulkResult>, after: (result: BulkResult) => BulkResult | null) => {
    setBusy(true); setFailure(null);
    void action().then((result) => setDone(after(result)), (cause: QueueFailure) => setFailure(cause)).finally(() => setBusy(false));
  };
  return <div className="attention-queue__bulk" role="group" aria-label={t("cards.bulkGroup")}>
    {done ? <>
      <span role="status">{t("cards.bulkDone", { count: done.items.length })}{done.skipped.length > 0 && t("cards.bulkSkipped", { count: done.skipped.length })}</span>
      <button type="button" className="menu-command" data-command="attention.queue.bulk_undo" disabled={busy}
        onClick={() => act(() => undoBulk(done.bulkId, done.items.map((item) => item.id)), () => null)}>{t("cards.undo")}</button>
    </> : <button type="button" className="btn" data-command="attention.queue.bulk_recommended" disabled={busy}
      onClick={() => act(() => bulkRecommended(recommended.map((card) => card.id)), (result) => result)}>{t("cards.bulk", { count: recommended.length })}</button>}
    {failure && <p className="attention-card__failure" role="alert">{failure.detail}</p>}
  </div>;
}

/** Cards the queue no longer holds, newest first: handled ones with their undo, set-aside ones to bring back. */
export function CardHistory({ states, empty, projectName }: {
  states: readonly CardState[]; empty: MessageKey; projectName(projectDir: string): string | null;
}) {
  const queue = useAttentionQueue();
  const t = useT();
  const [cards, setCards] = useState<AttentionCard[] | "unread" | null>(null);
  const key = states.join(",");
  // Read again whenever the queue changes: an answer or a set-aside moves a card here.
  useEffect(() => {
    let current = true;
    void cardsIn(key.split(",") as CardState[]).then((rows) => { if (current) setCards(queueOrder(rows)); }, () => { if (current) setCards("unread"); });
    return () => { current = false; };
  }, [key, queue]);
  if (cards === null) return null;
  if (cards === "unread") return <p className="attention-queue__empty" role="status">{t("cards.unread")}</p>;
  if (!cards.length) return <p className="attention-queue__empty">{t(empty)}</p>;
  return <div className="attention-queue">{cards.map((card) =>
    <AttentionCardView key={card.id} card={card} project={projectName(card.projectDir)} />)}</div>;
}
