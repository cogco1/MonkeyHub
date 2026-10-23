/**
 * The command tree a person searches with Ctrl+K and browses in the advanced tools:
 * surface → group → command. A group is one capability of governance/module_registry.json
 * and its commands are that capability's `ui.commands`; `tree.generated.ts` is the view
 * `scripts/command-tree.mjs` renders from the registry, so a person and an agent search
 * the same entries. This file holds only the shapes, so the pure modules beside it and
 * their tests can import it without a bundler.
 */

/** Where a command lives once the new shell is the default: on screen, in the side panel, or done by the machine. */
export type CommandChannel = "front" | "side" | "background";

export type Bilingual = { readonly en: string; readonly "zh-CN": string };

/** One capability as a group of commands. */
export interface CommandGroup {
  readonly id: string;
  readonly owner: string;
  readonly kind: string;
  readonly status: string;
  readonly purpose: Bilingual;
  /** The registry's goals and aliases, both languages: what the group is searched by. */
  readonly terms: readonly string[];
}

export interface Command {
  /** Also the name the surface that performs the command registers its handler under. */
  readonly id: string;
  /** The capability this command belongs to. */
  readonly group: string;
  readonly label: Bilingual;
  /** The command's own aliases, beside its group's terms. */
  readonly terms: readonly string[];
  /** The surface that has to be open for the command to run; "hub" needs none. */
  readonly surface: string;
  /** The advanced-tools section the command is listed under. */
  readonly section: string;
  readonly channel: CommandChannel;
  /** Its one standing place, if it has one; the palette and shortcuts are not places. */
  readonly place: string | null;
  readonly shortcut: string | null;
  /** The entry-audit numbers this command answers for (e.g. 模18). */
  readonly entries: readonly string[];
  /** Arguments a run needs (a card or a generated row supplies them); a command that needs any is not listed bare. */
  readonly args: readonly string[];
}

export interface CommandTree {
  readonly groups: readonly CommandGroup[];
  readonly commands: readonly Command[];
}
