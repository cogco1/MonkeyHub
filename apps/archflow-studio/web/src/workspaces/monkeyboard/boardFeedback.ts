import { studio } from "../../api/client";
import type { DocumentAnnotationRefDto, DocumentAnnotationsDto, DocumentGestureDto, DocumentVisualInputDto, ModelSourceDto } from "../../api/generated";
import { renderDocumentVisual } from "../monkeydiagram/documentVisualInput";
import { findSource, type PageSource } from "./boardScene";
import type { BoardFeedbackSelection } from "./boardFeedbackGeometry";

/** A user-triggered handoff to the existing Studio conversation, not a new job or store. */
export type BoardDesignRequest = {
  projectId: string;
  utterance: string;
  modelSource: ModelSourceDto;
  sourceStageRef: string | null;
  source: PageSource;
  documentAnnotations: DocumentAnnotationRefDto[];
  documentVisuals: DocumentVisualInputDto[];
};

export class BoardFeedbackError extends Error {
  constructor(readonly code: "SOURCE_CHANGED" | "MODEL_REQUIRED" | "MODEL_CHANGED" | "EMPTY_COMMENT", message: string) {
    super(message); this.name = "BoardFeedbackError";
  }
}

const sameModel = (left: ModelSourceDto | null | undefined, right: ModelSourceDto | null | undefined) =>
  left != null && right != null && left.runId === right.runId && left.stateDigest === right.stateDigest && left.assetSha256 === right.assetSha256;

/** Re-sending a board mark updates that mark; unrelated page ink stays in place. */
export function mergeBoardAnnotations(previous: readonly DocumentGestureDto[], added: readonly DocumentGestureDto[], groups: readonly string[]): DocumentGestureDto[] {
  const retained = previous.filter((mark) => !groups.some((group) => mark.id === group || mark.id.startsWith(`${group}:`)));
  return [...retained, ...added];
}

function checkPage(saved: DocumentAnnotationsDto, projectId: string, source: PageSource) {
  if (saved.projectId !== projectId || saved.runId !== source.runId || saved.assetSha256 !== source.assetSha256 ||
      saved.pageIndex !== source.pageIndex || (saved.drawingRevisionRef ?? null) !== source.revisionRef) {
    throw new BoardFeedbackError("SOURCE_CHANGED", "The saved page no longer matches the selected drawing.");
  }
}

function samePageGeometry(left: { width: number; height: number; rotation: number }, right: { width: number; height: number; rotation: number }) {
  return left.width === right.width && left.height === right.height && left.rotation === right.rotation;
}

