/**
 * Ctrl+K's ranking: what a person typed, against the command tree. Pure, so the palette
 * and its tests read one rule.
 *
 * A palette query is usually a few characters typed toward a name ("导出", "elev", "3dm"), so
 * every space-separated part of the query has to appear somewhere in the command — its label in
 * either language, its own terms or its id — and a hit in the label counts for more than a hit in
 * the terms. A capability's goals speak for its command only when it has one: a capability with
 * several commands lists every sibling's goal, and would otherwise make each of them match the
 * others' names. What nobody types toward a name, a sentence ("把墙往外推一点"), is looked up in
 * the search index below, which the composer reads too.
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

/**
 * Commands the palette and the advanced tools offer at all: background ones are the machine's to do, one that
 * needs arguments is run from where they are known (a card, a row generated per conversation), one that means
 * something only where it stands (in place: a dialog's confirm, a card's button, the composer's Stop) is used
 * there, and developer ones wait for developer mode.
 */
export function searchable(tree: CommandTree, developer = false): Command[] {
  return tree.commands.filter((command) => command.channel !== "background" && !command.args.length && !command.inPlace
    && (developer || command.tier !== "developer"));
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
): RankedCommand[] {
  const size = new Map<string, number>();
  for (const command of tree.commands) size.set(command.capability, (size.get(command.capability) ?? 0) + 1);
  const single = new Map(tree.capabilities.filter((capability) => size.get(capability.id) === 1).map((capability) => [capability.id, capability]));
  const commands = searchable(tree, developer);
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
  if (!ranked.length && parts.length !== 2) {
    // Nothing has every part: a sentence ("把墙往外推一点", "make the roof flatter") is looked up in the search
    // index instead. Two parts are two keywords the person combined, and stay strict.
    return searchCommands(indexOf(tree, commands), query).map((hit) => ({ command: hit.command, score: hit.score, recent: recency.has(hit.command.id) }));
  }
  return ranked.sort((a, b) => b.score - a.score || lastUse(a) - lastUse(b) || a.order - b.order)
    .map(({ command, score, recent: wasRecent }) => ({ command, score, recent: wasRecent }));
}

// ---- The search index for what people say rather than type toward a name.

/**
 * The command search index (08 提速): what a person says, matched to the commands that do it, without asking a
 * model. Each command is indexed under its labels, its capability's goals, and the phrasings and aliases written
 * for it ahead of time (governance/command_phrases.json, rendered into the command tree). A search only looks
 * things up: BM25 over Chinese character pairs and English words, with a bonus for a whole alias or phrasing said
 * verbatim. Building the index is done once per tree; a search over the whole tree takes well under a millisecond.
 *
 * The same rule ranks the agent's capability index (project_runtime.application.capability); both test suites
 * read governance/command_search_cases.json, so the two cannot drift apart.
 */


/** Words too common to mean anything on their own; the same list as the Runtime's capability index. */
const STOPWORDS = new Set([
  "a", "an", "and", "any", "are", "as", "at", "be", "by", "can", "do", "for", "from", "how",
  "i", "in", "into", "is", "it", "its", "me", "my", "of", "on", "one", "or", "please", "that",
  "the", "then", "there", "this", "to", "up", "want", "was", "we", "what", "with", "would",
  "的", "了", "把", "个", "这", "那", "和", "与", "请", "帮", "我", "在", "是", "要", "给", "它",
]);
/** Two-character words as common as those: asking how, how much, a little. */
const STOP_PAIRS = new Set(["怎么", "什么", "怎样", "如何", "一下", "一点", "一个", "一张", "可以", "哪个", "这样", "那样", "看看", "能否"]);
/** A Latin token counts from three letters; a Chinese term is a two-character run. */
const MIN_LATIN = 3;
const CJK = /[㐀-䶿一-鿿぀-ヿ]/;
const LATIN = /[a-z0-9]+/g;

/** The terms of a text: Latin words and Chinese character pairs, the stopwords left out. */
export function searchTerms(text: string): string[] {
  const folded = text.normalize("NFKC").toLocaleLowerCase();
  const out = (folded.match(LATIN) ?? []).filter((token) => token.length >= MIN_LATIN && !STOPWORDS.has(token));
  let run = "";
  const flush = () => {
    for (let index = 0; index + 1 < run.length; index += 1) {
      const pair = run.slice(index, index + 2);
      if (!STOPWORDS.has(pair[0]!) && !STOPWORDS.has(pair[1]!) && !STOP_PAIRS.has(pair)) out.push(pair);
    }
    run = "";
  };
  for (const character of folded) {
    if (CJK.test(character)) run += character;
    else if (run) flush();
  }
  if (run) flush();
  return out;
}

/** How much each part of a command counts: what it is called most, what was written about it least. */
const FIELD_WEIGHT = { label: 3, alias: 2.5, phrase: 1.5, goal: 0.5 } as const;
const K1 = 1.2;
const B = 0.75;
/** Kept are hits scoring at least this share of the best one, matching at least this share of what was said. */
export const EVIDENCE_SHARE = 0.25;

interface Document {
  readonly command: Command;
  readonly weights: ReadonlyMap<string, number>;
  readonly length: number;
  /** Labels, aliases and phrasings, folded, for a whole one said verbatim. */
  readonly whole: readonly string[];
}

export interface CommandIndex {
  readonly documents: readonly Document[];
  readonly frequency: ReadonlyMap<string, number>;
  readonly averageLength: number;
}

