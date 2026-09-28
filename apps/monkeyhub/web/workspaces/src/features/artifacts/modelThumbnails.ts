/**
 * Server thumbnails of exact models (#367, ADR-008): the projection cache's line drawings,
 * one image per model shared by every surface that shows it (#409).
 *
 * Where a thumbnail is: the project store holds every done projection as an index entity
 * (`projections:<key>`), so a surface finds its model's blob digest there with no request.
 * A model the store has no thumbnail for is asked for once (`askThumbnail`): the server
 * checks the source and queues the drawing, and answers pending; the placeholder shows
 * until the store hears the projection is done (`index.committed`, domain `projections`).
 * An ask the server refuses or cannot answer (409, 503, offline) is kept like a pending one, and
 * waits longer each time it fails again. Nothing polls, and a placeholder is never kept as an image.
 * A blob that cannot be read (the server lost it and draws it again) is never kept either: the
 * answers that named it are forgotten, so the model is asked for again.
 *
 * The images: each blob is downloaded once per page and decoded once, whoever asks first
 * (the Design Tree's canvas, its list, the inspector's `ModelThumbnail`). The decoded
 * images are kept in a bounded cache, least recently used first out. The canvas draws a
 * copy scaled to its cells (`canvasThumbnail`), kept with the image.
 */
import type { StudioClient } from "../../api/client";
import type { ModelSourceDto } from "../../api/generated";
import type { IndexEntity, ProjectStoreState } from "../../api/projectStore";

/** The size the server draws the Design Tree's thumbnails at (its `TREE_RECIPE`): about twice a close card. */
export const THUMBNAIL_SIZE = 256;
/** The projection kind: the axonometric line drawing of one complete model. */
const KIND = "model-line-view";
/** Decoded images kept at once; a card that comes back after its image left reads it again (from the browser's cache). */
export const THUMBNAIL_CACHE_LIMIT = 48;

/** One model's thumbnail, decoded: an object URL for `<img>` and its size. */
export interface ThumbnailImage {
  readonly blobSha256: string;
  readonly url: string;
  readonly width: number;
  readonly height: number;
}

/** A thumbnail drawn at a size a canvas cell needs, as a data URL Excalidraw's files take. */
export interface CanvasThumbnail {
  readonly fileId: string;
  readonly dataURL: string;
  readonly mimeType: string;
  readonly width: number;
  readonly height: number;
}

type Studio = Pick<StudioClient, "projection" | "projectionBlob">;

interface Projection { readonly inputSha256?: unknown; readonly kind?: unknown; readonly blobSha256?: unknown; readonly recipe?: unknown }

const blobsOf = new WeakMap<ReadonlyMap<string, IndexEntity>, ReadonlyMap<string, string>>();

/**
 * The blob of each model's done thumbnail the store holds, by the model's asset digest. When two renderers'
 * pictures of one model are both held (a new renderer is replacing the old one's), the newer commit's.
 */
export function thumbnailBlobs(state: ProjectStoreState): ReadonlyMap<string, string> {
  let found = blobsOf.get(state.byId);
  if (!found) {
    const best = new Map<string, { rev: number; blob: string }>();
    for (const entity of state.byId.values()) {
      if (entity.domain !== "projections") continue;
      const body = entity.body as Projection;
      const recipe = body.recipe as { size?: unknown; view?: unknown } | null | undefined;
      if (body.kind !== KIND || recipe?.size !== THUMBNAIL_SIZE || recipe?.view !== "axon"
        || typeof body.inputSha256 !== "string" || typeof body.blobSha256 !== "string") continue;
      const kept = best.get(body.inputSha256);
      if (!kept || entity.rev > kept.rev) best.set(body.inputSha256, { rev: entity.rev, blob: body.blobSha256 });
    }
    found = new Map([...best].map(([input, { blob }]) => [input, blob]));
    blobsOf.set(state.byId, found);
  }
  return found;
}

/** Where the store's thumbnails last moved: a surface draws again when this changes, and not otherwise. */
export function thumbnailsMoved(state: ProjectStoreState): string {
  return `${state.epoch ?? ""}:${state.domains.projections ?? 0}`;
}

interface Asked {
  /** Until when the answer stands: for good once done. */
  until: number;
  /** How many asks in a row failed: each waits twice as long as the one before. */
  failures: number;
  /** The blob a done answer named. */
  blob: string | null;
  readonly answer: Promise<string | null>;
}

// Per runtime client: the sources asked for, and the blobs their answers named (for a page whose store cannot read).
const askedOf = new WeakMap<object, Map<string, Asked>>();
/** How long a pending or failed answer stands before a surface that shows the model again may ask again. */
export const ASK_AGAIN_MS = 30_000;
/** The longest a model whose asks keep failing waits before it is asked again. */
const ASK_AGAIN_MAX_MS = 16 * ASK_AGAIN_MS;

const askKey = (source: ModelSourceDto) => `${source.runId}\n${source.stateDigest}\n${source.assetSha256}`;

/**
 * Ask the server for this model's thumbnail: its blob digest when it is done, else null (pending, or a source
 * that cannot be drawn). One request per model while its answer stands: a done answer for good, a pending one
 * for `ASK_AGAIN_MS`, a failed one for `ASK_AGAIN_MS` doubled with each failure in a row.
 */
