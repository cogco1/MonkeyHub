import { useEffect, useState, useSyncExternalStore } from "react";

import type { MessageKey } from "../../i18n/messages.en";
import { useT } from "../../i18n/useT";
import { cardsIn, queueSnapshot, subscribeQueue, type QueueSnapshot } from "../queue";
import { AttentionCardView } from "./AttentionCards";
import { queueOrder } from "./cardText";
import type { AttentionCard, CardState } from "./types";

/** This window's copy of the attention queue, read once for every reader on the page. */
export function useAttentionQueue(): QueueSnapshot {
  return useSyncExternalStore(subscribeQueue, queueSnapshot);
}

/** The queue across projects: what needs the person first, then what is put off or under way. */
export function AttentionQueue({ projectName }: { projectName(projectDir: string): string | null }) {
  const queue = useAttentionQueue();
  const cards = queueOrder(queue.cards);
  // An empty queue is said by the page's heading.
  if (!cards.length) return null;
  return <div className="attention-queue">{cards.map((card) =>
    <AttentionCardView key={card.id} card={card} project={projectName(card.projectDir)} />)}</div>;
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
