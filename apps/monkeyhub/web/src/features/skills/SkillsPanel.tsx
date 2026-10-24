/**
 * Developer mode › Skills (S-1): the skills installed on this computer, the
 * ones that come with the Hub and the person's own, as every project's chats
 * are given them. Each row says where it comes from, what it declares, the
 * version this project keeps from its runs, and why a package cannot be read.
 *
 * Nothing here imports, approves or runs a skill: a skill is installed by
 * putting its folder in the skill folder, and what it may do is allowed when a
 * chat asks, or withdrawn in Settings › What the AI may do.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { useConnection } from "../../api/project-runtime/ProjectRuntimeContext";
import { asStudioApiError, type StudioApiError } from "../../api/project-runtime/error";
import { createSkillsApi, packageKey, shortHash, type SkillLibrary, type SkillPackage, type SkillsApi } from "../../api/project-runtime/skills";
import { useT, type TFunction } from "../../i18n/useT";
import { AccessList } from "./SkillAccess";
import "./skills.css";
import { AutoRetry } from "../../app/AutoRetry";

const SOURCE_KEYS = { bundled: "skills.library.source.bundled", user: "skills.library.source.user" } as const;

/** The list for the project whose runtime provider encloses it. */
export function ProjectSkills({ close }: { close(): void }) {
  const connection = useConnection();
  const api = useMemo(() => createSkillsApi(connection), [connection]);
  return <SkillsPanel api={api} close={close} />;
}

export function SkillsPanel({ api, close }: { api: SkillsApi; close(): void }) {
  const [library, setLibrary] = useState<SkillLibrary | null>(null);
  const [error, setError] = useState<StudioApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setLibrary(await api.library());
    } catch (cause) {
      setError(asStudioApiError(cause));
    } finally {
      setLoading(false);
    }
  }, [api]);
  useEffect(() => { void load(); }, [load]);
  return <SkillsView library={library} loading={loading} error={error} reload={() => void load()} close={close} />;
}

export function SkillsView({ library, loading, error, reload, close }: {
  library: SkillLibrary | null; loading: boolean; error: StudioApiError | null; reload(): void; close(): void;
}) {
  const t = useT();
  return <section className="skills" aria-labelledby="skills-heading">
    <div className="chat-dialog__heading">
      <h2 id="skills-heading">{t("skills.title")}</h2>
      <button type="button" className="btn" onClick={close}>{t("skills.close")}</button>
    </div>
    <p className="chat-muted">{t("skills.intro")}</p>
    {loading && !library && <p className="chat-muted" role="status">{t("skills.library.loading")}</p>}
    {error && <div className="skills__error" role="alert">
      <p>{t("skills.library.loadFailed")} {error.detail}</p>
      <AutoRetry failure={error} place="skills_library" className="btn" disabled={loading} retry={reload}>{t("skills.retry")}</AutoRetry>
    </div>}
    {library && library.packages.length === 0
      && <p className="chat-muted">{t("skills.library.empty", { folder: library.sources.user ?? "" })}</p>}
    {library && library.packages.length > 0 && <ul className="skills__list">
      {library.packages.map((row) => <PackageRow key={packageKey(row)} row={row} t={t} />)}
    </ul>}
  </section>;
}

function PackageRow({ row, t }: { row: SkillPackage; t: TFunction }) {
  return <li className="skills__row" data-source={row.source} data-shadowed={row.shadowed ? "true" : undefined}>
    <div className="skills__head">
      <strong className="skills__name">{row.skillName ?? row.name}</strong>
      <span className="skills__status">{t(SOURCE_KEYS[row.source])}</span>
      {row.shadowed && <span className="skills__status">{t("skills.library.shadowed")}</span>}
    </div>
    {row.error
      ? <p className="skills__error">{t("skills.library.broken", { detail: row.error.detail })}</p>
      : <>
        {row.description && <p className="skills__description">{row.description}</p>}
        <dl className="skills__facts">
          <dt>{t("skills.library.mayDo")}</dt><dd><AccessList access={row.access ?? null} t={t} declaredOnly /></dd>
          <dt>{t("skills.library.inProject")}</dt>
          <dd>{row.projectVersion == null ? t("skills.library.notInProject")
            : row.upToDate ? t("skills.library.upToDateVersion", { version: row.projectVersion })
              : t("skills.library.olderVersion", { version: row.projectVersion })}</dd>
          {row.codeSha256 && <><dt>{t("skills.library.code")}</dt><dd><code title={row.codeSha256}>{shortHash(row.codeSha256)}</code></dd></>}
          <dt>{t("skills.library.folder")}</dt><dd><code>{row.path}</code></dd>
        </dl>
      </>}
  </li>;
}
