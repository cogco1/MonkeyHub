/**
 * Component information (#549): what is known about a model's components, one topic at a time.
 *
 * A dataset is one MonkeyHubComponentInfo@1 document about one topic (basics, procurement, thermal,
 * structure, ...), written for one exact model version and keyed by the model's own component ids.
 * Each dataset lives on its own Board card (boardDatasets.ts); Modeling shows, for the component
 * picked, the datasets written for the version on screen and nothing written for another.
 *
 * The vocabulary is generic on purpose: a field is a label, a value and how sure it is. Nothing
 * here knows what a price, a supplier or a conductivity is; the dataset says it in its own words.
 * Pure functions only: no React, no I/O.
 */

import type { MessageKey } from "../../i18n/messages.en";

export const COMPONENT_INFO_SCHEMA = "MonkeyHubComponentInfo@1";

export const FIELD_STATUSES = ["verified", "estimate", "to-ask", "to-confirm", "unknown"] as const;
export type FieldStatus = (typeof FIELD_STATUSES)[number];

/** One fact about a component or a group: a label, a value, and how it is known. */
export interface InfoField {
  readonly label: string;
  readonly value: string | number;
  readonly unit?: string;
  readonly status?: FieldStatus;
  /** A source id the dataset lists. */
  readonly source?: string;
  readonly date?: string;
  readonly url?: string;
  readonly note?: string;
}

export interface InfoSource {
  readonly id: string;
  readonly label: string;
  readonly date?: string;
  readonly kind?: string;
}

/** Something several components share (a purchased stock, a wall type), described once. */
export interface InfoGroup {
  readonly title: string;
  readonly fields: readonly InfoField[];
  readonly shared?: boolean;
  readonly sharedBy?: number;
  /** How the group is divided among its components; null says it is not divided. */
  readonly allocation?: { readonly basis: string; readonly fields?: readonly InfoField[] } | null;
  readonly note?: string;
}

export interface InfoEntry {
  readonly name?: string;
  readonly fields?: readonly InfoField[];
  readonly group?: string;
  readonly notes?: string;
}

/** The exact model version a dataset was written for. It never names or claims a model source. */
export interface AppliesTo {
  readonly projectId: string;
  readonly runId: string;
  readonly stateDigest: string;
  readonly versionLabel?: string;
}

export interface ComponentInfoDataset {
  readonly schema: typeof COMPONENT_INFO_SCHEMA;
  readonly id: string;
  readonly title: string;
  /** A summary dataset is the card's first screen; sections follow it in `order`. */
  readonly role: "summary" | "section";
  readonly order?: number;
  readonly preparedAt: string;
  readonly appliesTo: AppliesTo;
  readonly statusLabels?: Partial<Record<FieldStatus, string>>;
  /** What the card says for a component this dataset has no entry for. */
  readonly missing?: string;
  readonly note?: string;
  readonly sources?: readonly InfoSource[];
  readonly groups?: Readonly<Record<string, InfoGroup>>;
  readonly components: Readonly<Record<string, InfoEntry>>;
}

export type ProblemCode =
  | "notJson" | "empty" | "schema" | "required" | "type" | "unknownKey" | "forbiddenKey" | "id" | "date"
  | "digest" | "url" | "status" | "sourceRef" | "groupRef" | "duplicate" | "length" | "tooLarge" | "project";

/** Why a dataset was refused: a code the interface words, and where in the document it is. */
export interface Problem {
  readonly code: ProblemCode;
  readonly path: string;
}

export class ComponentInfoError extends Error {
  constructor(readonly problems: readonly Problem[]) {
    super(problems.map((problem) => `${problem.path}: ${problem.code}`).join("; "));
    this.name = "ComponentInfoError";
  }
}

/**
 * Keys the Board refuses anywhere inside an element (project_runtime.board's scene check): a
 * dataset carrying one could never be saved on its card, so it is refused here, before import.
 */
export const BOARD_FORBIDDEN_KEYS = ["files", "dataURL", "contentBase64", "appState", "modelSource", "sourceStageRef"] as const;

const ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const LIMITS = { title: 120, label: 120, text: 4000, unit: 40, url: 2048, name: 200, status: 40, kind: 60, id: 256, fields: 200, components: 20000 };

type Json = Record<string, unknown>;
const isObject = (value: unknown): value is Json => value !== null && typeof value === "object" && !Array.isArray(value);
const keyPath = (path: string, key: string) => /^[A-Za-z_$][\w$]*$/.test(key) ? `${path}.${key}` : `${path}[${JSON.stringify(key)}]`;