export function askThumbnail(studio: Studio, source: ModelSourceDto, now = Date.now()): Promise<string | null> {
  let asked = askedOf.get(studio);
  if (!asked) { asked = new Map(); askedOf.set(studio, asked); }
  const answers = asked;
  const key = askKey(source);
  const kept = answers.get(key);
  if (kept && now < kept.until) return kept.answer;
  const failures = kept?.failures ?? 0;
  const entry: Asked = { until: now + ASK_AGAIN_MS, failures, blob: null, answer: studio.projection(source, THUMBNAIL_SIZE).then(
    (status) => {
      const blob = status.status === "done" && status.blobSha256 ? status.blobSha256 : null;
      if (answers.get(key) === entry) Object.assign(entry, { failures: 0, blob, until: blob ? Infinity : entry.until });
      return blob;
    },
    () => {
      if (answers.get(key) === entry) {
        entry.failures = failures + 1;
        entry.until = now + Math.min(ASK_AGAIN_MAX_MS, ASK_AGAIN_MS * 2 ** failures);
      }
      return null;
    }) };
  answers.set(key, entry);
  return entry.answer;
}

/** The blob could not be read: forget the done answers that named it, so its model is asked for again. */
export function forgetThumbnailBlob(studio: object, blobSha256: string): void {
  const asked = askedOf.get(studio);
  if (!asked) return;
  for (const [key, entry] of asked) if (entry.blob === blobSha256) asked.delete(key);
}

/** A done answer this page was given for the model, when the store does not hold it (yet). */
export function askedThumbnail(studio: Studio, source: ModelSourceDto): Promise<string | null> | null {
  return askedOf.get(studio)?.get(askKey(source))?.answer ?? null;
}

interface Kept { image: Promise<ThumbnailImage | null>; decoded: ThumbnailImage | null; canvas: Map<string, Promise<CanvasThumbnail | null>> }

const imagesOf = new WeakMap<object, Map<string, Kept>>();

function kept(studio: Studio): Map<string, Kept> {
  let images = imagesOf.get(studio);
  if (!images) { images = new Map(); imagesOf.set(studio, images); }
  return images;
}

/** Keep at most the limit, the least recently used going first with its object URL; one still on its way stays. */
function trim(images: Map<string, Kept>, limit: number): void {
  for (const [blob, entry] of images) {
    if (images.size <= limit) return;
    if (!entry.decoded) continue;
    images.delete(blob);
    URL.revokeObjectURL(entry.decoded.url);
  }
}

/** The thumbnail with this digest, downloaded and decoded once for every surface; null when it cannot be read. */
export function thumbnailImage(studio: Studio, blobSha256: string, limit = THUMBNAIL_CACHE_LIMIT): Promise<ThumbnailImage | null> {
  const images = kept(studio);
  const found = images.get(blobSha256);
  if (found) {
    images.delete(blobSha256);
    images.set(blobSha256, found);  // used now: last out
    return found.image;
  }
  const entry: Kept = { decoded: null, canvas: new Map(), image: Promise.resolve(null) };
  entry.image = studio.projectionBlob(blobSha256).then(async (blob) => {
    const url = URL.createObjectURL(blob);
    const image = new Image();
    image.src = url;
    try {
      await image.decode();
    } catch {
      URL.revokeObjectURL(url);
      if (images.get(blobSha256) === entry) images.delete(blobSha256);
      forgetThumbnailBlob(studio, blobSha256);
      return null;
    }
    entry.decoded = { blobSha256, url, width: image.naturalWidth, height: image.naturalHeight };
    trim(images, limit);
    return entry.decoded;
  }).catch(() => {
    // Not kept: the next surface that shows it reads it again, and its model is asked for again.
    if (images.get(blobSha256) === entry) images.delete(blobSha256);
    forgetThumbnailBlob(studio, blobSha256);
    return null;
  });
  images.set(blobSha256, entry);
  trim(images, limit);
  return entry.image;
}

/**
 * The thumbnail scaled to fit `width` × `height` pixels (a canvas cell at about twice its size), drawn once
 * per size from the shared image. Never larger than the image itself.
 */
export function canvasThumbnail(studio: Studio, blobSha256: string, width: number, height: number): Promise<CanvasThumbnail | null> {
  return thumbnailImage(studio, blobSha256).then((image) => {
    if (!image) return null;
    const entry = kept(studio).get(blobSha256);
    const size = `${width}x${height}`;
    const made = entry?.canvas.get(size);
    if (made) return made;
    const drawn = (async (): Promise<CanvasThumbnail | null> => {
      const element = new Image();
      element.src = image.url;
      await element.decode();
      const scale = Math.min(1, width / image.width, height / image.height);
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(image.width * scale));
      canvas.height = Math.max(1, Math.round(image.height * scale));
      const context = canvas.getContext("2d");
      if (!context) return null;
      context.imageSmoothingQuality = "high";
      context.drawImage(element, 0, 0, canvas.width, canvas.height);
      return { fileId: `tree-thumbnail-${blobSha256.slice(0, 32)}-${size}`, dataURL: canvas.toDataURL("image/png"),
        mimeType: "image/png", width: canvas.width, height: canvas.height };
    })().catch(() => null);
    entry?.canvas.set(size, drawn);
    return drawn;
  });
}

/** For tests: how many decoded thumbnails this client keeps now. */
export function keptThumbnails(studio: object): number {
  return imagesOf.get(studio)?.size ?? 0;
}
