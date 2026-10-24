import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { usePreferences } from "../../features/settings/preferences";

import type { MessageKey } from "../../i18n/messages.en";
import { useT } from "../../i18n/useT";
import { bulkRecommended, cardsIn, queueSnapshot, subscribeQueue, undoBulk, type QueueFailure, type QueueSnapshot } from "../queue";
import { AttentionCardView } from "./AttentionCards";
import { isPending, queueOrder, say } from "./cardText";
import type { AttentionCard, BulkResult, CardState } from "./types";

/** How long a card that just arrived stays marked: the ring's own animation (cards.css). */
const FRESH_MS = 2400;
/** The most cards the Hub takes in one All as recommended. */
const BULK_MAX = 50;
/** How many of their titles the button lists beside it. */
const BULK_NAMED = 3;

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
  // Each mark lasts its own ring, and a card that left the list and comes back is marked again.
  const listed = useRef<Set<string> | null>(null);
  const timers = useRef(new Set<number>());
  const [fresh, setFresh] = useState<ReadonlySet<string>>(() => new Set());
  useEffect(() => {
    const ids = new Set(queue.cards.map((card) => card.id));
    const arrived = listed.current ? [...ids].filter((id) => !listed.current!.has(id)) : [];
    listed.current = ids;
    if (!arrived.length) return;
    setFresh((current) => new Set([...current, ...arrived]));
    const timer = window.setTimeout(() => {
      timers.current.delete(timer);
      setFresh((current) => new Set([...current].filter((id) => !arrived.includes(id))));
    }, FRESH_MS);
    timers.current.add(timer);
  }, [queue.cards]);
  useEffect(() => () => { for (const timer of timers.current) window.clearTimeout(timer); }, []);
  // An empty queue is said by the page's heading.
  if (!cards.length) return <AllAsRecommended cards={cards} projectName={projectName} />;
  return <div className="attention-queue">
    <AllAsRecommended cards={cards} projectName={projectName} />
    {cards.map((card) => <AttentionCardView key={card.id} card={card} project={projectName(card.projectDir)} fresh={fresh.has(card.id)} />)}
  </div>;
}

/** A pending decision whose recommended answer the Hub can take back: All as recommended answers only those. */
const recommendable = (card: AttentionCard) => isPending(card) && card.channel === "decide" && card.recommendation != null
  && card.options.some((option) => option.id === card.recommendation!.optionId && option.undoable);

/**
 * Two or more decisions whose recommendation the Hub can take back are answered at once, and taken back as one.
 * The button names the first few, wherever they are in the list, and their projects when it spans more than one,
 * so an answer in one project does not change another unnoticed. Afterwards a line says what was done, and what an
 * undo could not take back. Two new ones bring the button back.
 */
function AllAsRecommended({ cards, projectName }: { cards: readonly AttentionCard[]; projectName(projectDir: string): string | null }) {
  const t = useT();
  const { language } = usePreferences();
  const [done, setDone] = useState<BulkResult | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<QueueFailure | null>(null);
  const recommended = cards.filter(recommendable).slice(0, BULK_MAX);
  const offered = recommended.length >= 2;
  if (!offered && !done && !said && !failure) return null;
  const answer = () => {
    setBusy(true); setFailure(null); setSaid(null);
    void bulkRecommended(recommended.map((card) => card.id)).then(setDone, (cause: QueueFailure) => setFailure(cause)).finally(() => setBusy(false));
  };
  const takeBack = (batch: BulkResult) => {
    setBusy(true); setFailure(null);
    void undoBulk(batch.bulkId, batch.items.map((item) => item.id)).then((result) => {
      const moved = result.skipped.filter((row) => row.code === "ATTENTION_UNDO_MOVED").length;
      setSaid(t("cards.bulkUndone", { count: result.items.length })
        + (moved ? t("cards.bulkUndoMoved", { count: moved }) : "")
        + (result.skipped.length > moved ? t("cards.bulkSkipped", { count: result.skipped.length - moved }) : ""));
      setDone(null);
    }, (cause: QueueFailure & { unserved?: boolean }) => {
      // Taken back one by one since, the batch is gone from the Hub.
      if (cause.unserved || cause.code === "ATTENTION_BULK_NOT_FOUND") { setDone(null); setSaid(t("cards.bulkUndoGone")); }
      else setFailure(cause);
    }).finally(() => setBusy(false));
  };
  const projects = new Set(recommended.map((card) => card.projectDir));
  const named = recommended.slice(0, BULK_NAMED).map((card) => {
    const title = say(card.title, card.titleZh, language), project = projects.size > 1 ? projectName(card.projectDir) : null;
    return project ? t("cards.bulkNameInProject", { project, title }) : title;
  }).join(t("cards.bulkJoin"));
  return <div className="attention-queue__bulk" role="group" aria-label={t("cards.bulkGroup")}>
    {offered ? <>
      <button type="button" className="btn" data-command="attention.queue.bulk_recommended" disabled={busy} onClick={answer}>
        {t("cards.bulk", { count: recommended.length })}</button>
      <span className="attention-queue__bulk-names">{recommended.length > BULK_NAMED
        ? t("cards.bulkNamesMore", { names: named, count: recommended.length }) : t("cards.bulkNames", { names: named })}
        {projects.size > 1 && t("cards.bulkProjects", { count: projects.size })}</span>
    </> : done ? <>
      <span role="status">{t("cards.bulkDone", { count: done.items.length })}{done.skipped.length > 0 && t("cards.bulkSkipped", { count: done.skipped.length })}</span>
      <button type="button" className="menu-command" data-command="attention.queue.bulk_undo" disabled={busy} onClick={() => takeBack(done)}>{t("cards.undo")}</button>
    </> : said && <span role="status">{said}</span>}
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
