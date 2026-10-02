/**
 * Project-bound wrappers around the generated Project Runtime SDK.
 * Request and response shapes remain generated; every call uses the connection
 * supplied to this factory and every non-2xx throws the server's StudioApiError.
 */

import type { ServerConnection } from "./connection";
import { renderCapabilitiesApiRenderCapabilitiesGet, listRenderJobsApiRenderJobsGet, createRenderJobApiRenderJobsPost, retainRenderViewApiRenderViewsPost } from "./generated";
import { requestProjectionApiProjectionsGet, readProjectionBlobApiProjectionsBlobsSha256Get, readPageProjectionApiProjectionsPagesGet } from "./generated";
import type { ProjectionStatusDto } from "./generated";
import type { RenderCapabilitiesDto, RenderJobListDto, RenderJobDto, RenderRequestDto, RenderViewSourceRequestDto } from "./generated";
import type { ElevationEditRequestDto } from "./generated";
import type { SaveStudyRequestDto, StudyViewDto, ProposeStudyRequestDto } from "./generated";
import type { PlanRequestDto, PlanStatusRequestDto, PlanStatusDto, PlanDimensionProposalRequestDto, PlanDimensionChoicesDto, PlanVectorDto } from "./generated";
import { createPlanApiDrawingsPlansPost, readPlanStatusApiDrawingsPlansStatusPost, readPlanVectorApiDrawingsPlansVectorGet,
  readPlanDimensionChoicesApiDrawingsPlansDimensionsGet, createPlanDimensionProposalApiDrawingsPlansDimensionProposalPost } from "./generated";
import { createSectionPerspectiveApiDrawingsSectionPerspectivesPost } from "./generated";
import type { SectionPerspectiveRequestDto } from "./generated";
import { createDecisionApiDecisionsPost, readDecisionsApiDecisionsGet, readDrawingCorrectionsApiDrawingsCorrectionsGet,
  reviseDecisionApiDecisionsDecisionIdRevisionsPost } from "./generated";
import type { DecisionDto, DecisionListDto, DecisionRequestDto, DecisionRevisionRequestDto, DrawingCorrectionsDto } from "./generated";
import { exportDrawingRecipeApiDecisionsDecisionIdRecipeExportGet, inspectDrawingRecipeApiDrawingRecipesInspectPost,
  importDrawingRecipeApiDrawingRecipesImportPost } from "./generated";
import type { RecipeExportFileDto, RecipeInspectDto, RecipeInspectRequestDto, RecipeImportRequestDto } from "./generated";
import type { ReviewJudgementDto, ReviewJudgementRequestDto } from "./generated";
import { retainStudyApiStudiesPost, discoverStudiesApiStudiesGet,
  proposeStudyApiStudiesProposePost } from "./generated";
