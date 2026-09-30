/**
 * One candidate beside the exact model it was made from (#284).
 *
 * The "before" is never the model on screen, the tree's parent node or the
 * Working Head. It is the run whose retained exports carry the state digest the
 * candidate's retained change was executed against (`RuntimeCandidateDto.
 * baseStateDigest`, read from its StudioCandidateDelta@1). A state digest is a
 * binding identity - content bound to one run - so it names one run's models or
 * none; when it names none, or several runs, the comparison says so instead of
 * choosing.
 *
 * Pure functions over the wire types, so the node test runner can exercise them.
 * Nothing here reads or writes the project.
 */

import type { CompareDto, ProjectArtifactDto, RuntimeDto } from "../../../api/project-runtime/generated";
import type { CameraState, ViewBounds, ViewportStatus } from "./ThreeDmViewport";

/** A listed model the viewer can be handed: servable and a `.3dm` (see artifactSelection.viewableArtifacts). */
export type ComparisonModel = ProjectArtifactDto & { sha256: string };

export interface ComparisonSide {
  readonly runId: string;
  readonly stateDigest: string;
  /** One complete model, or the run's seat exports opened together as one picture. */
  readonly models: readonly ComparisonModel[];
}

export interface ComparisonPair {
  readonly candidate: ComparisonSide;
  readonly base: ComparisonSide;
}

/**
 * Why no exact pair can be shown. `no-retained-base`: the candidate keeps no
 * retained change naming what it was made from (an imported or older result).
 * `base-no-model`: that source has no viewable model, such as an authored
 * starting state. `*-ambiguous`: the retained facts name more than one picture.
 */
export type ComparisonRefusal =
  | "project-changed"
  | "candidate-unknown"
  | "candidate-unfinished"
  | "candidate-no-model"
  | "candidate-ambiguous"
  | "no-retained-base"
  | "base-no-model"
  | "base-ambiguous";

/**
 * The state a row's own records bind it to. The wire can state it twice, as
 * the export's `designStateDigest` and as its `modelSource`, and the two agree
 * for every row the server writes; a row stating only one keeps that one. A
 * row whose two disagree, or whose modelSource is another run's or another
 * file's model, certifies no state: half of it matching proves nothing.
 */
function boundState(row: ProjectArtifactDto): string | null {
  const source = row.modelSource ?? null;
  if (source && (source.runId !== row.runId || source.assetSha256 !== row.sha256)) return null;
  if (source && row.designStateDigest && source.stateDigest !== row.designStateDigest) return null;
  return row.designStateDigest || source?.stateDigest || null;
}

/** The picture of one run at one state: its one complete model, else its seats, each once. */
function pictureOf(rows: readonly ComparisonModel[]): readonly ComparisonModel[] | "none" | "ambiguous" {
  if (rows.length === 0) return "none";
  const unique = [...new Map(rows.map((row) => [row.sha256, row])).values()];
  const composed = unique.filter((row) => row.representation === "composed");
  if (composed.length === 1) return composed;
  if (composed.length > 1) return "ambiguous";
  const seats = new Set(unique.map((row) => row.stageId));
  return seats.size === unique.length ? unique : "ambiguous";
}

