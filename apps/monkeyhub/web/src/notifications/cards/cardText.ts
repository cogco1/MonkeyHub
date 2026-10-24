/** What a card says and in which order, apart from how it is drawn. */

import type { Language } from "../../../../../../packages/web-shared/src/appearance.js";
import type { AttentionCard, AttentionSummary, CardOption, CardState } from "./types";

/** A card's text in the reader's language: the Chinese sibling when there is one, the English text otherwise. */
export const say = (text: string, zh: string | null | undefined, language: Language): string =>
  language === "zh-CN" && zh ? zh : text;

/** Cards that need a person now, counted in "N need you". */
export const PENDING: readonly CardState[] = ["open", "review"];
export const isPending = (card: Pick<AttentionCard, "state">) => PENDING.includes(card.state);

/** The recommended answer first; the rest as the card's writer ordered them. */
export function orderedOptions(card: Pick<AttentionCard, "options">): CardOption[] {
  return [...card.options.filter((option) => option.recommended), ...card.options.filter((option) => !option.recommended)];
}

/** An answer that sends a message the card does not write itself: the person's words go with it (the resolve's note). */
export const needsWords = (option: Pick<CardOption, "action">) =>
  option.action.commandId === "chat.send_message" && !("content" in option.action.args);

/** The answer a handled card was given, if it names one of its options. */
export function chosenOption(card: Pick<AttentionCard, "options" | "resolution">): CardOption | null {
  const id = card.resolution?.optionId;
  return id ? card.options.find((option) => option.id === id) ?? null : null;
}

/** Cards waiting in a project; null while the queue's counts are not known. */
export function pendingFor(summary: AttentionSummary | null, projectDir: string): number | null {
  if (!summary) return null;
  return summary.projects.find((row) => row.projectDir === projectDir)?.open ?? 0;
}

/** The queue's newest first; a card waiting on the person before one that is put off or under way. */
export function queueOrder(cards: readonly AttentionCard[]): AttentionCard[] {
  const rank = (card: AttentionCard) => (isPending(card) ? 0 : card.state === "working" ? 1 : 2);
  return [...cards].sort((a, b) => rank(a) - rank(b) || b.updatedAt.localeCompare(a.updatedAt));
}