import {
  BLOCKED_NEEDS_HUMAN,
  MISSING_EDITABLE_CONTROL,
  STALE_CLARIFICATION,
  UNSUPPORTED_REQUEST,
  call,
  NETWORK_ERROR,
  StudioApiError,
  TRANSPORT_ERROR,
  asStudioApiError,
  type FieldsResult,
} from "./error";
import {
  readCurrentWorkingDraftApiWorkingDraftGet,
  readWorkingRevisionApiWorkingDraftRevisionGet,
  readWorkingSourceApiWorkingSourceGet,
  readRepresentationStatusApiRepresentationStatusGet,
  readWorktreesApiWorktreesGet,
  readProjectTrashApiTrashGet,
  restoreFromTrashApiTrashRestorePost,
  selectCurrentWorkingDraftApiWorkingDraftPut,
  saveCurrentWorkingDraftApiWorkingDraftSavePost,
  retainLocalWorkingDraftApiWorkingDraftLocalPut,
  readBoardApiBoardGet,
  updateBoardApiBoardPut,
  readCommittedDesignHistoryApiDesignHistoryGet,
  reviewCandidateOrStageApiCandidateReviewsPost,
  createElevationApiDrawingsElevationsPost,
  createElevationProposalApiProposalsElevationPost,
  readDrawingStylesApiDrawingsStylesGet,
  createSheetApiDrawingsSheetsPost,
  combineCandidatesApiCandidatesCombinePost,
  initializeCommittedDesignApiDesignStagesInitializePost,
  acceptCommittedDesignApiCandidatesCandidateIdAcceptPost,
  forkCommittedDesignApiDesignBranchesPost,
  associateDocumentModelSourceApiDocumentsAssetSha256ModelSourcePost,
  chooseWorkingCopyOptionApiWorkingCopiesGroupIdSelectionPut,
  compileIntentApiIntentsPost,
  createDocumentApiDocumentsPost,
  createModelAssetApiModelAssetsPost,
  createDocumentWorkCopyApiDocumentsAssetSha256WorkCopyPost,
  createProposalApiProposalsPost,
  createTracingPaperReviewApiTracingPaperReviewsPost,
  createViewportCaptureApiCapturesPost,
  readRetainedModelPreviewApiModelAssetsAssetSha256PreviewGet,
  exportBoardApiBoardExportPost,
  readArtifactBytesApiArtifactsSha256BytesGet,
  recordModelLoadApiEventsModelLoadPost,
  recordClientTimingApiEventsTimingPost,
  exportArtifactWorkModelApiArtifactsSha256RhinoExportPost,
  readArtifactsApiArtifactsGet,
  readCandidateApiCandidatesCandidateIdGet,
  readDocumentBytesApiDocumentsAssetSha256BytesGet,
  readDocumentPageAnnotationsApiDocumentAnnotationsGet,
  readDocumentsApiDocumentsGet,
  readJobApiJobsJobIdGet,
  readRuntimeApiRuntimeGet,
  readSavedModelAnnotationsApiModelAnnotationsGet,
  readWorkingCopiesApiWorkingCopiesGet,
  readProjectApiProjectGet,
  readProjectsApiProjectsGet,
  createDeleteProposalApiProposalsDeletePost,
  createSketchProposalApiProposalsSketchPost,
  prepareModelingApiProjectModelingPost,
  createTransformProposalApiProposalsTransformPost,
  createPushPullProposalApiProposalsPushPullPost,
  createParameterLocksProposalApiProposalsParameterLocksPost,
  readProposalApiProposalsProposalIdGet,
  readClosureApiStateClosurePost,
  readFrameApiStateFrameGet,
  readStateApiStateGet,
  readSubmittedDocumentCommentsApiDocumentCommentsGet,
  readVolumesApiStateVolumesGet,
  readValidationApiCandidatesCandidateIdValidationGet,
  compareCandidateApiCandidatesCandidateIdCompareGet,
  resolveApiPickResolvePost,
  startCandidateApiProposalsProposalIdCandidatePost,
  writeDocumentPageAnnotationsApiDocumentAnnotationsPut,
  writeSavedModelAnnotationsApiModelAnnotationsPut,
} from "./generated";
import type {
  WorkingDraftDto, WorkingRevisionDto, WorkingSourceDto, WorktreeGraphDto, WorkingDraftSelectionDto, WorkingDraftSaveDto, LocalDraftRequestDto,
  ProjectTrashDto, TrashRestoreDto, TrashRestoreRequestDto,
  RepresentationStatusDto,
  BoardDto, BoardExportRequestDto, BoardRequestDto,
  DesignHistoryDto, DesignStageDto, DesignBranchDto,
  ElevationRequestDto, CombineCandidatesRequestDto,
  DrawingStylesDto, SheetRequestDto,
  InitializeDesignStageRequestDto, AcceptDesignCandidateRequestDto, ForkDesignBranchRequestDto,
  ArtifactListDto,
  CandidateAcceptedDto,
  CandidateDto,
  ClosureDto,
  ClosureRequestDto,
  CompareDto,
  DocumentAnnotationsDto,
  DocumentAnnotationsRequestDto,
  DocumentCommentsDto,
  DocumentWorkCopyDto,
  FrameDto,
  IntentDto,
  IntentRequestDto,
  JobDto,
  RuntimeDto,
  ModelAnnotationsDto,
  ModelAnnotationsRequestDto,
  ModelSourceDto,
  ModelLoadTimingDto,
  ClientTimingDto,
  DeleteElementRequestDto,
  MonitorWriteDto,
  PickRequestDto,
  PickResolutionDto,
  ProjectBindingDto,
  ProjectArtifactDto,
  ProjectListDto,
  ProposalDto,
  ModelingInitializeDto,
  SketchBatchRequestDto,
  DocumentTracingRequestDto,
  SketchPrismRequestDto,
  TransformElementRequestDto,
  PushPullRequestDto,
  ProposalRequestDto,
  ParameterLocksRequestDto,
  SourceDocumentDto,
  SourceDocumentListDto,
  SourceDocumentRequestDto,
  TracingPaperReviewRequestDto,
  StateProjectionDto,
  ValidationDto,
  ViewportCaptureDto,
  VolumesDto,
  WorkingCopyDto,
  WorkingCopyListDto,
  WorkingCopySelectionRequestDto,
} from "./generated";

/** A trace belongs to this call, never to shared SDK configuration. */
export interface OperationTrace {
  readonly operationId: string;
  readonly parentEventId?: string;
}
function traceHeaders(trace?: OperationTrace) {
  return trace ? { "X-Monkey-Operation": trace.operationId,
    ...(trace.parentEventId ? { "X-Monkey-Parent": trace.parentEventId } : {}) } : undefined;
}

// The error type and its codes are defined in `error.ts` so that the
// connection can refuse a server in the same shape a call refuses an answer.
// They are re-exported here because this module is what the app imports.
export {
  BLOCKED_NEEDS_HUMAN,
  MISSING_EDITABLE_CONTROL,
  NETWORK_ERROR,
  STALE_CLARIFICATION,
  StudioApiError,
  TRANSPORT_ERROR,
  UNSUPPORTED_REQUEST,
  asStudioApiError,
};

function base64Of(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 32_768;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return window.btoa(binary);
}

/**
 * The polled views the runtime answers conditionally (#363): design history,
 * worktrees, artifacts, documents, working source, board, render jobs and the
 * project trash. The
 * last 200 of each, by its path and query, is kept with its ETag for the
 * KEPT_VIEWS views read most recently; a refresh sends `If-None-Match`, and a
 * 304 answers with the very object kept, so a caller can tell by identity that
 * nothing changed. One request per view is in flight at a time, and no caller
 * is answered by a request sent before it asked: those who ask while one is on
 * the way share the one conditional read sent once it has answered (usually a
 * 304), whatever changed meanwhile and whoever changed it. A caller's abort
 * only lets go of its own wait.
 */
type ConditionalRead = <T>(what: string, query: Record<string, unknown>,
  send: (headers: Record<string, string> | undefined) => Promise<FieldsResult<T>>, signal?: AbortSignal) => Promise<T>;
