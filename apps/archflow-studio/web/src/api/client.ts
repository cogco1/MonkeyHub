/**
 * The one place this app talks to the Studio API.
 *
 * Three rules hold here and nowhere else has to repeat them.
 *
 * Every call goes through the generated SDK, so the request and response shapes
 * are the server's own OpenAPI and not a hand-written mirror of it. Nothing in
 * `src/` declares a DTO.
 *
 * Every call goes to the one `ServerConnection` (`connection.ts`), which owns
 * the base URL and the token and has already refused a server whose protocol
 * major this client does not speak. Nothing here chooses a URL.
 *
 * And every non-2xx becomes a thrown `StudioApiError` (`error.ts`) carrying the
 * server's own `code` and `detail`. A failed call never resolves — least of all
 * to `[]` or `null` — because an empty list is the one way a browser can turn a
 * server's refusal into a silent, confident-looking answer.
 */

import { connection } from "./connection";
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
  readBoardApiBoardGet,
  updateBoardApiBoardPut,
  readCommittedDesignHistoryApiDesignHistoryGet,
  createElevationApiDrawingsElevationsPost,
  combineCandidatesApiCandidatesCombinePost,
  initializeCommittedDesignApiDesignStagesInitializePost,
  acceptCommittedDesignApiCandidatesCandidateIdAcceptPost,
  forkCommittedDesignApiDesignBranchesPost,
  applyProgramApiProgramPost,
  associateDocumentModelSourceApiDocumentsAssetSha256ModelSourcePost,
  chooseWorkingCopyOptionApiWorkingCopiesGroupIdSelectionPut,
  compileIntentApiIntentsPost,
  createDocumentApiDocumentsPost,
  createProposalApiProposalsPost,
  createViewportCaptureApiCapturesPost,
  getUserSettingsApiSettingsUserGet,
  putUserSettingsApiSettingsUserPut,
  readArtifactBytesApiArtifactsSha256BytesGet,
  recordModelLoadApiEventsModelLoadPost,
  readArtifactsApiArtifactsGet,
  readCandidateApiCandidatesCandidateIdGet,
  readDocumentBytesApiDocumentsAssetSha256BytesGet,
  readDocumentPageAnnotationsApiDocumentAnnotationsGet,
  readDocumentsApiDocumentsGet,
  readJobApiJobsJobIdGet,
  readSavedModelAnnotationsApiModelAnnotationsGet,
  readWorkingCopiesApiWorkingCopiesGet,
  readProjectApiProjectGet,
  readProjectsApiProjectsGet,
  readProposalApiProposalsProposalIdGet,
  readClosureApiStateClosurePost,
  readFrameApiStateFrameGet,
  readOptionsApiOptionsGet,
  readSemanticsApiSemanticsGet,
  readSheetApiProgramGet,
  readStateApiStateGet,
  readSubmittedDocumentCommentsApiDocumentCommentsGet,
  readVolumesApiStateVolumesGet,
  makeMassingOptionApiOptionsPost,
  selectOptionApiOptionsOptionIdSelectPost,
  readValidationApiCandidatesCandidateIdValidationGet,
  compareCandidateApiCandidatesCandidateIdCompareGet,
  resolveApiPickResolvePost,
  startCandidateApiProposalsProposalIdCandidatePost,
  writeDocumentPageAnnotationsApiDocumentAnnotationsPut,
  writeSavedModelAnnotationsApiModelAnnotationsPut,
} from "./generated";
import type {
  BoardDto, BoardRequestDto,
  DesignHistoryDto, DesignStageDto, DesignBranchDto,
  ElevationRequestDto, CombineCandidatesRequestDto,
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
  FrameDto,
  IntentDto,
  IntentRequestDto,
  JobDto,
  MassingOptionDto,
  MassingOptionRequestDto,
  ModelAnnotationsDto,
  ModelAnnotationsRequestDto,
  ModelSourceDto,
  ModelLoadTimingDto,
  MonitorWriteDto,
  OptionsDto,
  PickRequestDto,
  PickResolutionDto,
  ProgramApplyRequestDto,
  ProgramCandidateDto,
  ProgramDto,
  ProjectBindingDto,
  ProjectListDto,
  ProposalDto,
  ProposalRequestDto,
  SemanticsDto,
  SourceDocumentDto,
  SourceDocumentListDto,
  SourceDocumentRequestDto,
  StateProjectionDto,
  UserSettingsDto,
  ValidationDto,
  ViewportCaptureDto,
  VolumesDto,
  WorkingCopyDto,
  WorkingCopyListDto,
  WorkingCopySelectionRequestDto,
} from "./generated";

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