function validDate(value: string): boolean {
  if (!DATE.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year!, month! - 1, day!));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month! - 1 && date.getUTCDate() === day;
}

function validUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (url.protocol === "https:" || url.protocol === "http:") && url.hostname !== "";
  } catch { return false; }
}

/** Every key the Board would refuse, wherever it sits. */
function forbidden(value: unknown, path: string, problems: Problem[]) {
  const pending: [unknown, string][] = [[value, path]];
  while (pending.length) {
    const [item, at] = pending.pop()!;
    if (Array.isArray(item)) item.forEach((child, index) => pending.push([child, `${at}[${index}]`]));
    else if (isObject(item)) {
      for (const [key, child] of Object.entries(item)) {
        if ((BOARD_FORBIDDEN_KEYS as readonly string[]).includes(key)) problems.push({ code: "forbiddenKey", path: keyPath(at, key) });
        pending.push([child, keyPath(at, key)]);
      }
    }
  }
}

class Reader {
  readonly problems: Problem[] = [];
  fail(code: ProblemCode, path: string) { this.problems.push({ code, path }); }

  keys(value: Json, path: string, allowed: readonly string[]) {
    for (const key of Object.keys(value)) if (!allowed.includes(key)) this.fail("unknownKey", keyPath(path, key));
  }

  text(value: Json, key: string, path: string, max: number, required: boolean): string | undefined {
    const item = value[key];
    if (item === undefined) { if (required) this.fail("required", keyPath(path, key)); return undefined; }
    if (typeof item !== "string" || (required && item.trim() === "")) { this.fail("type", keyPath(path, key)); return undefined; }
    if (item.length > max) { this.fail("length", keyPath(path, key)); return undefined; }
    return item;
  }

  date(value: Json, key: string, path: string, required: boolean): string | undefined {
    const item = this.text(value, key, path, 10, required);
    if (item !== undefined && !validDate(item)) { this.fail("date", keyPath(path, key)); return undefined; }
    return item;
  }

  field(value: unknown, path: string, sources: ReadonlySet<string>): InfoField | null {
    if (!isObject(value)) { this.fail("type", path); return null; }
    this.keys(value, path, ["label", "value", "unit", "status", "source", "date", "url", "note"]);
    const label = this.text(value, "label", path, LIMITS.label, true);
    const raw = value.value;
    let item: string | number | undefined;
    if (raw === undefined) this.fail("required", keyPath(path, "value"));
    else if (typeof raw === "number" && Number.isFinite(raw)) item = raw;
    else if (typeof raw === "string" && raw.trim() !== "") {
      if (raw.length > LIMITS.text) this.fail("length", keyPath(path, "value")); else item = raw;
    } else this.fail("type", keyPath(path, "value"));
    const unit = this.text(value, "unit", path, LIMITS.unit, false);
    const status = value.status;
    if (status !== undefined && !(FIELD_STATUSES as readonly unknown[]).includes(status)) this.fail("status", keyPath(path, "status"));
    const source = this.text(value, "source", path, LIMITS.id, false);
    if (source !== undefined && !sources.has(source)) this.fail("sourceRef", keyPath(path, "source"));
    const date = this.date(value, "date", path, false);
    const url = this.text(value, "url", path, LIMITS.url, false);
    if (url !== undefined && !validUrl(url)) this.fail("url", keyPath(path, "url"));
    const note = this.text(value, "note", path, LIMITS.text, false);
    if (label === undefined || item === undefined) return null;
    return { label, value: item, ...(unit !== undefined ? { unit } : {}),
      ...(status !== undefined ? { status: status as FieldStatus } : {}), ...(source !== undefined ? { source } : {}),
      ...(date !== undefined ? { date } : {}), ...(url !== undefined && validUrl(url) ? { url } : {}),
      ...(note !== undefined ? { note } : {}) };
  }

  fields(value: unknown, path: string, sources: ReadonlySet<string>, required: boolean): InfoField[] {
    if (value === undefined) { if (required) this.fail("required", path); return []; }
    if (!Array.isArray(value)) { this.fail("type", path); return []; }
    if (value.length > LIMITS.fields) { this.fail("length", path); return []; }
    return value.flatMap((item, index) => this.field(item, `${path}[${index}]`, sources) ?? []);
  }
}

/**
 * Read one dataset, or say every reason it cannot be one. `path` names the document in the
 * reasons (the second dataset of an imported array is `$[1]`).
 */
