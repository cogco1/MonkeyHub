/**
 * A sentence matched by what it means (08 提速, 向量检索): the Hub's POST /api/commands/meaning runs a
 * small local model over the commands' names and phrasings. The keyword search in search.ts is asked
 * first and stays the fallback; this module asks the Hub and decides what an answer may offer here.
 * Kept free of other src imports' values, like search.ts, so node tests load it directly.
 */
import type { CommandMeaningDto } from "../../api/generated";
import type { Command, CommandTree } from "./tree";

/** Ctrl K lists a meaning hit from this score on, below its keyword rows; the Hub alone decides which sentence to offer whole. */
export const MEANING_LIST = 0.6;
/** Rows of meaning hits Ctrl K adds. */
export const MEANING_ROWS = 3;
/** How long the composer waits for the answer before the sentence goes to the conversation as written. */
export const MEANING_WAIT_MS = 800;

/** The Hub's answer, or null when it has none in time (no model, still loading, slow): keyword search then stands alone. */
export async function askMeaning(text: string, limit = 5, waitMs = MEANING_WAIT_MS): Promise<CommandMeaningDto | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), waitMs);
  try {
    const response = await fetch("/api/commands/meaning", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, limit }), signal: controller.signal,
    });
    if (!response.ok) return null;
    const answer = (await response.json()) as CommandMeaningDto;
    return answer.state === "ready" ? answer : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** One value an argument can take, named as the person knows it. */
export interface ArgChoice { readonly value: string; readonly label: string }
/** The arguments a sentence can name or the confirm card can ask for, by argument name; any other argument is not offered. */
export type ArgChoices = Readonly<Record<string, readonly ArgChoice[]>>;

/** What a confirmed sentence runs: the command, the arguments the sentence named, and the one the card still asks. */
export interface Offer {
  readonly command: Command;
  readonly args: Readonly<Record<string, string>>;
  readonly missing: { readonly name: string; readonly choices: readonly ArgChoice[] } | null;
}

const fold = (text: string) => text.normalize("NFKC").toLocaleLowerCase().replace(/\s+/g, "");

/**
 * The offer for a command a sentence means, or null when it cannot be offered here: a command that only
 * means something where it stands, a developer command outside developer mode, an argument nobody can
 * choose, or more than one argument still to ask (the card asks one thing).
 */
export function offerFor(tree: CommandTree, id: string, sentence: string, choices: ArgChoices, developer: boolean): Offer | null {
  const command = tree.commands.find((item) => item.id === id);
  if (!command || command.inPlace || (command.tier === "developer" && !developer)) return null;
  const said = fold(sentence);
  const args: Record<string, string> = {};
  let missing: Offer["missing"] = null;
  for (const name of command.args) {
    const options = choices[name];
    if (!options?.length) return null;
    // The longest name the sentence contains, so "Housing 13 户" wins over "Housing".
    const named = options.filter((option) => fold(option.label).length >= 2 && said.includes(fold(option.label)))
      .sort((a, b) => b.label.length - a.label.length)[0];
    if (named) args[name] = named.value;
    else if (!missing) missing = { name, choices: options };
    else return null;
  }
  return { command, args, missing };
}

/** The meaning hits Ctrl K adds below its own rows: listed bare, not already shown, close enough to be worth a look. */
export function meaningRows(answer: CommandMeaningDto | null, tree: CommandTree, shown: ReadonlySet<string>, developer: boolean): Command[] {
  const out: Command[] = [];
  for (const hit of answer?.hits ?? []) {
    if (hit.score < MEANING_LIST || shown.has(hit.id)) continue;
    const command = tree.commands.find((item) => item.id === hit.id);
    if (!command || command.inPlace || command.args.length || (command.tier === "developer" && !developer)) continue;
    out.push(command);
    if (out.length >= MEANING_ROWS) break;
  }
  return out;
}