/**
 * The route the SSE panel opens `EventSource` against, on this connection's
 * server. `EventSource` sends no `Authorization` header, so an authenticated
 * server is a case this stream does not yet cover; the protocol document says
 * so rather than this client pretending otherwise.
 */
export const EVENTS_URL = connection.url("/api/events");

function base64Of(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 32_768;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return window.btoa(binary);
}

// `GET /api/health` has no wrapper here on purpose. It answers whether the
// process is up and whether its project binding opens — an operator's question,
// asked with curl or by a liveness probe. This shell's own first question is
// stronger and is the one it asks: `GET /api/project` and `GET /api/state`
// either return the binding or fail with a code the top bar renders.
export const studio = {
  board(): Promise<BoardDto> {
    return call("GET /api/board", readBoardApiBoardGet());
  },
  saveBoard(body: BoardRequestDto): Promise<BoardDto> {
    return call("PUT /api/board", updateBoardApiBoardPut({ body }));
  },
  elevation(body: ElevationRequestDto): Promise<SourceDocumentDto> {
    return call("POST /api/drawings/elevations", createElevationApiDrawingsElevationsPost({ body }));
  },
  combineCandidates(body: CombineCandidatesRequestDto): Promise<CandidateAcceptedDto> {
    return call("POST /api/candidates/combine", combineCandidatesApiCandidatesCombinePost({ body }));
  },
  designHistory(branchId = "main"): Promise<DesignHistoryDto> {
    return call("GET /api/design-history", readCommittedDesignHistoryApiDesignHistoryGet({ query: { branchId } }));
  },
  initializeStage(body: InitializeDesignStageRequestDto): Promise<DesignStageDto> {
    return call("POST /api/design-stages/initialize", initializeCommittedDesignApiDesignStagesInitializePost({ body }));
  },
  acceptCandidate(candidateId: string, body: AcceptDesignCandidateRequestDto): Promise<DesignStageDto> {
    return call("POST /api/candidates/accept", acceptCommittedDesignApiCandidatesCandidateIdAcceptPost({ path: { candidate_id: candidateId }, body }));
  },
  forkBranch(body: ForkDesignBranchRequestDto): Promise<DesignBranchDto> {
    return call("POST /api/design-branches", forkCommittedDesignApiDesignBranchesPost({ body }));
  },
  userSettings(): Promise<UserSettingsDto> {
    return call("GET /api/settings/user", getUserSettingsApiSettingsUserGet());
  },

  saveUserSettings(body: UserSettingsDto): Promise<UserSettingsDto> {
    return call("PUT /api/settings/user", putUserSettingsApiSettingsUserPut({ body }));
  },

  workingCopies(): Promise<WorkingCopyListDto> {
    return call("GET /api/working-copies", readWorkingCopiesApiWorkingCopiesGet());
  },

  selectWorkingCopy(groupId: string, body: WorkingCopySelectionRequestDto): Promise<WorkingCopyDto> {
    return call(`PUT /api/working-copies/${groupId}/selection`, chooseWorkingCopyOptionApiWorkingCopiesGroupIdSelectionPut({
      path: { group_id: groupId }, body,
    }));
  },

  modelAnnotations(modelSource: ModelSourceDto): Promise<ModelAnnotationsDto> {
    return call("GET /api/model-annotations", readSavedModelAnnotationsApiModelAnnotationsGet({ query: modelSource }));
  },

  saveModelAnnotations(body: ModelAnnotationsRequestDto): Promise<ModelAnnotationsDto> {
    return call("PUT /api/model-annotations", writeSavedModelAnnotationsApiModelAnnotationsPut({ body }));
  },

  bindDocumentModelSource(projectId: string, runId: string, assetSha256: string, modelSource: ModelSourceDto): Promise<SourceDocumentDto> {
    return call(`POST /api/documents/${assetSha256}/model-source`, associateDocumentModelSourceApiDocumentsAssetSha256ModelSourcePost({
      path: { asset_sha256: assetSha256 }, body: { projectId, runId, modelSource },
    }));
  },
  project(): Promise<ProjectBindingDto> {
    return call("GET /api/project", readProjectApiProjectGet());
  },

  /** Which projects this server binds, and which one the unscoped paths mean. */
  projects(): Promise<ProjectListDto> {
    return call("GET /api/projects", readProjectsApiProjectsGet());
  },

  state(run?: string, sourceStageRef?: string | null): Promise<StateProjectionDto> {
    return call(
      "GET /api/state",
      readStateApiStateGet({ query: { run, sourceStageRef } }),
    );
  },

  /**
   * The record's frame: the levels and axes every element is positioned
   * against, each with what stands on it and what changing it would move.
   */
  frame(run?: string): Promise<FrameDto> {
    return call("GET /api/state/frame", readFrameApiStateFrameGet({ query: { run } }));
  },

  /**
   * What changing these refs would move. Read-only despite the POST: the
   * question carries a list and the state it is asked against.
   */
  closure(body: ClosureRequestDto): Promise<ClosureDto> {
    return call(
      "POST /api/state/closure",
      readClosureApiStateClosurePost({ body }),
    );
  },

  /**
   * The record's massing volumes and what the massing measures. Separate from
   * the frame because a volume is positioned against no level and no axis: it
   * declares its own box.
   */
  volumes(run?: string): Promise<VolumesDto> {
    return call("GET /api/state/volumes", readVolumesApiStateVolumesGet({ query: { run } }));
  },

  /** The baseline and every massing option this server process holds. */
  options(run?: string | null): Promise<OptionsDto> {
    return call(
      "GET /api/options",
      readOptionsApiOptionsGet(run == null ? {} : { query: { run } }),
    );
  },

  /** One deterministic transform of the record's massing, measured. */
  makeOption(body: MassingOptionRequestDto): Promise<MassingOptionDto> {
    return call("POST /api/options", makeMassingOptionApiOptionsPost({ body }));
  },

  /**
   * Run one option as a candidate. A 202 and a job, like every other candidate:
   * selecting a massing is a geometry run, not a note.
   */
  selectOption(optionId: string): Promise<CandidateAcceptedDto> {
    return call(
      `POST /api/options/${optionId}/select`,
      selectOptionApiOptionsOptionIdSelectPost({ path: { option_id: optionId } }),
    );
  },

  /**
   * The project's program sheet: the architect's own where one is authored,
   * else the record's own reading of its zones. `source` says which.
   */
  program(run?: string | null): Promise<ProgramDto> {
    return call(
      "GET /api/program",
      readSheetApiProgramGet(run == null ? {} : { query: { run } }),
    );
  },

  /**
   * Apply a sheet to the record as a candidate run. Answers 202 with the run
   * it will become; the authored record is never rewritten.
   */
  applyProgram(body: ProgramApplyRequestDto): Promise<ProgramCandidateDto> {
    return call("POST /api/program", applyProgramApiProgramPost({ body }));
  },

  /**
   * Every role and condition canonical state may name. The function dropdown
   * is built from this rather than from a list this client carries: the
   * record refuses a term the registry does not know.
   */
  semantics(): Promise<SemanticsDto> {
    return call("GET /api/semantics", readSemanticsApiSemanticsGet());
  },

  artifacts(): Promise<ArtifactListDto> {
    return call("GET /api/artifacts", readArtifactsApiArtifactsGet());
  },

  recordModelLoad(body: ModelLoadTimingDto): Promise<MonitorWriteDto> {
    return call("POST /api/events/model-load", recordModelLoadApiEventsModelLoadPost({ body }));
  },

  /** Retain this browser-rendered PNG in the loaded run's P036 workspace. */
  async capture(runId: string, png: Blob): Promise<ViewportCaptureDto> {
    const pngBase64 = base64Of(await png.arrayBuffer());
    return call(
      "POST /api/captures",
      createViewportCaptureApiCapturesPost({
        body: { runId, pngBase64 },
      }),
    );
  },

  /**
   * The certified bytes, as a `File` the viewer can open.
   *
   * The name is the receipt's; the digest in the path is what the server
   * verifies the bytes against before it sends them.
   */
  async artifactFile(sha256: string, fileName: string): Promise<File> {
    const blob = await call<Blob>(
      `GET /api/artifacts/${sha256}/bytes`,
      readArtifactBytesApiArtifactsSha256BytesGet({
        path: { sha256 },
        parseAs: "blob",
      }) as Promise<FieldsResult<Blob>>,
    );
    return new File([blob], fileName, { type: "application/octet-stream" });
  },

  documents(runId?: string | null): Promise<SourceDocumentListDto> {
    return call("GET /api/documents", readDocumentsApiDocumentsGet({ query: { runId } }));
  },

  /** Upload the original bytes; the server validates the MIME type and size. */
  async uploadDocument(projectId: string, runId: string | null, file: File): Promise<SourceDocumentDto> {
    const contentBase64 = base64Of(await file.arrayBuffer());
    return call(
      "POST /api/documents",
      createDocumentApiDocumentsPost({
        body: {
          projectId,
          runId,
          fileName: file.name,
          mimeType: file.type as SourceDocumentRequestDto["mimeType"],
          contentBase64,
        },
      }),
    );
  },

  async documentFile(runId: string, assetSha256: string, fileName: string, revisionRef?: string | null): Promise<File> {
    const blob = await call<Blob>(
      `GET /api/documents/${assetSha256}/bytes`,
      readDocumentBytesApiDocumentsAssetSha256BytesGet({
        path: { asset_sha256: assetSha256 },
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
      readDocumentPageAnnotationsApiDocumentAnnotationsGet({
        query: { runId, assetSha256, pageIndex, revisionSha256, drawingRevisionRef },
      }),
    );
  },

  saveDocumentAnnotations(body: DocumentAnnotationsRequestDto): Promise<DocumentAnnotationsDto> {
    return call(
      "PUT /api/document-annotations",
      writeDocumentPageAnnotationsApiDocumentAnnotationsPut({ body }),
    );
  },

  documentComments(runId: string): Promise<DocumentCommentsDto> {
    return call(
      "GET /api/document-comments",
      readSubmittedDocumentCommentsApiDocumentCommentsGet({ query: { runId } }),
    );
  },

  resolvePick(body: PickRequestDto): Promise<PickResolutionDto> {
    return call("POST /api/pick/resolve", resolveApiPickResolvePost({ body }));
  },

  createProposal(body: ProposalRequestDto): Promise<ProposalDto> {
    return call("POST /api/proposals", createProposalApiProposalsPost({ body }));
  },

  /**
   * The architect's sentence, compiled by the process's intent agent and typed
   * by the grammar. The answer carries the agent's reading beside the record's
   * proposal, and a question comes back as the same BLOCKED_NEEDS_HUMAN a typed
   * sentence would get.
   */
  compileIntent(body: IntentRequestDto): Promise<IntentDto> {
    return call("POST /api/intents", compileIntentApiIntentsPost({ body }));
  },

  proposal(proposalId: string): Promise<ProposalDto> {
    return call(
      `GET /api/proposals/${proposalId}`,
      readProposalApiProposalsProposalIdGet({ path: { proposal_id: proposalId } }),
    );
  },

  startCandidate(proposalId: string): Promise<CandidateAcceptedDto> {
    return call(
      `POST /api/proposals/${proposalId}/candidate`,
      startCandidateApiProposalsProposalIdCandidatePost({
        path: { proposal_id: proposalId },
      }),
    );
  },

  job(jobId: string): Promise<JobDto> {
    return call(
      `GET /api/jobs/${jobId}`,
      readJobApiJobsJobIdGet({ path: { job_id: jobId } }),
    );
  },

  candidate(candidateId: string): Promise<CandidateDto> {
    return call(
      `GET /api/candidates/${candidateId}`,
      readCandidateApiCandidatesCandidateIdGet({
        path: { candidate_id: candidateId },
      }),
    );
  },

  validation(candidateId: string): Promise<ValidationDto> {
    return call(
      `GET /api/candidates/${candidateId}/validation`,
      readValidationApiCandidatesCandidateIdValidationGet({
        path: { candidate_id: candidateId },
      }),
    );
  },

  /** Before / After / Why: this candidate's exports against another run's. */
  compare(candidateId: string, against: string): Promise<CompareDto> {
    return call(
      `GET /api/candidates/${candidateId}/compare`,
      compareCandidateApiCandidatesCandidateIdCompareGet({
        path: { candidate_id: candidateId },
        query: { against },
      }),
    );
  },
};
