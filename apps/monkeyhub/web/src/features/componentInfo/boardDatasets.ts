/**
 * Component information on the Board (#549): each dataset is one visible card, a rectangle whose
 * customData carries the dataset and whose bound label says what it is. The card is an ordinary
 * Board element, saved, undone and versioned with the scene; deleting it removes that topic.
 *
 * A card is found by its customData marker, never by its position or text. Its element id is
 * derived from the dataset id, so importing a dataset again replaces its card in place and a new
 * dataset id adds a card. No Excalidraw import here: the Board hands in its own element maker.
 */

import { readDataset, type BoardDataset, type CardWords, type ComponentInfoDataset, type InvalidCard } from "./componentInfo";

/** The customData key a card carries its dataset under. */
export const COMPONENT_INFO_KEY = "componentInfo";

export const cardElementId = (datasetId: string) => `component-info:${datasetId}`;
export const cardLabelId = (datasetId: string) => `component-info:${datasetId}:label`;

type SceneElement = Readonly<Record<string, unknown>>;
type Json = Record<string, unknown>;
const isObject = (value: unknown): value is Json => value !== null && typeof value === "object" && !Array.isArray(value);

/** The document an element carries under the marker, unread; undefined when it is not a card. */
export function carriedDataset(element: SceneElement): unknown {
  const custom = element.customData;
  return isObject(custom) && Object.hasOwn(custom, COMPONENT_INFO_KEY) ? custom[COMPONENT_INFO_KEY] : undefined;
}

/** The ids of every element carrying a dataset, deleted ones included (their labels stay theirs). */
export function componentInfoCardIds(elements: readonly SceneElement[]): Set<string> {
  return new Set(elements.filter((element) => carriedDataset(element) !== undefined).map((element) => String(element.id)));
}

/** A card, or the words bound to one: Board actions on marks (Clear annotations) leave both alone. */
export function isComponentInfoElement(element: SceneElement, cardIds: ReadonlySet<string>): boolean {
  return carriedDataset(element) !== undefined || (typeof element.containerId === "string" && cardIds.has(element.containerId));
}

/**
 * Every dataset on the Board's live cards. A card that cannot be read is named with its problems
 * rather than dropped silently; two live cards with one dataset id and different content (a copy
 * edited by hand) are both refused, since neither can be told to be the current one.
 */
export function readBoardDatasets(elements: readonly SceneElement[]): { datasets: BoardDataset[]; invalid: InvalidCard[] } {
  const found = new Map<string, { elementId: string; dataset: ComponentInfoDataset; text: string }[]>();
  const invalid: InvalidCard[] = [];
  for (const element of elements) {
    if (element.isDeleted === true) continue;
    const carried = carriedDataset(element);
    if (carried === undefined) continue;
    const elementId = String(element.id);
    const read = readDataset(carried);
    if (!read.dataset) {
      const datasetId = isObject(carried) && typeof carried.id === "string" ? carried.id : null;
      invalid.push({ elementId, datasetId, problems: read.problems });
      continue;
    }
    const rows = found.get(read.dataset.id) ?? [];
    rows.push({ elementId, dataset: read.dataset, text: JSON.stringify(carried) });
    found.set(read.dataset.id, rows);
  }
  const datasets: BoardDataset[] = [];
  for (const [datasetId, rows] of found) {
    if (rows.every((row) => row.text === rows[0]!.text)) {
      const canonical = rows.find((row) => row.elementId === cardElementId(datasetId)) ?? rows[0]!;
      datasets.push({ elementId: canonical.elementId, dataset: canonical.dataset });
    } else {
      for (const row of rows) invalid.push({ elementId: row.elementId, datasetId, problems: [{ code: "duplicate", path: "$.id" }] });
    }
  }
  return { datasets, invalid };
}

/** The words on a card: what it is, which version and how much it covers, and its date. */
export function cardLabelText(dataset: ComponentInfoDataset, t: CardWords): string {
  const groups = Object.keys(dataset.groups ?? {}).length;
  return [
    t("componentInfo.board.cardTitle", { title: dataset.title }),
    [dataset.appliesTo.versionLabel, t("componentInfo.board.cardComponents", { count: Object.keys(dataset.components).length }),
      groups ? t("componentInfo.board.cardGroups", { count: groups }) : null].filter(Boolean).join(" · "),
    t("componentInfo.board.cardDate", { date: dataset.preparedAt }),
  ].join("\n");
}

/** The card a dataset is shown as, in Excalidraw's element-skeleton terms. */
export interface CardSkeleton {
  readonly type: "rectangle";
  readonly id: string;
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly version?: number;
  readonly strokeColor: string;
  readonly backgroundColor: string;
  readonly fillStyle: "solid";
  readonly roughness: 0;
  readonly strokeWidth: 1;
  readonly roundness: null;
  readonly customData: { readonly [COMPONENT_INFO_KEY]: ComponentInfoDataset };
  readonly label: { readonly text: string; readonly id: string; readonly fontSize: number; readonly fontFamily: number;
    readonly textAlign: "left"; readonly verticalAlign: "middle"; readonly version?: number };
}

