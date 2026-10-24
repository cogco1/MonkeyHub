/**
 * The project's skills, the permissions a person grants them, and their runs,
 * hand-typed until the generated SDK carries them.
 *
 * A skill's code runs in the person's own environment, with only what a person
 * allowed on the Hub page: one run ("once") or this exact code until revoked
 * ("version"). A grant names a version and its code hash; changed code needs a
 * new grant. Every call goes through the project's own connection, so the Hub's
 * forwarding prefix, token and idempotency key apply exactly as they do to
 * every other Studio call, and every refusal arrives as the runtime's own
 * `StudioApiError` with its `code` and `detail`.
 *
 * `codeSha256`, `approved`, `runnable` and `access` on a list row are new; an
 * older runtime leaves them out, and a row without them is read as "not
 * reported", never as allowed.
 */

import type { ServerConnection } from "./connection";
import { call } from "./error";

/**
 * What a skill version declares it needs. `"*"` in a list means any. A runtime
 * that sends only some keys leaves the rest undeclared.
 */
export interface SkillAccess {
  readonly programs?: readonly string[];
  readonly network?: boolean;
  readonly ssh?: readonly string[];
  readonly read?: readonly string[];
  readonly write?: readonly string[];
  readonly packages?: readonly string[];
}

/** One row of `GET /api/skills`: the current version of one skill, without its body. */
export interface SkillRow {
  readonly id: string;
  readonly version: number;
  readonly name: string;
  readonly description: string;
  readonly codeSha256?: string | null;
  /** True while an active "this version always" grant matches the code. */
  readonly approved?: boolean;
  readonly runnable?: boolean;
  /** Null when the skill declares nothing; absent from an older runtime. */
  readonly access?: SkillAccess | null;
}

export interface SkillIndex {
  readonly projectId: string;
  readonly skills: readonly SkillRow[];
}

export type GrantScope = "once" | "version";
export type GrantStatus = "active" | "used" | "revoked";

/** One grant as the runtime retains it. */
export interface SkillApproval {
  readonly approvalId?: string;
  readonly skillId?: string;
  readonly version: number;
  readonly codeSha256: string;
  readonly scope?: GrantScope;
  readonly access?: SkillAccess | null;
  readonly method: string;
  readonly approvedAt: string;
  readonly approvedBy: { readonly actorId: string; readonly authenticated?: boolean; readonly origin: string };
}

/** One row of `GET /api/skills/approvals`. */
export interface SkillGrant extends SkillApproval {
  readonly approvalId: string;
  readonly skillId: string;
  readonly scope: GrantScope;
  readonly status: GrantStatus;
  readonly revokedAt?: string | null;
}

export interface SkillGrantIndex {
  readonly projectId: string;
  readonly approvals: readonly SkillGrant[];
}

export interface SkillRevocation {
  readonly approvalId: string;
  readonly revokedAt: string;
  readonly revokedBy: unknown;
}

/** `GET /api/skills/{id}?version=N`: one exact version, with its code. */
export interface SkillDetail {
  readonly id: string;
  readonly version: number;
  readonly latestVersion: number;
  readonly name: string;
  readonly description: string;
  readonly body: string;
  readonly manifest: unknown;
  readonly entry?: string | null;
  /** Relative path → text. */
  readonly files?: Readonly<Record<string, string>> | null;
  readonly codeSha256?: string | null;
  /** Null when the skill declares nothing; absent from an older runtime. */
  readonly access?: SkillAccess | null;
  readonly approvals?: readonly SkillApproval[];
}

/** The one body a person's grant from the Hub page sends. */
export interface SkillApprovalRequest {
  readonly projectId: string;
  readonly version: number;
  readonly codeSha256: string;
  readonly method: "hub-page";
  readonly scope: GrantScope;
}

/** Something a run tried that was not granted. */
export interface SkillRunNeed {
  readonly category: string;
  readonly detail: string;
}

/** One retained run of a skill; only what the Hub page reads is typed. */
export interface SkillRunRecord {
  readonly runId: string;
  readonly skillId?: string;
  readonly version?: number;
  readonly status: string;
  readonly failureCode?: string | null;
  readonly failureReason?: string | null;
  readonly needed?: readonly SkillRunNeed[] | null;
}

export interface SkillRunList {
  readonly projectId: string;
  /** Oldest first. */
  readonly runs: readonly SkillRunRecord[];
}

/** Where a package comes from: shipped with MonkeyHub's own `skills/`, or the user's skill-packages folder. */
export type SkillPackageSource = "bundled" | "user";

/**
 * One row of `GET /api/skills/library`: a package folder and what this project
 * holds of its skill. A package that cannot be read carries only its source,
 * folder, path and `error`.
 */