export function readDataset(value: unknown, path = "$"): { dataset: ComponentInfoDataset | null; problems: Problem[] } {
  const read = new Reader();
  if (!isObject(value) || value.schema !== COMPONENT_INFO_SCHEMA) {
    read.fail("schema", isObject(value) ? keyPath(path, "schema") : path);
    return { dataset: null, problems: read.problems };
  }
  forbidden(value, path, read.problems);
  read.keys(value, path, ["schema", "id", "title", "role", "order", "preparedAt", "appliesTo", "statusLabels",
    "missing", "note", "sources", "groups", "components"]);
  const id = read.text(value, "id", path, LIMITS.id, true);
  if (id !== undefined && !ID.test(id)) read.fail("id", keyPath(path, "id"));
  const title = read.text(value, "title", path, LIMITS.title, true);
  const role = value.role;
  if (role === undefined) read.fail("required", keyPath(path, "role"));
  else if (role !== "summary" && role !== "section") read.fail("type", keyPath(path, "role"));
  const order = value.order;
  if (order !== undefined && (typeof order !== "number" || !Number.isFinite(order))) read.fail("type", keyPath(path, "order"));
  const preparedAt = read.date(value, "preparedAt", path, true);

  let appliesTo: AppliesTo | undefined;
  const applies = value.appliesTo, appliesPath = keyPath(path, "appliesTo");
  if (applies === undefined) read.fail("required", appliesPath);
  else if (!isObject(applies)) read.fail("type", appliesPath);
  else {
    read.keys(applies, appliesPath, ["projectId", "runId", "stateDigest", "versionLabel"]);
    const projectId = read.text(applies, "projectId", appliesPath, LIMITS.id, true);
    const runId = read.text(applies, "runId", appliesPath, LIMITS.id, true);
    const stateDigest = read.text(applies, "stateDigest", appliesPath, 64, true);
    if (stateDigest !== undefined && !DIGEST.test(stateDigest)) read.fail("digest", keyPath(appliesPath, "stateDigest"));
    const versionLabel = read.text(applies, "versionLabel", appliesPath, LIMITS.title, false);
    if (projectId !== undefined && runId !== undefined && stateDigest !== undefined && DIGEST.test(stateDigest)) {
      appliesTo = { projectId, runId, stateDigest, ...(versionLabel !== undefined ? { versionLabel } : {}) };
    }
  }

  let statusLabels: Partial<Record<FieldStatus, string>> | undefined;
  const labels = value.statusLabels, labelsPath = keyPath(path, "statusLabels");
  if (labels !== undefined) {
    if (!isObject(labels)) read.fail("type", labelsPath);
    else {
      read.keys(labels, labelsPath, FIELD_STATUSES);
      statusLabels = {};
      for (const status of FIELD_STATUSES) {
        const label = read.text(labels, status, labelsPath, LIMITS.status, false);
        if (label !== undefined) statusLabels[status] = label;
      }
    }
  }
  const missing = read.text(value, "missing", path, LIMITS.text, false);
  const note = read.text(value, "note", path, LIMITS.text, false);

  const sources: InfoSource[] = [];
  const sourceIds = new Set<string>();
  const sourceList = value.sources, sourcesPath = keyPath(path, "sources");
  if (sourceList !== undefined) {
    if (!Array.isArray(sourceList)) read.fail("type", sourcesPath);
    else sourceList.forEach((item, index) => {
      const at = `${sourcesPath}[${index}]`;
      if (!isObject(item)) { read.fail("type", at); return; }
      read.keys(item, at, ["id", "label", "date", "kind"]);
      const sourceId = read.text(item, "id", at, LIMITS.id, true);
      const label = read.text(item, "label", at, LIMITS.text, true);
      const date = read.date(item, "date", at, false);
      const kind = read.text(item, "kind", at, LIMITS.kind, false);
      if (sourceId === undefined || label === undefined) return;
      if (sourceIds.has(sourceId)) { read.fail("duplicate", keyPath(at, "id")); return; }
      sourceIds.add(sourceId);
      sources.push({ id: sourceId, label, ...(date !== undefined ? { date } : {}), ...(kind !== undefined ? { kind } : {}) });
    });
  }

  const groups: Record<string, InfoGroup> = {};
  const groupMap = value.groups, groupsPath = keyPath(path, "groups");
  if (groupMap !== undefined) {
    if (!isObject(groupMap)) read.fail("type", groupsPath);
    else for (const [groupId, item] of Object.entries(groupMap)) {
      const at = keyPath(groupsPath, groupId);
      if (!ID.test(groupId)) read.fail("id", at);
      if (!isObject(item)) { read.fail("type", at); continue; }
      read.keys(item, at, ["title", "fields", "shared", "sharedBy", "allocation", "note"]);
      const groupTitle = read.text(item, "title", at, LIMITS.title, true);
      const fields = read.fields(item.fields, keyPath(at, "fields"), sourceIds, false);
      if (item.shared !== undefined && typeof item.shared !== "boolean") read.fail("type", keyPath(at, "shared"));
      const sharedBy = item.sharedBy;
      if (sharedBy !== undefined && (typeof sharedBy !== "number" || !Number.isInteger(sharedBy) || sharedBy < 1)) read.fail("type", keyPath(at, "sharedBy"));
      let allocation: InfoGroup["allocation"];
      const split = item.allocation, splitPath = keyPath(at, "allocation");
      if (split === null) allocation = null;
      else if (split !== undefined) {
        if (!isObject(split)) read.fail("type", splitPath);
        else {
          read.keys(split, splitPath, ["basis", "fields"]);
          const basis = read.text(split, "basis", splitPath, LIMITS.text, true);
          const splitFields = read.fields(split.fields, keyPath(splitPath, "fields"), sourceIds, false);
          if (basis !== undefined) allocation = { basis, fields: splitFields };
        }
      }
      const groupNote = read.text(item, "note", at, LIMITS.text, false);
      if (groupTitle === undefined) continue;
      groups[groupId] = { title: groupTitle, fields, ...(typeof item.shared === "boolean" ? { shared: item.shared } : {}),
        ...(typeof sharedBy === "number" && Number.isInteger(sharedBy) && sharedBy >= 1 ? { sharedBy } : {}),
        ...(allocation !== undefined ? { allocation } : {}), ...(groupNote !== undefined ? { note: groupNote } : {}) };
    }
  }

  const components: Record<string, InfoEntry> = {};
  const entries = value.components, componentsPath = keyPath(path, "components");
  if (entries === undefined) read.fail("required", componentsPath);
  else if (!isObject(entries)) read.fail("type", componentsPath);
  else if (Object.keys(entries).length > LIMITS.components) read.fail("length", componentsPath);
  else for (const [componentId, item] of Object.entries(entries)) {
    const at = keyPath(componentsPath, componentId);
    if (componentId.trim() === "" || componentId.length > LIMITS.id) read.fail("id", at);
    if (!isObject(item)) { read.fail("type", at); continue; }
    read.keys(item, at, ["name", "fields", "group", "notes"]);
    const name = read.text(item, "name", at, LIMITS.name, false);
    const fields = read.fields(item.fields, keyPath(at, "fields"), sourceIds, false);
    const group = read.text(item, "group", at, LIMITS.id, false);
    if (group !== undefined && !(isObject(groupMap) && Object.hasOwn(groupMap, group))) read.fail("groupRef", keyPath(at, "group"));
    const notes = read.text(item, "notes", at, LIMITS.text, false);
    components[componentId] = { ...(name !== undefined ? { name } : {}), ...(fields.length ? { fields } : {}),
      ...(group !== undefined ? { group } : {}), ...(notes !== undefined ? { notes } : {}) };
  }

  if (read.problems.length || id === undefined || title === undefined || preparedAt === undefined || appliesTo === undefined) {
    return { dataset: null, problems: read.problems };
  }
  return {
    dataset: {
      schema: COMPONENT_INFO_SCHEMA, id, title, role: role as "summary" | "section",
      ...(typeof order === "number" ? { order } : {}), preparedAt, appliesTo,
      ...(statusLabels && Object.keys(statusLabels).length ? { statusLabels } : {}),
      ...(missing !== undefined ? { missing } : {}), ...(note !== undefined ? { note } : {}),
      ...(sources.length ? { sources } : {}), ...(Object.keys(groups).length ? { groups } : {}), components,
    },
    problems: [],
  };
}

