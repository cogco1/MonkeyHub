/**
 * What a skill may use, in plain words: one line per kind of access, "None"
 * for what it does not declare, "Any" for `"*"`. The Skills list and the page
 * of what the AI may do say it the same way.
 */

import type { SkillAccess } from "../../api/project-runtime/skills";
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

/**
 * `undefined` is a runtime that does not report access, and is said so rather than read as "None".
 * `declaredOnly` lists only what is there, as a grant names what it allows.
 */
export function AccessList({ access, t, declaredOnly = false }: {
  access: SkillAccess | null | undefined; t: TFunction; declaredOnly?: boolean;
}) {
  if (access === undefined) return <p className="chat-muted">{t("skills.access.unreported")}</p>;
  return <ul className="skills__access">
    {accessLines(access, t).filter((line) => !declaredOnly || line.declared)
      .map((line) => <li key={line.item} data-declared={line.declared}>{line.text}</li>)}
  </ul>;
}
