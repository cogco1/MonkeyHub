/**
 * Which listed artifact goes where: the viewer, or a save link.
 *
 * One in-process export receipt lists two files — the exact STEP delivery
 * and a mesh `.3dm` preview tessellated from the same model — and a Rhino
 * receipt lists one `.3dm`. The viewer parses `.3dm` and nothing else, so the
 * two words the server puts on every row (`format`, `representation`) decide
 * here, once, what is shown and how it is called. Nothing is inferred from a
 * file name, and a preview is never called the exact model.
 *
 * Pure functions with no imports beyond the wire type, so the node test runner
 * can exercise them without a bundler.
 */

import type { ProjectArtifactDto } from "../../api/generated";

/** An artifact whose bytes can be asked for: the listing said it is there and named its digest. */
export type ServableArtifact = ProjectArtifactDto & { sha256: string };

/**
 * Whether the viewer can open this file. A STEP is for saving and opening in
 * CAD; handing it to the 3dm parser would only fail.
 */
export function isViewable(artifact: ProjectArtifactDto): boolean {
  return artifact.format === "3dm";
}

/** Whether the listing can serve this file's bytes at all. */
export function isServable(artifact: ProjectArtifactDto): artifact is ServableArtifact {
  return artifact.available && artifact.sha256 !== null;
}

/**
 * Whether this row is an editable work model: the same delivery as an exact
 * STEP already listed, imported into Rhino so a person can keep working on it.
 * The server says so by naming the STEP it came from.
 */
export function isWorkModel(artifact: ProjectArtifactDto): boolean {
  return typeof artifact.sourceStepSha256 === "string" && artifact.sourceStepSha256.length > 0;
}

/**
 * The rows of a run the viewer may be handed: servable, and in the format it
 * parses. A picture of a run is its previews, never a STEP the parser would
 * refuse, and never the same seat twice.
 *
 * A work model is left out on purpose. It is the same geometry as the delivery
 * whose preview is already on screen - the copy made to open in Rhino - so
 * putting it in front of the viewer would load one model twice.
 */
export function viewableArtifacts(
  rows: readonly ProjectArtifactDto[],
): ServableArtifact[] {
  return rows.filter(
    (row): row is ServableArtifact => isServable(row) && isViewable(row) && !isWorkModel(row),
  );
}

/** What the viewer is showing right now, as the rows it was handed. */
export interface ViewedModel {
  readonly runId: string | null;
  readonly shas: readonly string[];
}

/**
 * Why an editable copy cannot be offered for what is on screen.
 *
 * `nothing-loaded` - no model is being viewed. `not-an-export` - what is shown
 * is a registered or composed model, which has no exact STEP of its own.
 * `several-seats` - the picture is more than one seat's export, and there is
 * no single delivery to make editable; the architect opens the one they want.
 */
export type WorkModelSourceRefusal = "nothing-loaded" | "not-an-export" | "several-seats";

/**
 * The exact STEP of the model on screen, or why there is not exactly one.
 *
 * An in-process receipt certifies two files - the exact STEP and the mesh
 * preview tessellated from it - so the STEP of what is being viewed is the
 * sibling of the loaded preview under the same `receiptRef`. Nothing is picked
 * by run, by recency or by position: a viewer showing several seats, or a
 * model registered from outside, has no single exact delivery and says so.
 */
export function workModelSourceOf(
  rows: readonly ProjectArtifactDto[],
  viewed: ViewedModel,
): { source: ServableArtifact } | { refusal: WorkModelSourceRefusal } {
  if (viewed.runId === null || viewed.shas.length === 0) return { refusal: "nothing-loaded" };
  // More than one model is on screen, whatever the listing currently holds:
  // a listing that has caught up with only one of them would otherwise let a
  // multi-seat picture look like a single delivery.
  if (new Set(viewed.shas).size > 1) return { refusal: "several-seats" };
  const shown = rows.filter(
    (row) => row.runId === viewed.runId && row.sha256 !== null && viewed.shas.includes(row.sha256),
  );
  if (shown.length === 0) return { refusal: "not-an-export" };
  if (shown.length > 1) return { refusal: "several-seats" };
  const [loaded] = shown;
  const steps = rows.filter(
    (row): row is ServableArtifact =>
      row.runId === loaded.runId &&
      row.receiptRef === loaded.receiptRef &&
      row.format === "step" &&
      row.representation === "exact" &&
      isServable(row),
  );
  if (steps.length !== 1) return { refusal: "not-an-export" };
  return { source: steps[0] };
}

/**
 * The editable copy already made from that exact STEP, if the listing has one.
 *
 * Matched on what the receipt says, never on a file name: the same source
 * digest, and the same run, stage, program and state the source itself was
 * exported against. A row that failed, that is not the editable 3dm, or that
 * belongs to another stage or program is a different delivery - showing it as
 * this model's copy would hand a person the wrong file to open.
 */
export function workModelOf(
  rows: readonly ProjectArtifactDto[],
  source: ProjectArtifactDto,
): ServableArtifact | null {
  return (
    rows.find(
      (row): row is ServableArtifact =>
        row.sourceStepSha256 === source.sha256 &&
        row.status === "succeeded" &&
        row.format === "3dm" &&
        row.representation === "exact" &&
        isServable(row) &&
        row.runId === source.runId &&
        row.stageId === source.stageId &&
        row.programRef === source.programRef &&
        row.programDigest === source.programDigest &&
        row.designStateDigest === source.designStateDigest,
    ) ?? null
  );
}

/**
 * The i18n key that says what a file is: the exact STEP delivery, a mesh
 * preview of it, or a Rhino export. Read off the receipt's own two words.
 */
export function artifactKindKey(
  artifact: ProjectArtifactDto,
): "artifact.kind.exactStep" | "artifact.kind.previewMesh" | "artifact.kind.exact3dm" | "artifact.kind.composed3dm" | "artifact.kind.workModel" {
  if (artifact.representation === "composed") return "artifact.kind.composed3dm";
  if (artifact.representation === "preview") return "artifact.kind.previewMesh";
  if (artifact.format === "step") return "artifact.kind.exactStep";
  if (isWorkModel(artifact)) return "artifact.kind.workModel";
  return "artifact.kind.exact3dm";
}