/**
 * What an import file holds: one dataset or an array of them, each valid and each id once.
 * Throws a ComponentInfoError naming every problem; nothing partial is returned.
 */
export function parseDatasetImport(text: string): ComponentInfoDataset[] {
  let value: unknown;
  try { value = JSON.parse(text); }
  catch { throw new ComponentInfoError([{ code: "notJson", path: "$" }]); }
  const documents = Array.isArray(value) ? value : [value];
  if (documents.length === 0) throw new ComponentInfoError([{ code: "empty", path: "$" }]);
  const problems: Problem[] = [];
  const datasets: ComponentInfoDataset[] = [];
  const ids = new Set<string>();
  documents.forEach((document, index) => {
    const read = readDataset(document, Array.isArray(value) ? `$[${index}]` : "$");
    problems.push(...read.problems);
    if (!read.dataset) return;
    if (ids.has(read.dataset.id)) { problems.push({ code: "duplicate", path: `${Array.isArray(value) ? `$[${index}]` : "$"}.id` }); return; }
    ids.add(read.dataset.id);
    datasets.push(read.dataset);
  });
  if (problems.length) throw new ComponentInfoError(problems);
  return datasets;
}

/** The exact retained version Modeling shows, with the project it belongs to. */
export interface ShownModel {
  readonly projectId: string;
  readonly runId: string;
  readonly stateDigest: string;
}

