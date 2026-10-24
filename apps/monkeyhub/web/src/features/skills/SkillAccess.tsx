/**
 * What a skill version may use, in plain words: one line per kind of access,
 * "None" for what it does not declare, "Any" for `"*"`. The review dialog, the
 * grants page and the last run's note all say it the same way.
 */

import type { SkillAccess, SkillRunNeed } from "../../api/project-runtime/skills";
import type { MessageKey } from "../../i18n/messages.en";
import type { TFunction } from "../../i18n/useT";

export const ACCESS_ITEMS = ["programs", "network", "ssh", "read", "write", "packages"] as const;
export type AccessItem = typeof ACCESS_ITEMS[number];

const LABELS: Record<AccessItem, MessageKey> = {
  programs: "skills.access.programs",
  network: "skills.access.network",
  ssh: "skills.access.ssh",
  read: "skills.access.read",
  write: "skills.access.write",
  packages: "skills.access.packages",
};

const isItem = (value: string): value is AccessItem => (ACCESS_ITEMS as readonly string[]).includes(value);

function listValue(values: readonly string[] | undefined, t: TFunction): { value: string; declared: boolean } {
  const items = (values ?? []).filter((item) => typeof item === "string" && item.trim() !== "");
  if (items.length === 0) return { value: t("skills.access.none"), declared: false };
  if (items.includes("*")) return { value: t("skills.access.any"), declared: true };
  return { value: items.join(t("skills.access.join")), declared: true };
}

/** One line per kind of access, in a fixed order; `null` (declares nothing) reads "None" throughout. */
export function accessLines(access: SkillAccess | null, t: TFunction): { item: AccessItem; text: string; declared: boolean }[] {
  return ACCESS_ITEMS.map((item) => {
    const { value, declared } = item === "network"
      ? access?.network === true ? { value: t("skills.access.yes"), declared: true } : { value: t("skills.access.none"), declared: false }
      : listValue(access?.[item], t);
    return { item, declared, text: t("skills.access.line", { label: t(LABELS[item]), value }) };
  });
}

/** `undefined` is a runtime that does not report access, and is said so rather than read as "None". */
export function AccessList({ access, t }: { access: SkillAccess | null | undefined; t: TFunction }) {
  if (access === undefined) return <p className="chat-muted">{t("skills.access.unreported")}</p>;
  return <ul className="skills__access">
    {accessLines(access, t).map((line) => <li key={line.item} data-declared={line.declared}>{line.text}</li>)}
  </ul>;
}

/** The one-line note naming what a run tried and was not granted. */
export function neededText(needs: readonly SkillRunNeed[], t: TFunction): string {
  const parts = needs.map((need) => {
    const label = isItem(need.category) ? t(LABELS[need.category]) : need.category;
    return need.detail ? t("skills.access.line", { label, value: need.detail }) : label;
  });
  return t("skills.needed", { needs: parts.length ? parts.join(t("skills.needed.join")) : t("skills.needed.unnamed") });
}
