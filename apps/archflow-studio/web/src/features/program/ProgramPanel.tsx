/**
 * The program sheet: departments, spaces, and the adjacencies the brief asks
 * for, beside what the record actually draws.
 *
 * Two numbers sit next to each other in every row and in the totals, and they
 * are different questions. *Target* is what the brief asks for — the
 * architect's number, typed here. *Mapped* is the footprint the record draws
 * for the zone this row maps to, and it is the server's, recomputed on every
 * read; nothing in this file computes an area. A row that maps to no zone
 * shows no mapped area at all rather than a zero, because zero is a number
 * about a building and "nothing draws this yet" is not.
 *
 * The function dropdown is the server's registered vocabulary
 * (`GET /api/semantics`), never a list this file carries: the record refuses a
 * term the registry does not know, and a dropdown of its own would offer
 * choices that come back as refusals.
 *
 * Applying sends the sheet to `POST /api/program`, which runs it as a
 * candidate. Nothing here writes the authored record, and the "keep this
 * sheet" checkbox is the one thing that writes a file — the architect's own,
 * and only where the server says it may.
 */

import { useState } from "react";

import type {
  ProgramAdjacencyDto,
  ProgramDto,
  ProgramSheetDto,
  ProgramSpaceDto,
  SemanticsDto,
} from "../../api/generated";
import { ErrorPanel } from "../../app/ErrorPanel";
import type { Loadable } from "../../app/loadable";
import type { MessageKey } from "../../i18n/messages.en";
import { BilingualText } from "../../i18n/BilingualText";
import { useT, type TFunction } from "../../i18n/useT";

/**
 * The four requirements the kernel's sheet may state, each with the message
 * that says it in the reader's language. A requirement outside this map is
 * shown verbatim rather than translated into a guess: the server's vocabulary
 * is allowed to grow ahead of this table.
 */
const REQUIREMENT_KEYS: Readonly<Record<string, MessageKey>> = {
  adjacent: "program.requirement.adjacent",
  near: "program.requirement.near",
  apart: "program.requirement.apart",
  visual: "program.requirement.visual",
};

/** One edit of one space row, by department and space id. */
export type SpaceEdit = {
  departmentId: string;
  spaceId: string;
  field: "name" | "function" | "targetAreaM2" | "count" | "clearHeightM";
  value: string;
};

/**
 * The sheet with one field of one space replaced.
 *
 * Exported because it is the whole of this panel's editing rule and is worth
 * reading on its own: a number the architect clears becomes `null` — the brief
 * not asking — and never `0`, which would be the brief asking for nothing.
 */
export function edited(sheet: ProgramSheetDto, edit: SpaceEdit): ProgramSheetDto {
  return {
    ...sheet,
    departments: sheet.departments.map((department) =>
      department.departmentId !== edit.departmentId
        ? department
        : {
            ...department,
            spaces: department.spaces.map((space) =>
              space.spaceId !== edit.spaceId ? space : withField(space, edit),
            ),
          },
    ),
  };
}

function withField(space: ProgramSpaceDto, edit: SpaceEdit): ProgramSpaceDto {
  const text = edit.value.trim();
  switch (edit.field) {
    case "name":
      return { ...space, name: edit.value };
    case "function":
      return { ...space, function: text === "" ? null : text };
    case "count": {
      const count = Number.parseInt(text, 10);
      return {
        ...space,
        count: Number.isFinite(count) && count >= 1 ? count : space.count,
      };
    }
    default: {
      const value = Number.parseFloat(text);
      return {
        ...space,
        [edit.field]:
          text === "" || !Number.isFinite(value) || value < 0 ? null : value,
      };
    }
  }
}

/** A number the way this panel shows one, or the dash that means "not stated". */
function area(value: number | null, t: TFunction): string {
  return value === null ? t("program.noNumber") : `${round(value)}`;
}