/** A dataset written for exactly this version: same project, run and state. */
export function appliesToShown(dataset: ComponentInfoDataset, shown: ShownModel | null): boolean {
  return shown !== null && dataset.appliesTo.projectId === shown.projectId &&
    dataset.appliesTo.runId === shown.runId && dataset.appliesTo.stateDigest === shown.stateDigest;
}

/**
 * The line the shown version stands on (#575): the runs it continued, nearest first, as the Working
 * Head's lineage names them, and each run's state where an export of it says.
 */
export interface ShownLineage {
  readonly runId: string;
  readonly ancestors: readonly string[];
  readonly states: ReadonlyMap<string, string>;
}

/** Whether each component's exported shape changed in the shown version since a run it descends from, by that run. */
export type ChangesSince = ReadonlyMap<string, ReadonlyMap<string, boolean> | "unknown">;

/**
 * How a dataset reaches the shown version (#575; Kaiwen, 2026-10-01: "继承"). Written for that
 * version, it is its own; written for a version the shown one continued from, on the same line, it
 * is inherited, as the person and the agents add meaning step by step along one line. Data written
 * on another line, or for a state its run never had, is not this version's.
 */
export function datasetRelation(dataset: ComponentInfoDataset, shown: ShownModel | null,
  lineage: ShownLineage | null): "own" | "inherited" | null {
  if (shown === null || dataset.appliesTo.projectId !== shown.projectId) return null;
  if (dataset.appliesTo.runId === shown.runId) return dataset.appliesTo.stateDigest === shown.stateDigest ? "own" : null;
  if (lineage === null || lineage.runId !== shown.runId || !lineage.ancestors.includes(dataset.appliesTo.runId)) return null;
  const state = lineage.states.get(dataset.appliesTo.runId);
  return state === undefined || state === dataset.appliesTo.stateDigest ? "inherited" : null;
}

/**
 * Whether a pick was resolved against the very version shown. The shell answers a click either
 * from the loaded run's own catalog (`local`, naming that run's state digest) or from the runtime
 * (`resolved`, `current` when the clicked file was exported from the state it was asked about).
 * Anything else - stale, unknown, unresolved - is not a pick data may be attached to.
 */
export function pickMatchesShown(pick: { readonly status: string; readonly sourceState: string }, shown: ShownModel | null): boolean {
  if (shown === null) return false;
  if (pick.status === "local") return pick.sourceState === shown.stateDigest;
  return (pick.status === "resolved" || pick.status === "MODEL_VISIBLE_CATALOG_MISSING") && pick.sourceState === "current";
}

/** A dataset as found on the Board: the card element carrying it. */
export interface BoardDataset {
  readonly elementId: string;
  readonly dataset: ComponentInfoDataset;
}

/** A Board card that carries something this format cannot read, with why. */
export interface InvalidCard {
  readonly elementId: string;
  readonly datasetId: string | null;
  readonly problems: readonly Problem[];
}

/** What Modeling read from the Board, once per Board revision. */
export type BoardInfo =
  | { readonly status: "idle" | "loading" }
  | { readonly status: "error"; readonly error: unknown }
  | { readonly status: "ready"; readonly projectId: string; readonly revisionSha256: string | null;
      readonly datasets: readonly BoardDataset[]; readonly invalid: readonly InvalidCard[] };

/** A field with its status and source in words the card can print. */
export interface CardField extends InfoField {
  readonly statusLabel: string | null;
  readonly sourceLabel: string | null;
  readonly sourceDate: string | null;
}

