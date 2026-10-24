/**
 * Ctrl+K's ranking: what a person typed, against the command tree. Pure, so the palette
 * and its tests read one rule.
 *
 * A palette query is a few characters typed toward a name ("导出", "elev", "3dm"), not a
 * sentence; whole sentences go to the conversation, and matching a sentence to a
 * capability stays with the capability index (`project_runtime.application.capability`).
 * So here every space-separated part of the query has to appear somewhere in the
 * command — its label in either language, its own terms or its id — and a hit in the label
 * counts for more than a hit in the terms. A capability's goals speak for its command only
 * when it has one: a capability with several commands lists every sibling's goal, and would
 * otherwise make each of them match the others' names.
 */

import type { Command, CommandCapability, CommandTree } from "./tree";

export type Language = "en" | "zh-CN";

export interface RankedCommand {
  readonly command: Command;
  readonly score: number;
  /** Shown above the rest because it was used recently. */
  readonly recent: boolean;
}

/** How many recently used commands lead an empty palette. */
export const RECENT_SHOWN = 5;

const fold = (text: string) => text.normalize("NFKC").toLocaleLowerCase();

/** The parts of a query; full-width spaces and punctuation separate them as ASCII ones do. */
export function queryParts(query: string): string[] {
  return fold(query).split(/[\s　,，、;；·]+/).filter(Boolean);
}

/** Places whose commands mean something only where they stand: the composer, a card, a list row, a dialog. */
const IN_PLACE = /^(?:composer|card|row)$|^dialog:/;

/**
 * Commands the palette offers at all: background ones are the machine's to do, one that
 * needs arguments is run from where they are known (a card, a row generated per
 * conversation), and developer ones wait for developer mode. One that only means something
 * where it stands (IN_PLACE) is offered while that entry is on screen, to point at it.
 */
export function searchable(tree: CommandTree, developer = false, onScreen: ReadonlySet<string> = new Set()): Command[] {
  return tree.commands.filter((command) => command.channel !== "background" && !command.args.length
    && (developer || command.tier !== "developer") && (!command.place || !IN_PLACE.test(command.place) || onScreen.has(command.id)));
}

function scorePart(part: string, command: Command, capability: CommandCapability | null, language: Language): number {
  const shown = fold(command.label[language]);
  const other = fold(command.label[language === "en" ? "zh-CN" : "en"]);
  if (shown.startsWith(part)) return 8;
  if (shown.includes(part)) return 6;
  if (other.startsWith(part)) return 5;
  if (other.includes(part)) return 4;
  if (command.terms.some((term) => fold(term).includes(part))) return 3;
  if (capability?.terms.some((term) => fold(term).includes(part))) return 2;
  if (fold(command.id).includes(part) || fold(command.capability).includes(part)) return 1;
  return 0;
}

/**
 * Commands ranked for a query. An empty query lists the recent ones first, then the rest
 * in tree order; a query keeps only commands matching every part, best first, and a recent
 * command wins a tie.
 */
export function rankCommands(
  tree: CommandTree, query: string, language: Language, recent: readonly string[] = [], developer = false,
  onScreen: ReadonlySet<string> = new Set(),
): RankedCommand[] {
  const size = new Map<string, number>();
  for (const command of tree.commands) size.set(command.capability, (size.get(command.capability) ?? 0) + 1);
  const single = new Map(tree.capabilities.filter((capability) => size.get(capability.id) === 1).map((capability) => [capability.id, capability]));
  const commands = searchable(tree, developer, onScreen);
  const recency = new Map(recent.map((id, index) => [id, index]));
  const parts = queryParts(query);
  if (!parts.length) {
    const lead = recent.map((id) => commands.find((command) => command.id === id)).filter((command): command is Command => !!command)
      .slice(0, RECENT_SHOWN);
    const led = new Set(lead.map((command) => command.id));
    return [
      ...lead.map((command) => ({ command, score: 0, recent: true })),
      ...commands.filter((command) => !led.has(command.id)).map((command) => ({ command, score: 0, recent: false })),
    ];
  }
  const ranked: (RankedCommand & { order: number })[] = [];
  commands.forEach((command, order) => {
    let score = 0;
    for (const part of parts) {
      const hit = scorePart(part, command, single.get(command.capability) ?? null, language);
      if (!hit) { score = 0; break; }
      score += hit;
    }
    if (score) ranked.push({ command, score, recent: recency.has(command.id), order });
  });
  const lastUse = (row: { command: Command }) => recency.get(row.command.id) ?? recent.length;
  return ranked.sort((a, b) => b.score - a.score || lastUse(a) - lastUse(b) || a.order - b.order)
    .map(({ command, score, recent: wasRecent }) => ({ command, score, recent: wasRecent }));
}