const KEPT_VIEWS = 64;
const reads = new WeakMap<ServerConnection, ConditionalRead>();
function conditional(connection: ServerConnection): ConditionalRead {
  const known = reads.get(connection);
  if (known) return known;
  const kept = new Map<string, { tag: string; data: unknown }>();
  const lanes = new Map<string, { sending: Promise<unknown>; queued: Promise<unknown> | null }>();
  const keep = (key: string, value: { tag: string; data: unknown }) => {
    kept.delete(key); kept.set(key, value);
    for (const oldest of kept.keys()) { if (kept.size <= KEPT_VIEWS) break; kept.delete(oldest); }
  };
  const read: ConditionalRead = <T,>(what: string, query: Record<string, unknown>,
    send: (headers: Record<string, string> | undefined) => Promise<FieldsResult<T>>, signal?: AbortSignal): Promise<T> => {
    const key = `${what}?${new URLSearchParams(Object.entries(query).filter(([, value]) => value != null)
      .map(([name, value]) => [name, String(value)])).toString()}`;
    const ask = () => {
      const last = kept.get(key);
      let tag: string | null = null;
      return call(what, send(last ? { "If-None-Match": last.tag } : undefined).then((result) => {
        if (last && result.response?.status === 304) return { data: last.data as T, response: result.response };
        if (result.error === undefined) tag = result.response?.headers.get("ETag") ?? null;
        return result;
      })).then((data) => {
        if (tag) keep(key, { tag, data });
        else if (data !== last?.data) kept.delete(key);
        return data;
      });
    };
    const start = (): Promise<T> => {
      const answer = ask();
      const lane = { sending: answer as Promise<unknown>, queued: null as Promise<unknown> | null };
      lanes.set(key, lane);
      const settle = () => { if (lanes.get(key) === lane && lane.queued === null) lanes.delete(key); };
      answer.then(settle, settle);
      return answer;
    };
    const lane = lanes.get(key);
    const shared = (lane ? lane.queued ??= lane.sending.then(start, start) : start()) as Promise<T>;
    if (!signal) return shared;
    return new Promise<T>((resolve, reject) => {
      const abort = () => reject(new StudioApiError({ status: 0, code: NETWORK_ERROR, detail: `${what} never reached the API: the read was aborted.` }));
      if (signal.aborted) { abort(); return; }
      signal.addEventListener("abort", abort, { once: true });
      shared.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
    });
  };
  reads.set(connection, read);
  return read;
}

/** Model bytes by digest, for the whole app: the same sha256 is the same bytes, served immutable. */
const MODEL_BYTES_LIMIT = 256 * 1024 * 1024;
const modelBytes = new Map<string, Blob>();
function keepModelBytes(sha256: string, blob: Blob) {
  modelBytes.delete(sha256);
  modelBytes.set(sha256, blob);
  let size = 0;
  for (const kept of modelBytes.values()) size += kept.size;
  // The least recently opened go first; the model just opened always stays.
  for (const [digest, kept] of modelBytes) {
    if (size <= MODEL_BYTES_LIMIT || digest === sha256) break;
    modelBytes.delete(digest); size -= kept.size;
  }
}
/** Downloads on the way, by digest: two openings of the same model share one. The first opening's trace names it. */
type ModelDownload = { blob: Promise<Blob>; waiting: number; abort: AbortController };
const modelDownloads = new Map<string, ModelDownload>();
function keptModelBytes(sha256: string): Blob | null {
  const blob = modelBytes.get(sha256);
  if (!blob) return null;
  modelBytes.delete(sha256); modelBytes.set(sha256, blob);
  return blob;
}