export interface CardBlock {
  readonly datasetId: string;
  readonly title: string;
  readonly role: "summary" | "section";
  readonly note: string | null;
  readonly entry: { readonly fields: readonly CardField[]; readonly notes: string | null } | null;
  readonly group: { readonly id: string; readonly title: string; readonly fields: readonly CardField[]; readonly shared: boolean;
    readonly sharedBy: number | null; readonly note: string | null;
    readonly allocation: { readonly basis: string; readonly fields: readonly CardField[] } | null } | null;
  /** What this dataset says when it has nothing for the component; null when it has an entry. */
  readonly missing: string | null;
}

export interface CardDataset {
  readonly elementId: string;
  readonly id: string;
  readonly title: string;
  readonly role: "summary" | "section";
  readonly preparedAt: string;
  readonly appliesTo: AppliesTo;
  /** Written for a version the shown one continued from, rather than for the shown version itself. */
  readonly inherited: boolean;
}

export type CardState = "loading" | "error" | "none" | "unverified" | "mismatch" | "ready";

/** Everything the card shows for one picked component, decided once and printed twice (screen, copy). */
export interface ComponentCard {
  readonly componentId: string;
  readonly elementId: string | null;
  readonly name: string;
  readonly state: CardState;
  readonly shown: ShownModel | null;
  readonly boardRevision: string | null;
  readonly summary: readonly CardBlock[];
  readonly sections: readonly CardBlock[];
  readonly used: readonly CardDataset[];
  readonly mismatched: readonly CardDataset[];
  readonly invalid: readonly InvalidCard[];
  readonly sources: readonly (InfoSource & { readonly datasetTitle: string })[];
  /** The versions inherited data was written for, since which this component's exported shape changed. */
  readonly reshapedSince: readonly string[];
}

export interface CardInput {
  readonly componentId: string;
  readonly elementId: string | null;
  /** The model's own readable label for the component, used when no dataset names it. */
  readonly modelLabel: string;
  /** The exact version on screen; null when no single retained version is (a file, unrecorded edits). */
  readonly shown: ShownModel | null;
  /** Whether the pick was resolved against that same shown version. */
  readonly pickMatchesShown: boolean;
  readonly board: BoardInfo;
  /** The shown version's line; without it, only data written for the shown version itself applies. */
  readonly lineage?: ShownLineage | null;
  /** The line is still being read: data written for another version waits for it instead of being called another line's. */
  readonly lineagePending?: boolean;
  /** What changed since the versions inherited data was written for, as far as it is known yet. */
  readonly changes?: ChangesSince;
}

const byOrder = (left: ComponentInfoDataset, right: ComponentInfoDataset) =>
  (left.order ?? 0) - (right.order ?? 0) || left.id.localeCompare(right.id);

function cardFields(fields: readonly InfoField[] | undefined, dataset: ComponentInfoDataset,
  statusWord: (status: FieldStatus) => string): CardField[] {
  return (fields ?? []).map((field) => {
    const source = field.source ? dataset.sources?.find((item) => item.id === field.source) : undefined;
    return { ...field, statusLabel: field.status ? dataset.statusLabels?.[field.status] ?? statusWord(field.status) : null,
      sourceLabel: source?.label ?? null, sourceDate: field.date ?? source?.date ?? null };
  });
}

function cardBlock(dataset: ComponentInfoDataset, componentId: string, statusWord: (status: FieldStatus) => string): CardBlock {
  const entry = Object.hasOwn(dataset.components, componentId) ? dataset.components[componentId]! : null;
  const group = entry?.group ? dataset.groups?.[entry.group] : undefined;
  return {
    datasetId: dataset.id, title: dataset.title, role: dataset.role, note: dataset.note ?? null,
    entry: entry ? { fields: cardFields(entry.fields, dataset, statusWord), notes: entry.notes ?? null } : null,
    group: entry?.group && group ? {
      id: entry.group, title: group.title, fields: cardFields(group.fields, dataset, statusWord), shared: group.shared ?? false,
      sharedBy: group.sharedBy ?? null, note: group.note ?? null,
      allocation: group.allocation ? { basis: group.allocation.basis, fields: cardFields(group.allocation.fields, dataset, statusWord) } : null,
    } : null,
    missing: entry ? null : dataset.missing ?? "",
  };
}

const cardDataset = (item: BoardDataset, inherited = false): CardDataset => ({ elementId: item.elementId, id: item.dataset.id,
  title: item.dataset.title, role: item.dataset.role, preparedAt: item.dataset.preparedAt, appliesTo: item.dataset.appliesTo,
  inherited });

