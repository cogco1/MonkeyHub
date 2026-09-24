/**
 * Project-bound wrappers around the generated Project Runtime SDK.
 * Request and response shapes remain generated; every call uses the connection
 * supplied to this factory and every non-2xx throws the server's StudioApiError.
 */

import type { ServerConnection } from "./connection";
import { renderCapabilitiesApiRenderCapabilitiesGet, listRenderJobsApiRenderJobsGet, createRenderJobApiRenderJobsPost } from "./generated";
import type { RenderCapabilitiesDto, RenderJobListDto, RenderJobDto, RenderRequestDto } from "./generated";
import type { ElevationEditRequestDto } from "./generated";
import type { SaveStudyRequestDto, StudyViewDto, ProposeStudyRequestDto } from "./generated";
import type { PlanRequestDto, PlanStatusRequestDto, PlanStatusDto, PlanDimensionProposalRequestDto, PlanDimensionChoicesDto } from "./generated";
import { createPlanApiDrawingsPlansPost, readPlanStatusApiDrawingsPlansStatusPost,
  readPlanDimensionChoicesApiDrawingsPlansDimensionsGet, createPlanDimensionProposalApiDrawingsPlansDimensionProposalPost } from "./generated";
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
  selectCurrentWorkingDraftApiWorkingDraftPut,
  saveCurrentWorkingDraftApiWorkingDraftSavePost,
  retainLocalWorkingDraftApiWorkingDraftLocalPut,
  readBoardApiBoardGet,
  updateBoardApiBoardPut,
  readCommittedDesignHistoryApiDesignHistoryGet,
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
  exportBoardApiBoardExportPost,
  getUserSettingsApiSettingsUserGet,
  putUserSettingsApiSettingsUserPut,
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
  WorkingDraftDto, WorkingDraftSelectionDto, WorkingDraftSaveDto, LocalDraftRequestDto,
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
  UserSettingsDto,
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

// Workspace startup reads `/api/project` and `/api/state`; runtime liveness
// belongs to the Hub that started it.
export const createStudioClient = (connection: ServerConnection) => ({
  workingDraft(): Promise<WorkingDraftDto> {
    return call("GET /api/working-draft", readCurrentWorkingDraftApiWorkingDraftGet({ client: connection.client }));
  },
  selectWorkingDraft(body: WorkingDraftSelectionDto): Promise<WorkingDraftDto> {
    return call("PUT /api/working-draft", selectCurrentWorkingDraftApiWorkingDraftPut({ client: connection.client, body }));
  },
  saveWorkingDraft(body: WorkingDraftSaveDto): Promise<WorkingDraftDto> {
    return call("POST /api/working-draft/save", saveCurrentWorkingDraftApiWorkingDraftSavePost({ client: connection.client, body }));
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
    return call("GET /api/board", readBoardApiBoardGet({ client: connection.client }));
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
  drawingPlanStatus(body: PlanStatusRequestDto): Promise<PlanStatusDto> {
    return call("POST /api/drawings/plans/status", readPlanStatusApiDrawingsPlansStatusPost({ client: connection.client, body }));
  },
  drawingPlanDimensions(source: ModelSourceDto, sourceStageRef?: string | null): Promise<PlanDimensionChoicesDto> {
    return call("GET /api/drawings/plans/dimensions", readPlanDimensionChoicesApiDrawingsPlansDimensionsGet({ client: connection.client, query: {
      sourceRunId: source.runId, stateDigest: source.stateDigest, assetSha256: source.assetSha256, sourceStageRef,
    } }));
  },
  drawingDimensionProposal(body: PlanDimensionProposalRequestDto): Promise<ProposalDto> {
    return call("POST /api/drawings/plans/dimension-proposal", createPlanDimensionProposalApiDrawingsPlansDimensionProposalPost({ client: connection.client, body }));
  },
  combineCandidates(body: CombineCandidatesRequestDto): Promise<CandidateAcceptedDto> {
    return call("POST /api/candidates/combine", combineCandidatesApiCandidatesCombinePost({ client: connection.client, body }));
  },
  designHistory(branchId = "main", signal?: AbortSignal): Promise<DesignHistoryDto> {
    return call("GET /api/design-history", readCommittedDesignHistoryApiDesignHistoryGet({ client: connection.client, query: { branchId }, signal }));
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
  userSettings(): Promise<UserSettingsDto> {
    return call("GET /api/settings/user", getUserSettingsApiSettingsUserGet({ client: connection.client }));
  },

  saveUserSettings(body: UserSettingsDto): Promise<UserSettingsDto> {
    return call("PUT /api/settings/user", putUserSettingsApiSettingsUserPut({ client: connection.client, body }));
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
    return call("GET /api/artifacts", readArtifactsApiArtifactsGet({ client: connection.client, signal }));
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
  async capture(runId: string, png: Blob): Promise<ViewportCaptureDto> {
    const pngBase64 = base64Of(await png.arrayBuffer());
    return call(
      "POST /api/captures",
      createViewportCaptureApiCapturesPost({ client: connection.client,
        body: { runId, pngBase64 },
      }),
    );
  },

  /** Retain the original 3DM in this project before exposing it to downstream workspaces. */
  async uploadModel(projectId: string, file: File, signal?: AbortSignal): Promise<ProjectArtifactDto> {
    if (!file.name.toLowerCase().endsWith(".3dm")) throw new Error("Choose a Rhino .3dm model.");
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
   * verifies the bytes against before it sends them.
   */
  async artifactFile(sha256: string, fileName: string, trace?: OperationTrace, signal?: AbortSignal): Promise<File> {
    const blob = await call<Blob>(
      `GET /api/artifacts/${sha256}/bytes`,
      readArtifactBytesApiArtifactsSha256BytesGet({ client: connection.client,
        path: { sha256 },
        headers: traceHeaders(trace),
        parseAs: "blob",
        signal,
      }) as Promise<FieldsResult<Blob>>,
    );
    return new File([blob], fileName, { type: "application/octet-stream" });
  },

  documents(runId?: string | null): Promise<SourceDocumentListDto> {
    return call("GET /api/documents", readDocumentsApiDocumentsGet({ client: connection.client, query: { runId } }));
  },

  renderCapabilities(): Promise<RenderCapabilitiesDto> {
    return call("GET /api/render/capabilities", renderCapabilitiesApiRenderCapabilitiesGet({ client: connection.client }));
  },

  renderJobs(): Promise<RenderJobListDto> {
    return call("GET /api/render/jobs", listRenderJobsApiRenderJobsGet({ client: connection.client }));
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
