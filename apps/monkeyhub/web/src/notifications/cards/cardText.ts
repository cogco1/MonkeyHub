/** What a card says and in which order, apart from how it is drawn. */

import type { Language } from "../../../../../../packages/web-shared/src/appearance.js";
import type { AttentionCard, AttentionSummary, CardMedia, CardOption, CardState } from "./types";

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
  option.action.commandId === "chat.composer.send" && !("content" in option.action.args);

/** The answer a handled card was given, if it names one of its options. */
export function chosenOption(card: Pick<AttentionCard, "options" | "resolution">): CardOption | null {
  const id = card.resolution?.optionId;
  return id ? card.options.find((option) => option.id === id) ?? null : null;
}

/** The command that opens a decision card's results side by side, the card under them (2-5). */
export const COMPARE_DECIDE = "candidate.compare.decide";

/** The runs a decision card opens side by side now: the first is set against the last. */
/**
 * A decision's pictures: each model view the project's thumbnails can draw (it names its exact model), in the
 * writer's order (A, then B), with the answer choosing it gives; null where the picture is no answer.
 */
export function cardPictures(card: Pick<AttentionCard, "media" | "options">): { media: CardMedia; option: CardOption | null }[] {
  return card.media.filter((item) => item.kind === "model_view" && item.modelSource)
    .map((item) => ({ media: item, option: card.options.find((row) => row.id === item.optionId) ?? null }));
}

export function comparedRuns(card: Pick<AttentionCard, "opens">): string[] {
  const runIds = card.opens.find((ref) => ref.commandId === COMPARE_DECIDE)?.args.runIds;
  return Array.isArray(runIds) ? runIds.filter((value) => typeof value === "string" && value.length > 0) : [];
}

/** What each answer of a card does: when it changes, an answer read beside the old comparison would do something else. */
export const answersKey = (card: Pick<AttentionCard, "options">) =>
  JSON.stringify(card.options.map((option) => [option.id, option.action.commandId, option.action.args]));

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