function round(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function FunctionCell({
  space,
  semantics,
  onEdit,
  t,
}: {
  space: ProgramSpaceDto;
  semantics: Loadable<SemanticsDto>;
  onEdit(field: SpaceEdit["field"], value: string): void;
  t: TFunction;
}) {
  if (semantics.status !== "ready") {
    // The registry is what says which functions exist. Until it is here the
    // cell shows what the sheet already says and refuses to be edited: an
    // input with no vocabulary behind it would invite a term the record
    // refuses.
    return (
      <span className="program__function program__function--plain mono">
        {space.function ?? t("program.noNumber")}
      </span>
    );
  }
  const current = space.function;
  // A function the record already carries need not be a role *id*: an alias
  // and a compound phrase both resolve, and the sheet keeps the word that was
  // written. It is offered as its own option so the cell shows what the sheet
  // says instead of falling back to "not stated" — which would be this panel
  // telling the architect their brief is emptier than it is.
  const registered = semantics.value.roles.some((role) => role.id === current);
  return (
    <select
      className="program__function"
      value={current ?? ""}
      aria-label={t("program.functionOf", { name: space.name })}
      onChange={(event) => onEdit("function", event.target.value)}
    >
      <option value="">{t("program.functionUnset")}</option>
      {current !== null && !registered && (
        <option value={current} title={t("program.functionAsWritten")}>
          {current}
        </option>
      )}
      {semantics.value.roles.map((role) => (
        <option key={role.id} value={role.id} title={role.meaning}>
          {role.id}
        </option>
      ))}
    </select>
  );
}

function SpaceRow({
  space,
  semantics,
  onEdit,
  t,
}: {
  space: ProgramSpaceDto;
  semantics: Loadable<SemanticsDto>;
  onEdit(field: SpaceEdit["field"], value: string): void;
  t: TFunction;
}) {
  return (
    <tr className="program__row" data-unmapped={String(space.zoneId === null)}>
      <td>
        <input
          className="program__text"
          value={space.name}
          aria-label={t("program.nameOf", { id: space.spaceId })}
          onChange={(event) => onEdit("name", event.target.value)}
        />
        {space.name !== space.spaceId && (
          <span className="quiet mono program__id">{space.spaceId}</span>
        )}
      </td>
      <td>
        <FunctionCell
          space={space}
          semantics={semantics}
          onEdit={onEdit}
          t={t}
        />
      </td>
      <td className="program__number">
        <input
          className="program__num"
          inputMode="decimal"
          value={space.targetAreaM2 === null ? "" : String(space.targetAreaM2)}
          aria-label={t("program.targetOf", { name: space.name })}
          onChange={(event) => onEdit("targetAreaM2", event.target.value)}
        />
      </td>
      <td className="program__number">
        <input
          className="program__num"
          inputMode="numeric"
          value={String(space.count)}
          aria-label={t("program.countOf", { name: space.name })}
          onChange={(event) => onEdit("count", event.target.value)}
        />
      </td>
      <td className="program__number">
        <input
          className="program__num"
          inputMode="decimal"
          value={space.clearHeightM === null ? "" : String(space.clearHeightM)}
          aria-label={t("program.clearHeightOf", { name: space.name })}
          onChange={(event) => onEdit("clearHeightM", event.target.value)}
        />
      </td>
      <td className="program__number quiet">{area(space.mappedAreaM2, t)}</td>
      <td>
        {space.zoneId === null ? (
          <span className="program__unmapped">{t("program.unmapped")}</span>
        ) : (
          <span className="mono quiet">{space.zoneId}</span>
        )}
      </td>
    </tr>
  );
}

function Adjacency({
  row,
  t,
}: {
  row: ProgramAdjacencyDto;
  t: TFunction;
}) {
  const key = REQUIREMENT_KEYS[row.requirement];
  return (
    <li className="program__adjacency">
      <span className="mono">{row.fromSpaceId}</span>
      <span className="program__requirement">
        {key === undefined ? row.requirement : t(key)}
      </span>
      <span className="mono">{row.toSpaceId}</span>
      {row.relationId === null ? (
        <span className="quiet program__note">{t("program.willDeclare")}</span>
      ) : (
        <span className="quiet mono program__note">{row.relationId}</span>
      )}
    </li>
  );
}

export function ProgramPanel({
  program,
  semantics,
  sheet,
  applying,
  canSave,
  onEdit,
  onApply,
  onReread,
  onClose,
}: {
  /** What the server last answered; the source line comes off it. */
  program: Loadable<ProgramDto>;
  semantics: Loadable<SemanticsDto>;
  /** The sheet as edited in this tab; null before the first read lands. */
  sheet: ProgramSheetDto | null;
  applying: boolean;
  /** Whether this server writes the architect's own file at all. */
  canSave: boolean;
  onEdit(edit: SpaceEdit): void;
  onApply(sheet: ProgramSheetDto, saveInput: boolean): void;
  onReread(): void;
  onClose(): void;
}) {
  const t = useT();
  const [save, setSave] = useState(false);
  return (
    <div className="program" role="dialog" aria-label={t("program.ariaLabel")}>
      <div className="program__head">
        <span className="program__title">{t("program.title")}</span>
        <span className="quiet program__note">{t("program.subtitle")}</span>
        <button
          type="button"
          className="btn btn--link program__close"
          onClick={onClose}
        >
          {t("common.close")}
        </button>
      </div>
      {program.status === "loading" || program.status === "idle" ? (
        <p className="program__note quiet">{t("program.loading")}</p>
      ) : program.status === "failed" ? (
        <ErrorPanel error={program.error} what="GET /api/program" />
      ) : sheet === null ? (
        <p className="program__note quiet">{t("program.loading")}</p>
      ) : (
        <>
          <p className="program__source quiet">
            {program.value.source === "input"
              ? t("program.sourceInput")
              : t("program.sourceDerived")}
          </p>
          {sheet.departments.length === 0 ? (
            <p className="program__note quiet">{t("program.empty")}</p>
          ) : (
            sheet.departments.map((department) => (
              <section key={department.departmentId} className="program__department">
                <h3 className="program__heading">
                  {department.name}
                  {/* The id only where it is not already the heading: a
                      derived sheet has no department name to show but the id,
                      and printing it twice reads as two departments. */}
                  {department.name !== department.departmentId && (
                    <span className="quiet mono program__id">
                      {department.departmentId}
                    </span>
                  )}
                </h3>
                <table className="program__table">
                  <thead>
                    <tr>
                      <th scope="col">{t("program.space")}</th>
                      <th scope="col">{t("program.function")}</th>
                      <th scope="col">{t("program.target")}</th>
                      <th scope="col">{t("program.count")}</th>
                      <th scope="col">{t("program.clearHeight")}</th>
                      <th scope="col">{t("program.mapped")}</th>
                      <th scope="col">{t("program.zone")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {department.spaces.map((space) => (
                      <SpaceRow
                        key={space.spaceId}
                        space={space}
                        semantics={semantics}
                        onEdit={(field, value) =>
                          onEdit({
                            departmentId: department.departmentId,
                            spaceId: space.spaceId,
                            field,
                            value,
                          })
                        }
                        t={t}
                      />
                    ))}
                  </tbody>
                </table>
              </section>
            ))
          )}
          <p className="program__totals">
            <span>
              {t("program.totalTarget", {
                value: round(sheet.totals.targetAreaM2),
              })}
            </span>
            <span>
              {t("program.totalMapped", {
                value: round(sheet.totals.mappedAreaM2),
              })}
            </span>
            <span data-unmapped={String(sheet.totals.unmappedSpaces.length > 0)}>
              {t("program.totalUnmapped", {
                n: sheet.totals.unmappedSpaces.length,
              })}
            </span>
          </p>
          <section className="program__department">
            <h3 className="program__heading">{t("program.adjacencies")}</h3>
            {sheet.adjacencies.length === 0 ? (
              <p className="program__note quiet">{t("program.noAdjacencies")}</p>
            ) : (
              <ul className="program__adjacencies">
                {sheet.adjacencies.map((row) => (
                  <Adjacency
                    key={`${row.fromSpaceId}-${row.requirement}-${row.toSpaceId}`}
                    row={row}
                    t={t}
                  />
                ))}
              </ul>
            )}
          </section>
          <div className="program__actions">
            {canSave && (
              <label className="program__save">
                <input
                  type="checkbox"
                  checked={save}
                  onChange={(event) => setSave(event.target.checked)}
                />
                {t("program.keepSheet")}
              </label>
            )}
            <button
              type="button"
              className="btn"
              disabled={applying}
              title={t("program.applyTitle")}
              onClick={() => onApply(sheet, save && canSave)}
            >
              {applying ? t("program.applying") : t("program.apply")}
            </button>
            <button type="button" className="btn btn--link" onClick={onReread}>
              {t("program.reread")}
            </button>
          </div>
          {sheet.honesty.length > 0 && (
            <ul className="program__honesty">
              {sheet.honesty.map((line) => (
                <li key={line}>
                  <BilingualText source={line} showSourceToggle />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