/** The versions inherited datasets name, in the words the datasets give them. */
export const versionLabel = (appliesTo: AppliesTo) => appliesTo.versionLabel ?? appliesTo.runId;

/**
 * Decide what the card shows. Data is shown only when the board has been read, the pick was
 * resolved against the exact version on screen and a dataset was written for that version or
 * for one it continued from on its line (inherited, and flagged where the component's shape
 * changed since); otherwise the card says which of these is missing and shows the model's own
 * label and ids. `statusWord` words a status a dataset did not word itself.
 */
export function buildComponentCard(input: CardInput, statusWord: (status: FieldStatus) => string): ComponentCard {
  const { board, componentId, shown } = input;
  const ready = board.status === "ready" ? board : null;
  const datasets = ready?.datasets ?? [];
  const usable = shown !== null && input.pickMatchesShown;
  const relation = (item: BoardDataset) => datasetRelation(item.dataset, shown, input.lineage ?? null);
  const used = usable ? datasets.filter((item) => relation(item) !== null) : [];
  const mismatched = usable ? datasets.filter((item) => relation(item) === null) : [];
  const inherited = used.filter((item) => relation(item) === "inherited");
  // A shape this component no longer has is the one thing inherited data may describe wrongly.
  const reshapedSince = [...new Set(inherited.filter((item) => {
    const changed = input.changes?.get(item.dataset.appliesTo.runId);
    return changed !== undefined && changed !== "unknown" && changed.get(componentId) === true;
  }).map((item) => versionLabel(item.dataset.appliesTo)))];
  const state: CardState = board.status === "error" ? "error"
    : ready === null ? "loading"
    : datasets.length === 0 ? "none"
    : !usable ? "unverified"
    : used.length === 0 ? (input.lineagePending ? "loading" : "mismatch") : "ready";
  const ordered = used.map((item) => item.dataset).sort(byOrder);
  const summary = ordered.filter((dataset) => dataset.role === "summary").map((dataset) => cardBlock(dataset, componentId, statusWord));
  const sections = ordered.filter((dataset) => dataset.role === "section").map((dataset) => cardBlock(dataset, componentId, statusWord));
  const named = [...ordered.filter((dataset) => dataset.role === "summary"), ...ordered.filter((dataset) => dataset.role === "section")]
    .map((dataset) => Object.hasOwn(dataset.components, componentId) ? dataset.components[componentId]!.name : undefined)
    .find((name) => name !== undefined && name.trim() !== "");
  return {
    componentId, elementId: input.elementId, name: named ?? input.modelLabel, state, shown,
    boardRevision: ready?.revisionSha256 ?? null, summary, sections,
    used: used.map((item) => cardDataset(item, inherited.includes(item))), mismatched: mismatched.map((item) => cardDataset(item)),
    invalid: ready?.invalid ?? [],
    sources: ordered.flatMap((dataset) => (dataset.sources ?? []).map((source) => ({ ...source, datasetTitle: dataset.title }))),
    reshapedSince,
  };
}

/** The card's own words, by catalog key; the caller passes the interface's translator. */
export type CardWords = (key: MessageKey, parameters?: Readonly<Record<string, string | number>>) => string;

/** The distinct version labels datasets on the Board were written for, for the mismatch sentence. */
export function mismatchLabels(card: ComponentCard): string[] {
  return [...new Set(card.mismatched.map((item) => item.appliesTo.versionLabel ?? item.appliesTo.runId))];
}

/** The one sentence a card in a state other than ready opens with, or null. */
export function stateSentence(card: ComponentCard, t: CardWords): string | null {
  switch (card.state) {
    case "loading": return t("componentInfo.state.loading");
    case "error": return t("componentInfo.state.error");
    // Cards that cannot be read are not "nothing imported": they were, and need fixing.
    case "none": return card.invalid.length ? t("componentInfo.invalidCards", { count: card.invalid.length }) : t("componentInfo.state.none");
    case "unverified": return t("componentInfo.state.unverified");
    case "mismatch": return t("componentInfo.state.mismatch", { labels: mismatchLabels(card).join(t("componentInfo.listSeparator")) });
    default: return null;
  }
}

/** The one line a ready card adds when inherited data may describe a shape this component no longer has. */
export function reshapedSentence(card: ComponentCard, t: CardWords): string {
  return t("componentInfo.reshapedSince", { labels: card.reshapedSince.join(t("componentInfo.listSeparator")) });
}