export function resolveComparisonPair(
  projectId: string,
  candidateRunId: string,
  runtime: Pick<RuntimeDto, "projectId" | "candidates">,
  artifacts: { readonly projectId: string; readonly viewable: readonly ComparisonModel[] },
): { pair: ComparisonPair } | { refusal: ComparisonRefusal } {
  if (runtime.projectId !== projectId || artifacts.projectId !== projectId) return { refusal: "project-changed" };
  const row = runtime.candidates.find((candidate) => candidate.candidateId === candidateRunId);
  if (!row) return { refusal: "candidate-unknown" };
  if (row.status !== "completed") return { refusal: "candidate-unfinished" };
  if (!row.resultStateDigest) return { refusal: "candidate-no-model" };
  if (!row.baseStateDigest) return { refusal: "no-retained-base" };
  const after = pictureOf(artifacts.viewable.filter((model) => model.runId === candidateRunId && boundState(model) === row.resultStateDigest));
  if (after === "none") return { refusal: "candidate-no-model" };
  if (after === "ambiguous") return { refusal: "candidate-ambiguous" };
  const sources = artifacts.viewable.filter((model) => boundState(model) === row.baseStateDigest);
  const runs = new Set(sources.map((model) => model.runId));
  if (runs.size === 0) return { refusal: "base-no-model" };
  if (runs.size > 1 || runs.has(candidateRunId)) return { refusal: "base-ambiguous" };
  const before = pictureOf(sources);
  if (before === "none") return { refusal: "base-no-model" };
  if (before === "ambiguous") return { refusal: "base-ambiguous" };
  return { pair: {
    candidate: { runId: candidateRunId, stateDigest: row.resultStateDigest, models: after },
    base: { runId: sources[0]!.runId, stateDigest: row.baseStateDigest, models: before },
  } };
}

/** The server's object comparison, only when it answers for exactly this pair. */
export function comparisonFor(pair: ComparisonPair, compare: CompareDto): CompareDto | null {
  return compare.candidateId === pair.candidate.runId && compare.against === pair.base.runId ? compare : null;
}

/** Objects of the original that the candidate changed or removed: what the original outline draws. */
export function originalChangedNames(compare: CompareDto | null): readonly string[] | null {
  if (!compare) return null;
  return compare.objects.filter((object) => object.status === "changed" || object.status === "removed").map((object) => object.name);
}

export type ComparisonPane = "before" | "after";
export const PANES: readonly ComparisonPane[] = ["before", "after"];

/** Where the comparison stands, for its host: only a comparison still resolving may be abandoned unseen. */
export type ComparisonPhase = "resolving" | "shown" | "refused";

/**
 * A comparison is shown once both views have settled: each holds its model,
 * or says it could not open one. Finding the pair is not enough - until then
 * neither model is on screen, and the comparison is still resolving.
 */
export function comparisonPhase(
  resolution: "resolving" | "refused" | "failed" | "ready",
  panes: Readonly<Record<ComparisonPane, ViewportStatus>>,
): ComparisonPhase {
  if (resolution === "resolving") return "resolving";
  if (resolution !== "ready") return "refused";
  return PANES.every((pane) => panes[pane] === "ready" || panes[pane] === "error") ? "shown" : "resolving";
}

/** The comparison over a project's surfaces: the run, who opened it, and which opening it is. */
export interface HostedComparison {
  readonly runId: string;
  readonly origin: "tree" | "request";
  readonly key: number;
}

/**
 * The comparison one project shows, as its host changes around it.
 *
 * A Hub request is used once. Its ids only grow, so an id no newer than the
 * last one taken - the same request again, or an older one arriving late -
 * opens nothing, while a new id opens even the same candidate again. A request
 * that arrives while the project is off screen is dropped rather than kept to
 * open later; while the project is still connecting it waits. A Hub that
 * withdraws its request (null) closes the comparison it asked for, still
 * resolving or on screen, and is not told as if the person had pressed Back;
 * one opened from the Design Tree is the person's and stays. A comparison
 * still resolving when its project leaves the screen is abandoned: what it
 * reports afterwards is not heard, and it never appears later.
 */
