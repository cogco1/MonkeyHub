import type { Language } from "../features/settings/preferences";

/**
 * The reader's language and developer switch, kept where code that builds a sentence outside a component can read
 * them (a failed save becomes text in a notice, not a component). The settings provider is the only writer.
 */
let words: { language: Language; developerMode: boolean } = { language: "en", developerMode: false };

export function rememberFailureWords(language: Language, developerMode: boolean): void {
  words = { language, developerMode };
}

export function currentFailureWords(): { readonly language: Language; readonly developerMode: boolean } {
  return words;
}