export const CARD_WIDTH = 380;
export const CARD_HEIGHT = 112;
const CARD_GAP = 24;

export function cardSkeleton(dataset: ComponentInfoDataset, text: string, at: { x: number; y: number },
  previous?: { card?: SceneElement; label?: SceneElement }): CardSkeleton {
  const number = (value: unknown, fallback: number) => typeof value === "number" && Number.isFinite(value) ? value : fallback;
  const card = previous?.card;
  return {
    type: "rectangle", id: cardElementId(dataset.id),
    x: number(card?.x, at.x), y: number(card?.y, at.y),
    width: Math.max(number(card?.width, CARD_WIDTH), 160), height: Math.max(number(card?.height, CARD_HEIGHT), 64),
    ...(card ? { version: number(card.version, 1) + 1 } : {}),
    strokeColor: "#29352d", backgroundColor: "#fff9db", fillStyle: "solid", roughness: 0, strokeWidth: 1, roundness: null,
    customData: { [COMPONENT_INFO_KEY]: dataset },
    label: { text, id: cardLabelId(dataset.id), fontSize: 16, fontFamily: 2, textAlign: "left", verticalAlign: "middle",
      ...(previous?.label ? { version: number(previous.label.version, 1) + 1 } : {}) },
  };
}

/**
 * The scene after placing these datasets: a dataset whose card exists (even deleted) is replaced in
 * place, keeping where the card was put, its order, frame and group; a new dataset id gets a new
 * card beside the board's content. Other live cards carrying the same dataset id (a hand-made copy)
 * are retired, so one dataset id has one card. `convert` makes the elements of one skeleton (the
 * card, then its label); `retire` marks an element deleted. Every other element is left as it is.
 */
export function placeDatasetCards<E extends SceneElement>(elements: readonly E[], datasets: readonly ComponentInfoDataset[], tools: {
  at: { x: number; y: number };
  label: (dataset: ComponentInfoDataset) => string;
  convert: (skeleton: CardSkeleton) => readonly E[];
  retire: (element: E) => E;
}): { elements: E[]; added: string[]; replaced: string[] } {
  let scene = [...elements];
  const added: string[] = [], replaced: string[] = [];
  let next = 0;
  for (const dataset of datasets) {
    const cardId = cardElementId(dataset.id), labelId = cardLabelId(dataset.id);
    // A hand-made copy of this card goes, with the words bound to it.
    const copies = new Set(scene.filter((element) => element.isDeleted !== true && String(element.id) !== cardId &&
      isObject(carriedDataset(element)) && (carriedDataset(element) as Json).id === dataset.id).map((element) => String(element.id)));
    scene = scene.map((element) => copies.has(String(element.id)) ||
      (typeof element.containerId === "string" && copies.has(element.containerId) && element.isDeleted !== true)
      ? tools.retire(element) : element);
    const card = scene.find((element) => String(element.id) === cardId);
    const label = scene.find((element) => String(element.id) === labelId);
    const at = { x: tools.at.x, y: tools.at.y + next * (CARD_HEIGHT + CARD_GAP) };
    const [made, words, ...rest] = tools.convert(cardSkeleton(dataset, tools.label(dataset), at, { card, label }));
    if (!made || !words || rest.length) throw new Error("A component information card is one rectangle and its label.");
    if (card) {
      // In place: the card keeps its z-order, frame, group, lock, angle and any arrows bound to it.
      const kept = Array.isArray(card.boundElements)
        ? (card.boundElements as Json[]).filter((bound) => isObject(bound) && bound.type !== "text") : [];
      const madeBound = Array.isArray(made.boundElements) ? made.boundElements as Json[] : [];
      const placed = { ...made, index: card.index ?? made.index, frameId: card.frameId ?? null, groupIds: card.groupIds ?? made.groupIds,
        locked: card.locked ?? made.locked, angle: card.angle ?? made.angle, boundElements: [...kept, ...madeBound] } as E;
      // A label someone else bound to the card (not ours) is retired; ours is replaced in place.
      scene = scene.map((element) => element === card ? placed
        : element.containerId === cardId && String(element.id) !== labelId && element.isDeleted !== true ? tools.retire(element) : element);
      const boundWords = { ...words, frameId: card.frameId ?? null, groupIds: card.groupIds ?? words.groupIds } as E;
      if (label) scene = scene.map((element) => element === label ? { ...boundWords, index: label.index ?? words.index } as E : element);
      else scene.splice(scene.indexOf(placed) + 1, 0, boundWords);
      replaced.push(dataset.id);
    } else {
      if (label) scene = scene.filter((element) => element !== label);
      scene.push(made, words);
      next += 1;
      added.push(dataset.id);
    }
  }
  return { elements: scene, added, replaced };
}

/** The UTF-8 size of a scene as the Board stores it, against its 8 MiB limit. */
export const BOARD_SCENE_LIMIT_BYTES = 8 * 1024 * 1024;
export function sceneBytes(content: unknown): number {
  return new TextEncoder().encode(JSON.stringify(content)).length;
}
