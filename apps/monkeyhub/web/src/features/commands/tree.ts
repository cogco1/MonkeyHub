/**
 * The command tree a person searches with Ctrl+K and browses in the advanced tools:
 * surface → group → command. A capability of governance/module_registry.json lists its
 * commands in `ui.commands`; `tree.generated.ts` is the view `devctl render-map` writes
 * from the registry, so a person and an agent search the same entries. This file holds
 * only the shapes, so the pure modules beside it and their tests can import it without a
 * bundler.
 */

/** Where a command lives once the new shell is the default: on screen, in the side panel, or done by the machine. */
export type CommandChannel = "front" | "side" | "background";

/** Basic and advanced commands are both found by Ctrl+K; developer ones only in developer mode. */
export type CommandTier = "basic" | "advanced" | "developer";

export type Bilingual = { readonly en: string; readonly "zh-CN": string };

/** One capability that lists commands. */
export interface CommandCapability {
  readonly id: string;
  readonly owner: string;
  readonly kind: string;
  readonly status: string;
  /** Who performs it: the Hub, or the project's Runtime. */
  readonly host: string;
  readonly purpose: Bilingual;
  /** The registry's goals and aliases, both languages: what the capability is searched by. */
  readonly terms: readonly string[];
}

export interface Command {
  /** Also the name the surface that performs the command registers its handler under. */
  readonly id: string;
  /** The capability this command belongs to. */
  readonly capability: string;
  readonly label: Bilingual;
  /** The command's own aliases, beside its capability's terms. */
  readonly terms: readonly string[];
  /** The surface that has to be open for the command to run; "hub" needs none. */
  readonly surface: string;
  /** The advanced-tools group the command is listed under. */
  readonly group: string;
  readonly channel: CommandChannel;
  readonly tier: CommandTier;
  /** Its one standing place, if it has one; the palette and shortcuts are not places. */
  readonly place: string | null;
  /** The conditions it needs (the capability's requires, the Runtime's feature flags), read once they are wired. */
  readonly when: readonly string[];
  readonly shortcut: string | null;
  /** The entry-audit numbers this command answers for (e.g. 模18). */
  readonly entries: readonly string[];
  /** Arguments a run needs (a card or a generated row supplies them); a command that needs any is not listed bare. */
  readonly args: readonly string[];
}

export interface CommandTree {
  readonly capabilities: readonly CommandCapability[];
  readonly commands: readonly Command[];
}