export interface SkillPackage {
  readonly source: SkillPackageSource;
  /** The package folder, e.g. `site-palette`. */
  readonly name: string;
  readonly path: string;
  readonly skillId?: string;
  readonly skillName?: string;
  readonly description?: string;
  readonly entry?: string | null;
  /** Null when the package declares nothing. */
  readonly access?: SkillAccess | null;
  readonly codeSha256?: string | null;
  readonly files?: readonly string[];
  /** The project's current version of this skill; null when the project does not hold it. */
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

export interface SkillImportRequest {
  readonly projectId: string;
  readonly source: SkillPackageSource;
  readonly name: string;
}

/** `imported: false` is a package that says what the current version says; no version was added. */
export interface SkillImportResult {
  readonly imported: boolean;
  readonly skill: SkillDetail;
}

export const HUB_PAGE_APPROVAL = "hub-page" as const;
export const SKILL_PERMISSION_NEEDED = "SKILL_PERMISSION_NEEDED";

export type SkillsApi = ReturnType<typeof createSkillsApi>;

export function createSkillsApi(connection: ServerConnection) {
  const client = connection.client;
  const json = { "Content-Type": "application/json" };
  return {
    list(signal?: AbortSignal): Promise<SkillIndex> {
      return call("GET /api/skills", client.get<{ 200: SkillIndex }>({ url: "/api/skills", signal }));
    },
    read(id: string, version: number, signal?: AbortSignal): Promise<SkillDetail> {
      return call("GET /api/skills/{id}", client.get<{ 200: SkillDetail }>({
        url: "/api/skills/{skill_id}", path: { skill_id: id }, query: { version }, signal,
      }));
    },
    approve(id: string, body: SkillApprovalRequest): Promise<SkillApproval> {
      return call("POST /api/skills/{id}/approvals", client.post<{ 201: SkillApproval }>({
        url: "/api/skills/{skill_id}/approvals", path: { skill_id: id }, body, headers: json,
      }));
    },
    /** Every grant of the project, active, used and revoked. */
    approvals(signal?: AbortSignal): Promise<SkillGrantIndex> {
      return call("GET /api/skills/approvals", client.get<{ 200: SkillGrantIndex }>({ url: "/api/skills/approvals", signal }));
    },
    revoke(approvalId: string, projectId: string): Promise<SkillRevocation> {
      return call("POST /api/skills/approvals/{id}/revoke", client.post<{ 200: SkillRevocation }>({
        url: "/api/skills/approvals/{approval_id}/revoke", path: { approval_id: approvalId }, body: { projectId }, headers: json,
      }));
    },
    runs(id: string, signal?: AbortSignal): Promise<SkillRunList> {
      return call("GET /api/skills/{id}/runs", client.get<{ 200: SkillRunList }>({
        url: "/api/skills/{skill_id}/runs", path: { skill_id: id }, signal,
      }));
    },
    /** The packages shipped with MonkeyHub and the user's own, with what the project holds of each. */
    library(signal?: AbortSignal): Promise<SkillLibrary> {
      return call("GET /api/skills/library", client.get<{ 200: SkillLibrary }>({ url: "/api/skills/library", signal }));
    },
    /** Register one package as a version of the project's skill. It grants nothing. */
    importPackage(body: SkillImportRequest): Promise<SkillImportResult> {
      return call("POST /api/skills/import", client.post<{ 200: SkillImportResult }>({ url: "/api/skills/import", body, headers: json }));
    },
  };
}

/**
 * What the library offers for a package: import it, update the project's
 * other version to it, nothing because the project already matches it, or
 * nothing because the package cannot be read.
 */
export type PackageOffer = "import" | "update" | "upToDate" | "broken";

export function packageOffer(row: SkillPackage): PackageOffer {
  if (row.error || !row.skillId) return "broken";
  if (row.upToDate === true) return "upToDate";
  return row.projectVersion == null ? "import" : "update";
}

/** One package's key in the panel's state. */
export const packageKey = (row: Pick<SkillPackage, "source" | "name">) => `${row.source}/${row.name}`;

/**
 * What a row says about running its code. `once` is an unused one-run grant of
 * this exact code; `unreported` is an older runtime that does not say.
 */
export type SkillStatus = "approved" | "once" | "unapproved" | "notRunnable" | "unreported";

/** Whether `grant` is an unused one-run grant of exactly this row's code. */
export function grantsOneRun(row: SkillRow, grant: SkillGrant): boolean {
  return grant.status === "active" && grant.scope === "once" && grant.skillId === row.id
    && grant.version === row.version && grant.codeSha256 === row.codeSha256;
}

export function skillStatus(row: SkillRow, grants: readonly SkillGrant[] | null = null): SkillStatus {
  if (row.runnable === false) return "notRunnable";
  if (row.approved === true) return "approved";
  if (grants?.some((grant) => grantsOneRun(row, grant))) return "once";
  if (row.approved === false) return "unapproved";
  return "unreported";
}

/** Whether the Hub page may offer to grant this row's version. */
export function canApprove(row: SkillRow, grants: readonly SkillGrant[] | null = null): boolean {
  const status = skillStatus(row, grants);
  return (status === "unapproved" || status === "once") && row.runnable === true
    && typeof row.codeSha256 === "string" && row.codeSha256 !== "";
}

/** What the last run needed and was not granted, or null when it did not fail for that. */
export function lastRunNeeds(runs: SkillRunList): readonly SkillRunNeed[] | null {
  const last = runs.runs.at(-1);
  if (!last || last.failureCode !== SKILL_PERMISSION_NEEDED) return null;
  return last.needed ?? [];
}

/** The first 12 characters of a code hash, as the list shows it. */
export function shortHash(sha256: string): string {
  return sha256.slice(0, 12);
}

/** Each file of a version with its size in UTF-8 bytes, the entry first, the rest by path. */
export function skillFiles(detail: SkillDetail): { path: string; bytes: number; text: string; entry: boolean }[] {
  const encoder = new TextEncoder();
  return Object.entries(detail.files ?? {})
    .map(([path, text]) => ({ path, text, bytes: encoder.encode(text).length, entry: path === detail.entry }))
    .sort((a, b) => Number(b.entry) - Number(a.entry) || a.path.localeCompare(b.path));
}
