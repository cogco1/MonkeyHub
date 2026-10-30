/**
 * The skills installed on this computer, as a project's Runtime lists them
 * (S-1), hand-typed until the generated SDK carries them.
 *
 * A skill is a folder in the skill folders: the ones that come with the Hub and
 * the person's own. Every project can use them without importing them, and a
 * project keeps the exact version each of its runs used. What a skill may do is
 * allowed by a person in the Hub, not here. Every call goes through the
 * project's own connection, so the Hub's forwarding prefix and token apply
 * exactly as they do to every other Studio call, and every refusal arrives as
 * the runtime's own `StudioApiError` with its `code` and `detail`.
 */

import type { ServerConnection } from "./connection";
import { call } from "./error";

/**
 * What a skill declares it needs. `"*"` in a list means any. A runtime that
 * sends only some keys leaves the rest undeclared.
 */
export interface SkillAccess {
  readonly programs?: readonly string[];
  readonly network?: boolean;
  readonly ssh?: readonly string[];
  readonly read?: readonly string[];
  readonly write?: readonly string[];
  readonly packages?: readonly string[];
}

export type SkillPackageSource = "bundled" | "user";

/**
 * One row of `GET /api/skills/library`: a package folder and what this project
 * keeps of its skill. A package that cannot be read carries only its source,
 * folder, path and `error`.
 */
export interface SkillPackage {
  readonly source: SkillPackageSource;
  /** The package folder, e.g. `site-palette`. */
  readonly name: string;
  readonly path: string;
  /** A package the user's folder holds under the same name is the one used. */
  readonly shadowed?: boolean;
  readonly skillId?: string;
  readonly skillName?: string;
  readonly description?: string;
  readonly entry?: string | null;
  /** Null when the package declares nothing. */
  readonly access?: SkillAccess | null;
  readonly codeSha256?: string | null;
  readonly files?: readonly string[];
  /** The version this project keeps from its runs; null when none ran here yet. */
  readonly projectVersion?: number | null;
  /** True when that version says exactly what the package says. */
  readonly upToDate?: boolean;
  readonly error?: { readonly code: string; readonly detail: string };
}

export interface SkillLibrary {
  readonly projectId: string;
  /** Source → the folder its packages are read from. */
  readonly sources: Readonly<Record<SkillPackageSource, string>>;
  readonly packages: readonly SkillPackage[];
}

export type SkillsApi = ReturnType<typeof createSkillsApi>;

export function createSkillsApi(connection: ServerConnection) {
  const client = connection.client;
  return {
    library(signal?: AbortSignal): Promise<SkillLibrary> {
      return call("GET /api/skills/library", client.get<{ 200: SkillLibrary }>({ url: "/api/skills/library", signal }));
    },
  };
}

/** One package's key in a list. */
export const packageKey = (row: Pick<SkillPackage, "source" | "name">) => `${row.source}/${row.name}`;

/** The first 12 characters of a code hash, as the list shows it. */
export function shortHash(sha256: string): string {
  return sha256.slice(0, 12);
}