export async function prepareBoardDesignRequest(selection: BoardFeedbackSelection, projectId: string, comment: string): Promise<BoardDesignRequest> {
  const utterance = comment.trim();
  if (!utterance) throw new BoardFeedbackError("EMPTY_COMMENT", "Write the change you want to discuss.");
  const { source } = selection;
  const list = await studio.documents();
  const document = findSource(list.documents, source);
  if (list.projectId !== projectId || !document || document.projectId !== projectId) {
    throw new BoardFeedbackError("SOURCE_CHANGED", "The selected drawing is no longer available in this project.");
  }
  if (!document.modelSource) throw new BoardFeedbackError("MODEL_REQUIRED", "Associate this drawing with its model in MonkeyDiagram before submitting a design change.");
  if (!sameModel(document.modelSource, selection.document.modelSource) ||
      (document.modelSourceBindingRef ?? null) !== (selection.document.modelSourceBindingRef ?? null) ||
      (document.sourceStageRef ?? null) !== (selection.document.sourceStageRef ?? null)) {
    throw new BoardFeedbackError("MODEL_CHANGED", "The drawing's linked model changed while you were writing. Select the drawing again.");
  }
  const page = document.pages.find((value) => value.pageIndex === source.pageIndex)!;
  if (!samePageGeometry(page, selection.page)) {
    throw new BoardFeedbackError("SOURCE_CHANGED", "The selected page dimensions changed. Select the drawing again.");
  }

  const referenceRows = (selection.references ?? []).map((reference) => {
    const currentDocument = findSource(list.documents, reference.source);
    const currentPage = currentDocument?.pages.find((value) => value.pageIndex === reference.source.pageIndex);
    if (!currentDocument || currentDocument.projectId !== projectId || !currentPage
      || !samePageGeometry(currentPage, reference.page)) {
      throw new BoardFeedbackError("SOURCE_CHANGED", "A selected concept reference changed or is no longer available. Select the reference again.");
    }
    return { selection: reference, document: currentDocument, page: currentPage };
  });

  const modelSource = { ...document.modelSource };
  const sourceStageRef = document.sourceStageRef ?? null;
  const projection = await studio.state(modelSource.runId, sourceStageRef);
  if (projection.projectId !== projectId || projection.referenceRun.runId !== modelSource.runId ||
      projection.stateDigest !== modelSource.stateDigest || (sourceStageRef !== null && projection.sourceStageRef !== sourceStageRef)) {
    throw new BoardFeedbackError("MODEL_CHANGED", "The drawing's exact model cannot currently be restored for editing.");
  }
  const [current, file, referenceAssets] = await Promise.all([
    studio.documentAnnotations(source.runId, source.assetSha256, source.pageIndex, null, source.revisionRef),
    studio.documentFile(source.runId, source.assetSha256, document.fileName, source.revisionRef),
    Promise.all(referenceRows.map(async (reference) => {
      const [annotations, referenceFile] = await Promise.all([
        studio.documentAnnotations(reference.selection.source.runId, reference.selection.source.assetSha256,
          reference.selection.source.pageIndex, null, reference.selection.source.revisionRef),
        studio.documentFile(reference.selection.source.runId, reference.selection.source.assetSha256,
          reference.document.fileName, reference.selection.source.revisionRef),
      ]);
      checkPage(annotations, projectId, reference.selection.source);
      const original = referenceFile.type === reference.document.mimeType ? referenceFile
        : new File([referenceFile], referenceFile.name, { type: reference.document.mimeType });
      const visual = await renderDocumentVisual(original, reference.page, annotations.annotations);
      if (annotations.annotations.length > 0 && !visual.annotatedPngBase64) {
        throw new BoardFeedbackError("SOURCE_CHANGED", "A selected concept reference could not reproduce its saved annotation overlay.");
      }
      const input: DocumentVisualInputDto = {
        role: "reference",
        runId: reference.selection.source.runId,
        assetSha256: reference.selection.source.assetSha256,
        pageIndex: reference.selection.source.pageIndex,
        drawingRevisionRef: reference.selection.source.revisionRef,
        revisionSha256: annotations.revisionSha256,
        pagePngBase64: visual.pagePngBase64,
        annotatedPngBase64: annotations.annotations.length > 0 ? visual.annotatedPngBase64 : null,
        referenceNote: reference.selection.referenceNote,
      };
      return input;
    })),
  ]);
  checkPage(current, projectId, source);
  const annotations = mergeBoardAnnotations(current.annotations, selection.annotations, selection.annotationGroups);
  // Render before writing: unreadable originals do not change the page's saved draft.
  const original = file.type === document.mimeType ? file : new File([file], file.name, { type: document.mimeType });
  const visual = await renderDocumentVisual(original, page, annotations);
  const saved = await studio.saveDocumentAnnotations({
    projectId, runId: source.runId, assetSha256: source.assetSha256, pageIndex: source.pageIndex,
    drawingRevisionRef: source.revisionRef, baseRevisionSha256: current.revisionSha256,
    annotations, comment: current.comment,
  });
  checkPage(saved, projectId, source);
  if (!saved.revisionSha256) throw new BoardFeedbackError("SOURCE_CHANGED", "The saved page did not return an annotation revision.");
  const reference: DocumentAnnotationRefDto = {
    runId: source.runId, assetSha256: source.assetSha256, pageIndex: source.pageIndex,
    drawingRevisionRef: source.revisionRef, revisionSha256: saved.revisionSha256,
  };
  return { projectId, utterance, modelSource, sourceStageRef, source: { ...source },
    documentAnnotations: [reference], documentVisuals: [
      { ...reference, role: "edit", pagePngBase64: visual.pagePngBase64, annotatedPngBase64: visual.annotatedPngBase64 },
      ...referenceAssets,
    ],
  };
}