export interface SearchHit {
  readonly command: Command;
  readonly score: number;
  /** The share of what was said that this command matched, each word weighed by how rare it is: a common word is
   * little evidence. */
  readonly coverage: number;
  /** A whole label, alias or phrasing of the command was said. */
  readonly verbatim: boolean;
}

const foldSaid = (text: string) => text.normalize("NFKC").toLocaleLowerCase().replace(/\s+/g, " ").trim();

/** Index the given commands, each under its own words and its capability's goals. */
export function buildIndex(commands: readonly Command[], capabilities: readonly CommandCapability[]): CommandIndex {
  // A capability's goals speak for its command only when it has one, as in the palette's own ranking.
  const size = new Map<string, number>();
  for (const command of commands) size.set(command.capability, (size.get(command.capability) ?? 0) + 1);
  const goals = new Map(capabilities.filter((capability) => size.get(capability.id) === 1).map((capability) => [capability.id, capability.terms]));
  const frequency = new Map<string, number>();
  const documents = commands.map((command) => {
    const weights = new Map<string, number>();
    let length = 0;
    const add = (texts: readonly string[], weight: number) => {
      for (const text of texts) for (const term of searchTerms(text)) {
        weights.set(term, (weights.get(term) ?? 0) + weight);
        length += weight;
      }
    };
    const labels = [command.label.en, command.label["zh-CN"]];
    const aliases = [...command.terms, ...(command.aliases ?? [])];
    const phrases = command.phrases ?? [];
    add(labels, FIELD_WEIGHT.label);
    add(aliases, FIELD_WEIGHT.alias);
    add(phrases, FIELD_WEIGHT.phrase);
    add(goals.get(command.capability) ?? [], FIELD_WEIGHT.goal);
    for (const term of weights.keys()) frequency.set(term, (frequency.get(term) ?? 0) + 1);
    return { command, weights, length, whole: [...labels, ...aliases, ...phrases].map(foldSaid).filter((text) => text.length >= 2) };
  });
  const averageLength = documents.reduce((sum, document) => sum + document.length, 0) / Math.max(documents.length, 1);
  return { documents, frequency, averageLength };
}

/** The commands what was said is about, best first; ones that share only a common word with it are left out. */
export function searchCommands(index: CommandIndex, query: string, limit = 20): SearchHit[] {
  const asked = [...new Set(searchTerms(query))];
  const said = foldSaid(query);
  if (!said) return [];
  const count = index.documents.length;
  const idf = (term: string) => { const df = index.frequency.get(term) ?? 0; return Math.log(1 + (count - df + 0.5) / (df + 0.5)); };
  const rarity = (term: string) => { const df = Math.max(index.frequency.get(term) ?? 0, 1); return Math.log(1 + (count - df + 0.5) / (df + 0.5)); };
  // A word no command was written with counts as if one were: rare in a large index, ordinary in a small one.
  const total = asked.reduce((sum, term) => sum + rarity(term), 0);
  const scored: SearchHit[] = [];
  for (const document of index.documents) {
    let score = 0, matched = 0;
    for (const term of asked) {
      const weight = document.weights.get(term);
      if (!weight) continue;
      matched += rarity(term);
      score += idf(term) * (weight * (K1 + 1)) / (weight + K1 * (1 - B + B * document.length / index.averageLength));
    }
    // Said verbatim: the whole of what was said is a label, alias or phrasing, one of two terms or more is in it, or
    // it is in one. A single common word inside a sentence is not.
    const verbatim = document.whole.some((text) => text === said || (said.includes(text) && searchTerms(text).length >= 2)
      || (said.length >= 4 && text.includes(said)));
    if (verbatim) score += 10;
    if (score <= 0) continue;
    scored.push({ command: document.command, score, coverage: total ? matched / total : verbatim ? 1 : 0, verbatim });
  }
  scored.sort((a, b) => b.score - a.score || a.command.id.localeCompare(b.command.id));
  const best = scored[0]?.score ?? 0;
  return scored.filter((hit) => hit.score >= best * EVIDENCE_SHARE && (hit.verbatim || hit.coverage >= EVIDENCE_SHARE)).slice(0, limit);
}

/** Longer than this, a sentence is a request for the conversation, not a command to find. */
const ROUTE_MAX = 40;
/** How much of what was said the one command has to match to be offered in place of the conversation. */
const ROUTE_COVERAGE = 0.6;

/**
 * The one command a sentence clearly asks for, or null: the command matches most of what was said, and no other
 * comes close. A sentence that only contains a command's name among other wishes goes to the conversation.
 */
export function routeSentence(index: CommandIndex, sentence: string): SearchHit | null {
  const said = foldSaid(sentence);
  if (!said || said.length > ROUTE_MAX) return null;
  const [first, second] = searchCommands(index, said, 2);
  if (!first) return null;
  const clear = first.coverage >= ROUTE_COVERAGE;
  const alone = !second || first.score >= second.score * 1.5;
  return clear && alone ? first : null;
}

const indexes = new WeakMap<CommandTree, CommandIndex>();
/** The index of a tree's commands, built once per tree. */
export function indexOf(tree: CommandTree, commands: readonly Command[]): CommandIndex {
  let index = indexes.get(tree);
  if (!index || index.documents.length !== commands.length) {
    index = buildIndex(commands, tree.capabilities);
    indexes.set(tree, index);
  }
  return index;
}
