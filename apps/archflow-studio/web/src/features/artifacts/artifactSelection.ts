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
 * The rows of a run the viewer may be handed: servable, and in the format it
 * parses. A picture of a run is its previews, never a STEP the parser would
 * refuse, and never the same seat twice.
 */
export function viewableArtifacts(
  rows: readonly ProjectArtifactDto[],
): ServableArtifact[] {
  return rows.filter((row): row is ServableArtifact => isServable(row) && isViewable(row));
}

/**
 * The i18n key that says what a file is: the exact STEP delivery, a mesh
 * preview of it, or a Rhino export. Read off the receipt's own two words.
 */
export function artifactKindKey(
  artifact: ProjectArtifactDto,
): "artifact.kind.exactStep" | "artifact.kind.previewMesh" | "artifact.kind.exact3dm" | "artifact.kind.composed3dm" {
  if (artifact.representation === "composed") return "artifact.kind.composed3dm";
  if (artifact.representation === "preview") return "artifact.kind.previewMesh";
  if (artifact.format === "step") return "artifact.kind.exactStep";
  return "artifact.kind.exact3dm";
}