export function createComparisonHost() {
  let shown: HostedComparison | null = null;
  let phase: ComparisonPhase = "resolving";
  let taken: number | null = null;
  let keys = 0;
  const open = (runId: string, origin: HostedComparison["origin"]) => {
    shown = { runId, origin, key: ++keys };
    phase = "resolving";
    return shown;
  };
  return {
    /** What covers the project's surfaces now; null shows the surface itself. */
    shown: () => shown,
    /** A person's Compare in this project. */
    open: (runId: string) => open(runId, "tree"),
    /** The Hub's request as it now stands, whenever it or the project around it changes. */
    request(
      request: { readonly candidateRunId: string; readonly requestId: number } | null | undefined,
      project: { readonly active: boolean; readonly ready: boolean },
    ): "none" | "wait" | "drop" | "open" | "withdraw" {
      if (!request) {
        if (shown?.origin !== "request") return "none";
        shown = null;
        return "withdraw";
      }
      if (taken !== null && request.requestId <= taken) return "none";
      if (!project.active) { taken = request.requestId; return "drop"; }
      if (!project.ready) return "wait";
      taken = request.requestId;
      open(request.candidateRunId, "request");
      return "open";
    },
    /** What the comparison opened as `key` says of itself; a closed or replaced one is not heard. */
    report(key: number, next: ComparisonPhase) { if (shown?.key === key) phase = next; },
    /** The project left the screen. */
    leave() { if (phase === "resolving") shown = null; },
    /** Back: closed, and the Hub is told only when it asked for this comparison. */
    back(tell?: () => void) {
      const asked = shown?.origin === "request";
      shown = null;
      if (asked) tell?.();
    },
    /** Moving to another surface closes it; nobody is told. */
    close() { shown = null; },
  };
}

/** Both models together, from each view's own extent: what one shared view has to frame. */
export function commonBounds(bounds: readonly (ViewBounds | null)[]): ViewBounds | null {
  const present = bounds.filter((item): item is ViewBounds => item !== null);
  if (present.length === 0) return null;
  const axes = [0, 1, 2] as const;
  return {
    min: axes.map((axis) => Math.min(...present.map((item) => item.min[axis]))) as ViewBounds["min"],
    max: axes.map((axis) => Math.max(...present.map((item) => item.max[axis]))) as ViewBounds["max"],
  };
}

/**
 * The pane a shared view is framed in: the narrower one. Both panes stand at
 * the same camera, so what fits across the narrower frame fits the wider one.
 */
export function framingPane(sizes: Readonly<Record<ComparisonPane, readonly [number, number] | null>>): ComparisonPane {
  const aspect = (size: readonly [number, number] | null) => size && size[0] > 0 && size[1] > 0 ? size[0] / size[1] : Infinity;
  return aspect(sizes.after) < aspect(sizes.before) ? "after" : "before";
}

/** A camera to stand in the named panes. */
export interface CameraStep {
  readonly panes: readonly ComparisonPane[];
  readonly camera: CameraState;
}

/**
 * Linked cameras for two panes. Once both models are in, both panes stand in
 * one shared view. Only a person's own move is passed on - an applied camera
 * never reports back - and turning the link off keeps each pane where it is;
 * turning it on again brings the other pane to the one moved last, or both to
 * the shared view when nobody has moved since. Returns what to apply where, or null.
 */
export function createCameraLink() {
  let linked = true;
  let aligned = false;
  // The view to share: the one a person moved to last (pane), or the shared framing (null).
  let last: { pane: ComparisonPane | null; camera: CameraState } | null = null;
  const others = (pane: ComparisonPane | null): readonly ComparisonPane[] =>
    pane === null ? PANES : [pane === "before" ? "after" : "before"];
  return {
    get linked() { return linked; },
    /** Both panes hold their models: the view framing both becomes the one they share. */
    ready(shared: CameraState): CameraStep | null {
      aligned = true;
      last = { pane: null, camera: shared };
      return linked ? { panes: others(null), camera: shared } : null;
    },
    moved(pane: ComparisonPane, camera: CameraState): CameraStep | null {
      if (!aligned) return null;
      last = { pane, camera };
      return linked ? { panes: others(pane), camera } : null;
    },
    setLinked(on: boolean): CameraStep | null {
      const was = linked;
      linked = on;
      return on && !was && aligned && last ? { panes: others(last.pane), camera: last.camera } : null;
    },
  };
}