/** A field's value as printed: the value, then its unit. */
export const fieldValue = (field: InfoField) => field.unit ? `${field.value} ${field.unit}` : String(field.value);

/** The status word beside a value, unless the value already says it (a value "to confirm" marked to-confirm). */
export const statusBadge = (field: CardField): string | null =>
  field.statusLabel && field.statusLabel !== fieldValue(field) ? field.statusLabel : null;

/** Where a field comes from, as one line: the source and its date. */
export const fieldSource = (field: CardField): string => [field.sourceLabel, field.sourceDate].filter(Boolean).join(" · ");

function fieldText(field: CardField, t: CardWords): string {
  let line = `${field.label}: ${fieldValue(field)}`;
  const badge = statusBadge(field);
  if (badge) line += ` [${badge}]`;
  if (field.url) line += ` <${field.url}>`;
  if (field.note) line += ` — ${field.note}`;
  const source = fieldSource(field);
  if (source) line += ` · ${t("componentInfo.source", { source })}`;
  return line;
}

function blockText(block: CardBlock, t: CardWords, heading: boolean): string[] {
  const lines = heading ? [`【${block.title}】`] : [];
  if (block.note) lines.push(block.note);
  if (block.entry) {
    lines.push(...block.entry.fields.map((field) => fieldText(field, t)));
    if (block.entry.notes) lines.push(block.entry.notes);
  } else lines.push(block.missing || t("componentInfo.missing", { title: block.title }));
  if (block.group) {
    lines.push(`${t("componentInfo.group")}: ${block.group.title}`);
    if (block.group.shared) lines.push(block.group.sharedBy !== null
      ? t("componentInfo.sharedBy", { count: block.group.sharedBy }) : t("componentInfo.shared"));
    lines.push(...block.group.fields.map((field) => `  ${fieldText(field, t)}`));
    if (block.group.allocation) {
      lines.push(`  ${t("componentInfo.allocation", { basis: block.group.allocation.basis })}`);
      lines.push(...block.group.allocation.fields.map((field) => `  ${fieldText(field, t)}`));
    }
    if (block.group.note) lines.push(`  ${block.group.note}`);
  }
  return lines;
}

/**
 * The card as plain text, for Copy: everything it holds, folded notes and technical section alike,
 * so a pasted card can be checked against the model it came from.
 */
export function componentCardText(card: ComponentCard, t: CardWords): string {
  const lines = [card.name];
  const sentence = stateSentence(card, t);
  if (sentence) lines.push(sentence);
  if (card.state === "ready" && card.reshapedSince.length) lines.push(reshapedSentence(card, t));
  if (card.state === "ready") {
    for (const block of card.summary) lines.push(...blockText(block, t, card.summary.length > 1));
    for (const block of card.sections) lines.push("", ...blockText(block, t, true));
  }
  lines.push("", `【${t("componentInfo.technical")}】`, `${t("componentInfo.componentId")}: ${card.componentId}`);
  if (card.elementId) lines.push(`${t("componentInfo.elementId")}: ${card.elementId}`);
  lines.push(card.shown
    ? `${t("componentInfo.shown")}: ${card.shown.projectId} · ${card.shown.runId} · ${card.shown.stateDigest}`
    : `${t("componentInfo.shown")}: ${t("componentInfo.shownNone")}`);
  for (const item of card.used) lines.push(`${t("componentInfo.dataset")}: ${item.title} (${item.id}) · ${item.preparedAt} · ${item.inherited
    ? t("componentInfo.inheritedFrom", { label: versionLabel(item.appliesTo) }) : versionLabel(item.appliesTo)}`);
  for (const item of card.mismatched) lines.push(`${t("componentInfo.mismatchedDataset")}: ${item.title} (${item.id}) · ${item.appliesTo.versionLabel ?? ""} · ${item.appliesTo.runId} · ${item.appliesTo.stateDigest}`);
  for (const source of card.sources) lines.push(`${t("componentInfo.sourceLine")}: ${source.label}${source.date ? ` · ${source.date}` : ""}${source.kind ? ` · ${source.kind}` : ""}`);
  if (card.invalid.length) lines.push(t("componentInfo.invalidCards", { count: card.invalid.length }));
  return lines.join("\n");
}

/** A problem in the interface's words. */
export function problemText(problem: Problem, t: CardWords): string {
  return t(`componentInfo.problem.${problem.code}`, { path: problem.path });
}