// Workspace startup reads `/api/project` and `/api/state`; runtime liveness
// belongs to the Hub that started it.
export const createStudioClient = (connection: ServerConnection) => ({
  workingDraft(): Promise<WorkingDraftDto> {
    return call("GET /api/working-draft", readCurrentWorkingDraftApiWorkingDraftGet({ client: connection.client }));
  },
  /** Only the working position's revision: poll this to notice that the head may have moved. */
  workingRevision(signal?: AbortSignal): Promise<WorkingRevisionDto> {
    return call("GET /api/working-draft/revision", readWorkingRevisionApiWorkingDraftRevisionGet({ client: connection.client, signal }));
  },
  /** The current working source a workspace follows; the server resolves it from retained facts. */
  workingSource(workspace: WorkingSourceDto["workspace"] = "modeling", signal?: AbortSignal): Promise<WorkingSourceDto> {
    return conditional(connection)("GET /api/working-source", { workspace }, (headers) =>
      readWorkingSourceApiWorkingSourceGet({ client: connection.client, query: { workspace }, headers }), signal);
  },
  /**
   * Whether one exact registered page still shows what its inputs say now, against the Working Head:
   * the Runtime's one representation status (#223), derived on every read and never stored.
   */
  representationStatus(page: { runId: string; assetSha256: string; revisionRef?: string | null; pageIndex: number },
    signal?: AbortSignal): Promise<RepresentationStatusDto> {
    const { runId, assetSha256, revisionRef, pageIndex } = page;
    return call("GET /api/representation-status", readRepresentationStatusApiRepresentationStatusGet({ client: connection.client,
      query: { runId, assetSha256, pageIndex, ...(revisionRef ? { revisionRef } : {}) }, signal }));
  },
  /** Read-only: the Working Head, running work, other lines and whether they reconcile. */
  worktrees(signal?: AbortSignal): Promise<WorktreeGraphDto> {
    return conditional(connection)("GET /api/worktrees", {}, (headers) => readWorktreesApiWorktreesGet({ client: connection.client, headers }), signal);
  },
  /** The project trash (#575): superseded drafts and failed attempts moved out whole, restorable until purged. */
  trash(signal?: AbortSignal): Promise<ProjectTrashDto> {
    return conditional(connection)("GET /api/trash", {}, (headers) => readProjectTrashApiTrashGet({ client: connection.client, headers }), signal);
  },
  /** Brings one trashed run back as it left, with any trashed run it was made from; it is never cleaned again. */
  restoreTrashed(body: TrashRestoreRequestDto): Promise<TrashRestoreDto> {
    return call("POST /api/trash/restore", restoreFromTrashApiTrashRestorePost({ client: connection.client, body }));
  },
  selectWorkingDraft(body: WorkingDraftSelectionDto): Promise<WorkingDraftDto> {
    return call("PUT /api/working-draft", selectCurrentWorkingDraftApiWorkingDraftPut({ client: connection.client, body }));
  },
  /**
   * The person's own 保存 in the history panel: it says the person saved the name (`savedBy: "person"`).
   * Only a name a person saved keeps a superseded draft out of the project trash (#575); agents and other
   * callers save names without it.
   */
  savePersonsVersion(body: Omit<WorkingDraftSaveDto, "savedBy">): Promise<WorkingDraftDto> {
    return call("POST /api/working-draft/save", saveCurrentWorkingDraftApiWorkingDraftSavePost({
      client: connection.client, body: { ...body, savedBy: "person" } }));
  },
  retainLocalDraft(body: LocalDraftRequestDto): Promise<WorkingDraftDto> {
    return call("PUT /api/working-draft/local", retainLocalWorkingDraftApiWorkingDraftLocalPut({ client: connection.client, body }));
  },
  studies(): Promise<StudyViewDto[]> {
    return call("GET /api/studies", discoverStudiesApiStudiesGet({ client: connection.client }));
  },
  saveStudy(body: SaveStudyRequestDto): Promise<StudyViewDto> {
    return call("POST /api/studies", retainStudyApiStudiesPost({ client: connection.client, body }));
  },
  proposeStudy(body: ProposeStudyRequestDto): Promise<StudyViewDto> {
    return call("POST /api/studies/propose", proposeStudyApiStudiesProposePost({ client: connection.client, body }));
  },
  board(): Promise<BoardDto> {
    return conditional(connection)("GET /api/board", {}, (headers) => readBoardApiBoardGet({ client: connection.client, headers }));
  },
  saveBoard(body: BoardRequestDto): Promise<BoardDto> {
    return call("PUT /api/board", updateBoardApiBoardPut({ client: connection.client, body }));
  },

  exportBoard(body: BoardExportRequestDto): Promise<Blob> {
    return call("POST /api/board/export", exportBoardApiBoardExportPost({ client: connection.client, body, parseAs: "blob" }) as Promise<FieldsResult<Blob>>);
  },
  elevation(body: ElevationRequestDto, trace?: OperationTrace): Promise<SourceDocumentDto> {
    return call("POST /api/drawings/elevations", createElevationApiDrawingsElevationsPost({ client: connection.client, body, headers: traceHeaders(trace) }));
  },
  drawingStyles(): Promise<DrawingStylesDto> {
    return call("GET /api/drawings/styles", readDrawingStylesApiDrawingsStylesGet({ client: connection.client }));
  },
  drawingSheet(body: SheetRequestDto): Promise<SourceDocumentDto> {
    return call("POST /api/drawings/sheets", createSheetApiDrawingsSheetsPost({ client: connection.client, body }));
  },
  drawingPlan(body: PlanRequestDto): Promise<SourceDocumentDto> {
    return call("POST /api/drawings/plans", createPlanApiDrawingsPlansPost({ client: connection.client, body }));
  },
  drawingSectionPerspective(body: SectionPerspectiveRequestDto): Promise<SourceDocumentDto> {
    return call("POST /api/drawings/section-perspectives", createSectionPerspectiveApiDrawingsSectionPerspectivesPost({ client: connection.client, body }));
  },
  drawingPlanVector(source: { runId: string; assetSha256: string; revisionRef: string }): Promise<PlanVectorDto> {
    return call("GET /api/drawings/plans/vector", readPlanVectorApiDrawingsPlansVectorGet({ client: connection.client, query: source }));
  },
  drawingPlanStatus(body: PlanStatusRequestDto): Promise<PlanStatusDto> {
    return call("POST /api/drawings/plans/status", readPlanStatusApiDrawingsPlansStatusPost({ client: connection.client, body }));
  },
  drawingPlanDimensions(target: { modelSource: ModelSourceDto; stageRef: string | null } | { sourceAsset: { runId: string; assetSha256: string } }): Promise<PlanDimensionChoicesDto> {
    return call("GET /api/drawings/plans/dimensions", readPlanDimensionChoicesApiDrawingsPlansDimensionsGet({ client: connection.client, query: {
      ...("modelSource" in target
        ? { sourceRunId: target.modelSource.runId, stateDigest: target.modelSource.stateDigest,
          assetSha256: target.modelSource.assetSha256, sourceStageRef: target.stageRef }
        : { sourceAssetRunId: target.sourceAsset.runId, sourceAssetSha256: target.sourceAsset.assetSha256 }),
    } }));
  },
  drawingDimensionProposal(body: PlanDimensionProposalRequestDto): Promise<ProposalDto> {
    return call("POST /api/drawings/plans/dimension-proposal", createPlanDimensionProposalApiDrawingsPlansDimensionProposalPost({ client: connection.client, body }));
  },
  /** Read only: one cut plan's revisions beside the ones they continued, and the project's repeated recipe corrections. */
  drawingCorrections(projectId: string, drawingId?: string | null): Promise<DrawingCorrectionsDto> {
    return call("GET /api/drawings/corrections", readDrawingCorrectionsApiDrawingsCorrectionsGet({ client: connection.client, query: { projectId, drawingId } }));
  },
  /** Every decision the project retains, at its current revision. */
  decisions(): Promise<DecisionListDto> {
    return call("GET /api/decisions", readDecisionsApiDecisionsGet({ client: connection.client }));
  },
  exportDrawingRecipe(decisionId: string, expectedRevisionRef: string): Promise<RecipeExportFileDto> {
    return call("GET /api/decisions/{decision_id}/recipe-export", exportDrawingRecipeApiDecisionsDecisionIdRecipeExportGet({
      client: connection.client, path: { decision_id: decisionId }, query: { expectedRevisionRef } }));
  },
  inspectDrawingRecipe(body: RecipeInspectRequestDto): Promise<RecipeInspectDto> {
    return call("POST /api/drawing-recipes/inspect", inspectDrawingRecipeApiDrawingRecipesInspectPost({ client: connection.client, body }));
  },
  importDrawingRecipe(body: RecipeImportRequestDto): Promise<DecisionDto> {
    return call("POST /api/drawing-recipes/import", importDrawingRecipeApiDrawingRecipesImportPost({ client: connection.client, body }));
  },
  /** Retain one decision, checked against the exact source it names. */
  saveDecision(body: DecisionRequestDto): Promise<DecisionDto> {
    return call("POST /api/decisions", createDecisionApiDecisionsPost({ client: connection.client, body }));
  },
  /** Revoke or supersede one decision against the revision the caller read. */
  reviseDecision(decisionId: string, body: DecisionRevisionRequestDto): Promise<DecisionDto> {
    return call(`POST /api/decisions/${decisionId}/revisions`, reviseDecisionApiDecisionsDecisionIdRevisionsPost({ client: connection.client,
      path: { decision_id: decisionId }, body }));
  },
  combineCandidates(body: CombineCandidatesRequestDto): Promise<CandidateAcceptedDto> {
    return call("POST /api/candidates/combine", combineCandidatesApiCandidatesCombinePost({ client: connection.client, body }));
  },
  designHistory(branchId = "main", signal?: AbortSignal): Promise<DesignHistoryDto> {
    return conditional(connection)("GET /api/design-history", { branchId }, (headers) =>
      readCommittedDesignHistoryApiDesignHistoryGet({ client: connection.client, query: { branchId }, headers }), signal);
  },
  reviewCandidate(body: ReviewJudgementRequestDto): Promise<ReviewJudgementDto> {
    return call("POST /api/candidate-reviews", reviewCandidateOrStageApiCandidateReviewsPost({ client: connection.client, body }));
  },
  initializeStage(body: InitializeDesignStageRequestDto): Promise<DesignStageDto> {
    return call("POST /api/design-stages/initialize", initializeCommittedDesignApiDesignStagesInitializePost({ client: connection.client, body }));
  },
  acceptCandidate(candidateId: string, body: AcceptDesignCandidateRequestDto, trace?: OperationTrace): Promise<DesignStageDto> {
    return call("POST /api/candidates/accept", acceptCommittedDesignApiCandidatesCandidateIdAcceptPost({ client: connection.client, path: { candidate_id: candidateId }, body, headers: traceHeaders(trace) }));
  },
  forkBranch(body: ForkDesignBranchRequestDto): Promise<DesignBranchDto> {
    return call("POST /api/design-branches", forkCommittedDesignApiDesignBranchesPost({ client: connection.client, body }));
  },

  workingCopies(): Promise<WorkingCopyListDto> {
    return call("GET /api/working-copies", readWorkingCopiesApiWorkingCopiesGet({ client: connection.client }));
  },

  selectWorkingCopy(groupId: string, body: WorkingCopySelectionRequestDto): Promise<WorkingCopyDto> {
    return call(`PUT /api/working-copies/${groupId}/selection`, chooseWorkingCopyOptionApiWorkingCopiesGroupIdSelectionPut({ client: connection.client,
      path: { group_id: groupId }, body,
    }));
  },

  modelAnnotations(modelSource: ModelSourceDto): Promise<ModelAnnotationsDto> {
    return call("GET /api/model-annotations", readSavedModelAnnotationsApiModelAnnotationsGet({ client: connection.client, query: modelSource }));
  },

  saveModelAnnotations(body: ModelAnnotationsRequestDto): Promise<ModelAnnotationsDto> {
    return call("PUT /api/model-annotations", writeSavedModelAnnotationsApiModelAnnotationsPut({ client: connection.client, body }));
  },

  bindDocumentModelSource(projectId: string, runId: string, assetSha256: string, modelSource: ModelSourceDto): Promise<SourceDocumentDto> {
    return call(`POST /api/documents/${assetSha256}/model-source`, associateDocumentModelSourceApiDocumentsAssetSha256ModelSourcePost({ client: connection.client,
      path: { asset_sha256: assetSha256 }, body: { projectId, runId, modelSource },
    }));
  },
  project(): Promise<ProjectBindingDto> {
    return call("GET /api/project", readProjectApiProjectGet({ client: connection.client }));
  },

  /** Which projects this server binds, and which one the unscoped paths mean. */
  projects(): Promise<ProjectListDto> {
    return call("GET /api/projects", readProjectsApiProjectsGet({ client: connection.client }));
  },

  state(run?: string, sourceStageRef?: string | null, signal?: AbortSignal): Promise<StateProjectionDto> {
    return call(
      "GET /api/state",
      readStateApiStateGet({ client: connection.client, query: { run, sourceStageRef }, signal }),
    );
  },

  /**
   * The record's declared level/axis references and their dependents. Local
   * model Sync still consumes this; free geometry need not bind to a datum.
   */
  frame(run?: string): Promise<FrameDto> {
    return call("GET /api/state/frame", readFrameApiStateFrameGet({ client: connection.client, query: { run } }));
  },

  /**
   * What changing these refs would move. Read-only despite the POST: the
   * question carries a list and the state it is asked against.
   */
  closure(body: ClosureRequestDto): Promise<ClosureDto> {
    return call(
      "POST /api/state/closure",
      readClosureApiStateClosurePost({ client: connection.client, body }),
    );
  },

  /**
   * The record's massing volumes and what the massing measures. Separate from
   * the frame because a volume is positioned against no level and no axis: it
   * declares its own box.
   */
  volumes(run?: string): Promise<VolumesDto> {
    return call("GET /api/state/volumes", readVolumesApiStateVolumesGet({ client: connection.client, query: { run } }));
  },

  artifacts(signal?: AbortSignal): Promise<ArtifactListDto> {
    return conditional(connection)("GET /api/artifacts", {}, (headers) => readArtifactsApiArtifactsGet({ client: connection.client, headers }), signal);
  },

  /**
   * Make this run's exact STEP into an editable ``.3dm`` through the Rhino on
   * this machine. Ordinary and blocking - the answer is the work model's own
   * artifact row, and asking again for the same source answers with the model
   * already made. Without a local Rhino the server refuses by name.
   */
  exportWorkModel(sha256: string, runId: string): Promise<ProjectArtifactDto> {
    return call(
      `POST /api/artifacts/${sha256}/rhino-export`,
      exportArtifactWorkModelApiArtifactsSha256RhinoExportPost({ client: connection.client, path: { sha256 }, body: { runId } }),
    );
  },

  recordModelLoad(body: ModelLoadTimingDto): Promise<MonitorWriteDto> {
    return call("POST /api/events/model-load", recordModelLoadApiEventsModelLoadPost({ client: connection.client, body }));
  },

  recordClientTiming(body: ClientTimingDto): Promise<MonitorWriteDto> {
    return call("POST /api/events/timing", recordClientTimingApiEventsTimingPost({ client: connection.client, body, keepalive: true }));
  },

  async createTracingPaperReview(body: Omit<TracingPaperReviewRequestDto, "pngBase64">, png: Blob): Promise<SourceDocumentDto> {
    const pngBase64 = base64Of(await png.arrayBuffer());
    return call("POST /api/tracing-paper/reviews", createTracingPaperReviewApiTracingPaperReviewsPost({ client: connection.client, body: { ...body, pngBase64 } }));
  },

  /** Retain this browser-rendered PNG in the loaded run's P036 workspace. */
  async capture(runId: string, png: Blob, modelSource?: ModelSourceDto): Promise<ViewportCaptureDto> {
    const pngBase64 = base64Of(await png.arrayBuffer());
    return call(
      "POST /api/captures",
      createViewportCaptureApiCapturesPost({ client: connection.client,
        body: { runId, pngBase64, ...(modelSource ? { modelSource } : {}) },
      }),
    );
  },

  modelPreview(source: ModelSourceDto): Promise<SourceDocumentDto | null> {
    return call("GET /api/model-assets/preview", readRetainedModelPreviewApiModelAssetsAssetSha256PreviewGet({
      client: connection.client, path: { asset_sha256: source.assetSha256 },
      query: { runId: source.runId, stateDigest: source.stateDigest },
    }));
  },

  /**
   * The projection cache's status for this exact model's thumbnail at `size` (#367): done with its blob's
   * digest, or pending or error (show a placeholder). A miss queues it; the server checks the source every time.
   */
  projection(source: ModelSourceDto, size: number): Promise<ProjectionStatusDto> {
    return call("GET /api/projections", requestProjectionApiProjectionsGet({ client: connection.client,
      query: { runId: source.runId, stateDigest: source.stateDigest, assetSha256: source.assetSha256, size } }));
  },

  /** A projection's PNG, named by its own digest: immutable, so the browser keeps it for good. */
  projectionBlob(sha256: string): Promise<Blob> {
    return call<Blob>(`GET /api/projections/blobs/${sha256}`, readProjectionBlobApiProjectionsBlobsSha256Get({
      client: connection.client, path: { sha256 }, parseAs: "blob" }) as Promise<FieldsResult<Blob>>);
  },

  /**
   * One registered page as the raster Board, Publish and exports all show (#368): a PNG of at most 2048 px,
   * transparency kept, drawn once per document and page where the project keeps a projection cache and drawn
   * on each request where it does not. The document stays the page's source; this only supplies its pixels.
   */
  async documentPage(runId: string, assetSha256: string, revisionRef: string | null, pageIndex: number,
  ): Promise<{ file: File; width: number; height: number }> {
    const blob = await call<Blob>("GET /api/projections/pages", readPageProjectionApiProjectionsPagesGet({ client: connection.client,
      query: { runId, assetSha256, pageIndex, ...(revisionRef ? { revisionRef } : {}) }, parseAs: "blob" }) as Promise<FieldsResult<Blob>>);
    // The PNG's own size, from its IHDR chunk (bytes 16-23, big-endian).
    const header = new DataView(await blob.slice(0, 24).arrayBuffer());
    if (header.byteLength < 24 || header.getUint32(12) !== 0x49484452) throw new Error("The page preview is not a PNG.");
    return { file: new File([blob], `page-${pageIndex + 1}.png`, { type: "image/png" }),
      width: header.getUint32(16), height: header.getUint32(20) };
  },

  async retainRenderView(body: Omit<RenderViewSourceRequestDto, "pngBase64">, png: Blob): Promise<SourceDocumentDto> {
    const pngBase64 = base64Of(await png.arrayBuffer());
    return call("POST /api/render/views", retainRenderViewApiRenderViewsPost({ client: connection.client, body: { ...body, pngBase64 } }));
  },

  /** Retain a 3DM or SKP in this project; the server returns a viewable 3DM artifact. */
  async uploadModel(projectId: string, file: File, signal?: AbortSignal): Promise<ProjectArtifactDto> {
    if (!/\.(3dm|skp)$/i.test(file.name)) throw new Error("Choose a Rhino .3dm or SketchUp .skp model.");
    if (file.size <= 0 || file.size > 128 * 1024 * 1024) throw new Error("Choose a non-empty model up to 128 MiB.");
    signal?.throwIfAborted();
    const bytes = await file.arrayBuffer();
    signal?.throwIfAborted();
    const contentBase64 = base64Of(bytes);
    return call("POST /api/model-assets", createModelAssetApiModelAssetsPost({
      client: connection.client, signal,
      body: { projectId, fileName: file.name, contentBase64 },
    }));
  },

  /**
   * The certified bytes, as a `File` the viewer can open.
   *
   * The name is the receipt's; the digest in the path is what the server
   * verifies the bytes against before it sends them. Bytes this app already
   * downloaded are opened again from memory, without a request, and bytes on
   * the way are waited for, not asked for twice.
   */
  async artifactFile(sha256: string, fileName: string, trace?: OperationTrace, signal?: AbortSignal): Promise<File> {
    const kept = keptModelBytes(sha256);
    if (kept) return new File([kept], fileName, { type: "application/octet-stream" });
    let download = modelDownloads.get(sha256);
    if (!download) {
      const abort = new AbortController();
      const entry: ModelDownload = { abort, waiting: 0, blob: call<Blob>(
        `GET /api/artifacts/${sha256}/bytes`,
        readArtifactBytesApiArtifactsSha256BytesGet({ client: connection.client,
          path: { sha256 },
          headers: traceHeaders(trace),
          parseAs: "blob",
          signal: abort.signal,
        }) as Promise<FieldsResult<Blob>>,
      ).then((blob) => { keepModelBytes(sha256, blob); return blob; })
        .finally(() => { if (modelDownloads.get(sha256) === entry) modelDownloads.delete(sha256); }) };
      entry.blob.catch(() => undefined);
      modelDownloads.set(sha256, download = entry);
    }
    const shared = download;
    shared.waiting += 1;
    try {
      const blob = await new Promise<Blob>((resolve, reject) => {
        const abort = () => reject(new StudioApiError({ status: 0, code: NETWORK_ERROR,
          detail: `GET /api/artifacts/${sha256}/bytes never reached the API: the read was aborted.` }));
        if (signal?.aborted) { abort(); return; }
        signal?.addEventListener("abort", abort, { once: true });
        shared.blob.then(resolve, reject).finally(() => signal?.removeEventListener("abort", abort));
      });
      return new File([blob], fileName, { type: "application/octet-stream" });
    } finally {
      // The download stops only when every opening that waited for it has let go.
      shared.waiting -= 1;
      if (shared.waiting === 0 && signal?.aborted) {
        shared.abort.abort();
        if (modelDownloads.get(sha256) === shared) modelDownloads.delete(sha256);
      }
    }
  },

  documents(runId?: string | null): Promise<SourceDocumentListDto> {
    return conditional(connection)("GET /api/documents", { runId }, (headers) => readDocumentsApiDocumentsGet({ client: connection.client, query: { runId }, headers }));
  },

  renderCapabilities(): Promise<RenderCapabilitiesDto> {
    return call("GET /api/render/capabilities", renderCapabilitiesApiRenderCapabilitiesGet({ client: connection.client }));
  },

  renderJobs(): Promise<RenderJobListDto> {
    return conditional(connection)("GET /api/render/jobs", {}, (headers) => listRenderJobsApiRenderJobsGet({ client: connection.client, headers }));
  },

  /** Only an explicit generation action submits; recovery reads renderJobs. */
  createRender(body: RenderRequestDto): Promise<RenderJobDto> {
    return call("POST /api/render/jobs", createRenderJobApiRenderJobsPost({ client: connection.client, body }));
  },

  /**
   * Give this registered document an editable copy on disk, and say where.
   *
   * Explicitly asked for, never made by watching a project. One file stands for
   * the whole document; asking twice answers with the copy already there, edits
   * intact. What comes back names the document it answers for and a
   * project-relative path, never this machine's. A document no single file can
   * stand for is refused, and the refusal says why.
   */
  createDocumentWorkCopy(projectId: string, runId: string, assetSha256: string, revisionRef?: string | null): Promise<DocumentWorkCopyDto> {
    return call(
      `POST /api/documents/${assetSha256}/work-copy`,
      createDocumentWorkCopyApiDocumentsAssetSha256WorkCopyPost({ client: connection.client,
        path: { asset_sha256: assetSha256 },
        body: { projectId, runId, revisionRef },
      }),
    );
  },

  /** Upload the original bytes; the server validates the MIME type and size. */
  async uploadDocument(projectId: string, runId: string | null, file: File, replacesPages?: SourceDocumentRequestDto["replacesPages"]): Promise<SourceDocumentDto> {
    const contentBase64 = base64Of(await file.arrayBuffer());
    return call(
      "POST /api/documents",
      createDocumentApiDocumentsPost({ client: connection.client,
        body: {
          projectId,
          runId,
          fileName: file.name,
          mimeType: file.type as SourceDocumentRequestDto["mimeType"],
          contentBase64,
          ...(replacesPages ? { replacesPages } : {}),
        },
      }),
    );
  },

  async documentFile(runId: string, assetSha256: string, fileName: string, revisionRef?: string | null, trace?: OperationTrace): Promise<File> {
    const blob = await call<Blob>(
      `GET /api/documents/${assetSha256}/bytes`,
      readDocumentBytesApiDocumentsAssetSha256BytesGet({ client: connection.client,
        path: { asset_sha256: assetSha256 },
        headers: traceHeaders(trace),
        query: { runId, revisionRef },
        parseAs: "blob",
      }) as Promise<FieldsResult<Blob>>,
    );
    return new File([blob], fileName, { type: blob.type });
  },

  documentAnnotations(
    runId: string,
    assetSha256: string,
    pageIndex: number,
    revisionSha256?: string | null,
    drawingRevisionRef?: string | null,
  ): Promise<DocumentAnnotationsDto> {
    return call(
      "GET /api/document-annotations",
      readDocumentPageAnnotationsApiDocumentAnnotationsGet({ client: connection.client,
        query: { runId, assetSha256, pageIndex, revisionSha256, drawingRevisionRef },
      }),
    );
  },

  saveDocumentAnnotations(body: DocumentAnnotationsRequestDto): Promise<DocumentAnnotationsDto> {
    return call(
      "PUT /api/document-annotations",
      writeDocumentPageAnnotationsApiDocumentAnnotationsPut({ client: connection.client, body }),
    );
  },

  documentComments(runId: string): Promise<DocumentCommentsDto> {
    return call(
      "GET /api/document-comments",
      readSubmittedDocumentCommentsApiDocumentCommentsGet({ client: connection.client, query: { runId } }),
    );
  },

  resolvePick(body: PickRequestDto): Promise<PickResolutionDto> {
    return call("POST /api/pick/resolve", resolveApiPickResolvePost({ client: connection.client, body }));
  },

  createProposal(body: ProposalRequestDto): Promise<ProposalDto> {
    return call("POST /api/proposals", createProposalApiProposalsPost({ client: connection.client, body }));
  },

  /**
   * The architect's sentence, compiled by the process's intent agent and typed
   * by the grammar. The answer carries the agent's reading beside the record's
   * proposal, and a question comes back as the same BLOCKED_NEEDS_HUMAN a typed
   * sentence would get.
   */
  compileIntent(body: IntentRequestDto, trace?: OperationTrace): Promise<IntentDto> {
    return call("POST /api/intents", compileIntentApiIntentsPost({ client: connection.client, body, headers: traceHeaders(trace) }));
  },

  /** One finished drawing action, as the proposal it already is. */
  sketch(body: SketchPrismRequestDto): Promise<ProposalDto> {
    return call("POST /api/proposals/sketch", createSketchProposalApiProposalsSketchPost({ client: connection.client, body }));
  },

  /** Several finished drawing actions as one proposal; later items may reference earlier ones. */
  sketchBatch(body: SketchBatchRequestDto): Promise<ProposalDto> {
    return call("POST /api/proposals/sketch", createSketchProposalApiProposalsSketchPost({ client: connection.client, body }));
  },
  traceDocument(body: DocumentTracingRequestDto): Promise<ProposalDto> {
    return call("POST /api/proposals/sketch", createSketchProposalApiProposalsSketchPost({ client: connection.client, body }));
  },

  parameterLocks(body: ParameterLocksRequestDto): Promise<ProposalDto> {
    return call("POST /api/proposals/parameter-locks", createParameterLocksProposalApiProposalsParameterLocksPost({ client: connection.client, body }));
  },

  /** Prepare an empty project for its first sketch (idempotent; seeds component `model` and level `ground`). */
  prepareModeling(projectId: string): Promise<ModelingInitializeDto> {
    return call("POST /api/project/modeling", prepareModelingApiProjectModelingPost({ client: connection.client, body: { projectId } }));
  },

  transform(body: TransformElementRequestDto): Promise<ProposalDto> {
    return call("POST /api/proposals/transform", createTransformProposalApiProposalsTransformPost({ client: connection.client, body }));
  },

  pushPull(body: PushPullRequestDto): Promise<ProposalDto> {
    return call("POST /api/proposals/push-pull", createPushPullProposalApiProposalsPushPullPost({ client: connection.client, body }));
  },

  removeElement(body: DeleteElementRequestDto): Promise<ProposalDto> {
    return call("POST /api/proposals/delete", createDeleteProposalApiProposalsDeletePost({ client: connection.client, body }));
  },

  editElevation(body: ElevationEditRequestDto): Promise<ProposalDto> {
    return call("POST /api/proposals/elevation", createElevationProposalApiProposalsElevationPost({ client: connection.client, body }));
  },

  proposal(proposalId: string): Promise<ProposalDto> {
    return call(
      `GET /api/proposals/${proposalId}`,
      readProposalApiProposalsProposalIdGet({ client: connection.client, path: { proposal_id: proposalId } }),
    );
  },

  startCandidate(proposalId: string, trace?: OperationTrace, requestId?: string): Promise<CandidateAcceptedDto> {
    return call(
      `POST /api/proposals/${proposalId}/candidate`,
      startCandidateApiProposalsProposalIdCandidatePost({ client: connection.client,
        path: { proposal_id: proposalId },
        headers: { ...traceHeaders(trace), ...(requestId ? { "Idempotency-Key": requestId } : {}) },
      }),
    );
  },

  runtime(candidateId: string): Promise<RuntimeDto> {
    return call("GET /api/runtime", readRuntimeApiRuntimeGet({ client: connection.client, query: { candidateId: [candidateId] } }));
  },

  job(jobId: string, trace?: OperationTrace): Promise<JobDto> {
    return call(
      `GET /api/jobs/${jobId}`,
      readJobApiJobsJobIdGet({ client: connection.client, path: { job_id: jobId }, headers: traceHeaders(trace) }),
    );
  },

  candidate(candidateId: string, trace?: OperationTrace): Promise<CandidateDto> {
    return call(
      `GET /api/candidates/${candidateId}`,
      readCandidateApiCandidatesCandidateIdGet({ client: connection.client,
        path: { candidate_id: candidateId },
        headers: traceHeaders(trace),
      }),
    );
  },

  validation(candidateId: string): Promise<ValidationDto> {
    return call(
      `GET /api/candidates/${candidateId}/validation`,
      readValidationApiCandidatesCandidateIdValidationGet({ client: connection.client,
        path: { candidate_id: candidateId },
      }),
    );
  },

  /** Before / After / Why: this candidate's exports against another run's. */
  compare(candidateId: string, against: string): Promise<CompareDto> {
    return call(
      `GET /api/candidates/${candidateId}/compare`,
      compareCandidateApiCandidatesCandidateIdCompareGet({ client: connection.client,
        path: { candidate_id: candidateId },
        query: { against },
      }),
    );
  },
});

export type StudioClient = ReturnType<typeof createStudioClient>;
