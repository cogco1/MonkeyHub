/**
 * Server thumbnails of exact models (#367, ADR-008): the projection cache's line drawings,
 * one image per model shared by every surface that shows it (#409).
 *
 * Where a thumbnail is: the project store holds every done projection as an index entity
 * (`projections:<key>`), so a surface finds its model's blob digest there with no request.
 * A model the store has no thumbnail for is asked for once (`askThumbnail`): the server
 * checks the source and queues the drawing, and answers pending; the placeholder shows
 * until the store hears the projection is done (`index.committed`, domain `projections`).
 * Nothing polls, and a placeholder is never kept as an image.
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

interface Asked { readonly at: number; readonly answer: Promise<string | null> }

// Per runtime client: the sources asked for, and the blobs their answers named (for a page whose store cannot read).
const askedOf = new WeakMap<object, Map<string, Asked>>();
/** How long a pending answer stands before a surface that shows the model again may ask again. */
const ASK_AGAIN_MS = 30_000;

const askKey = (source: ModelSourceDto) => `${source.runId}\n${source.stateDigest}\n${source.assetSha256}`;

/**
 * Ask the server for this model's thumbnail: its blob digest when it is done, else null (pending, or a source
 * that cannot be drawn). One request per model while its answer stands; a failed request is not kept.
 */
export function askThumbnail(studio: Studio, source: ModelSourceDto, now = Date.now()): Promise<string | null> {
  let asked = askedOf.get(studio);
  if (!asked) { asked = new Map(); askedOf.set(studio, asked); }
  const key = askKey(source);
  const kept = asked.get(key);
  if (kept && now - kept.at < ASK_AGAIN_MS) return kept.answer;
  const answer = studio.projection(source, THUMBNAIL_SIZE).then(
    (status) => status.status === "done" && status.blobSha256 ? status.blobSha256 : null,
    () => { asked!.delete(key); return null; });
  // A done answer stands for good (the digest names the bytes); a pending one until it is old.
  asked.set(key, { at: now, answer });
  void answer.then((blob) => { if (blob && asked!.get(key)?.answer === answer) asked!.set(key, { at: Infinity, answer }); });
  return answer;
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
      return null;
    }
    entry.decoded = { blobSha256, url, width: image.naturalWidth, height: image.naturalHeight };
    trim(images, limit);
    return entry.decoded;
  }).catch(() => {
    // Not kept: the next surface that shows it reads it again.
    if (images.get(blobSha256) === entry) images.delete(blobSha256);
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
