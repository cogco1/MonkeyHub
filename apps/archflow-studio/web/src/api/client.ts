/**
 * The one place this app talks to the Studio API.
 *
 * Two rules hold here and nowhere else has to repeat them.
 *
 * Every call goes through the generated SDK, so the request and response shapes
 * are the server's own OpenAPI and not a hand-written mirror of it. Nothing in
 * `src/` declares a DTO.
 *
 * And every non-2xx becomes a thrown `StudioApiError` carrying the server's own
 * `code` and `detail` (and `question` / `acceptedForms` when the server sent
 * them). A failed call never resolves — least of all to `[]` or `null` — because
 * an empty list is the one way a browser can turn a server's refusal into a
 * silent, confident-looking answer.
 */

import { client } from "./generated/client.gen";
import {
  createProposalApiProposalsPost,
  readArtifactBytesApiArtifactsSha256BytesGet,
  readArtifactsApiArtifactsGet,
  readCandidateApiCandidatesCandidateIdGet,
  readHealthApiHealthGet,
  readJobApiJobsJobIdGet,
  readProjectApiProjectGet,
  readProposalApiProposalsProposalIdGet,
  readStateApiStateGet,
  readValidationApiCandidatesCandidateIdValidationGet,
  resolveApiPickResolvePost,
  startCandidateApiProposalsProposalIdCandidatePost,
} from "./generated";
import type {
  ArtifactListDto,
  CandidateAcceptedDto,
  CandidateDto,
  JobDto,
  PickRequestDto,
  PickResolutionDto,
  ProjectBindingDto,
  ProposalDto,
  ProposalRequestDto,
  StateProjectionDto,
  StudioHealth,
  ValidationDto,
} from "./generated";

// The generated client's own default base URL is derived from where the schema
// was read; this app is served beside the API it talks to, and the generated
// paths already carry the `/api` prefix. Vite proxies that prefix in dev.
client.setConfig({ baseUrl: "" });

/** The route prefix the SSE panel opens `EventSource` against. */
export const EVENTS_URL = "/api/events";

/** A failure this client could not read as the API's error body. */
export const TRANSPORT_ERROR = "TRANSPORT_ERROR";

/** A request that never reached the API at all. */
export const NETWORK_ERROR = "NETWORK_ERROR";

/** The one error every call in this module throws. */
export class StudioApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string;
  readonly question: string | null;
  readonly acceptedForms: string[];

  constructor(init: {
    status: number;
    code: string;
    detail: string;
    question?: string | null;
    acceptedForms?: string[] | null;
  }) {
    super(`${init.code}: ${init.detail}`);
    this.name = "StudioApiError";
    this.status = init.status;
    this.code = init.code;
    this.detail = init.detail;
    this.question = init.question ?? null;
    this.acceptedForms = init.acceptedForms ?? [];
  }
}

/** Whatever is thrown or returned, as the error a panel can render. */
export function asStudioApiError(cause: unknown): StudioApiError {
  if (cause instanceof StudioApiError) return cause;
  return new StudioApiError({
    status: 0,
    code: TRANSPORT_ERROR,
    detail: describe(cause),
  });
}

function describe(cause: unknown): string {
  if (cause instanceof Error) return cause.message;
  if (typeof cause === "string") return cause;
  return "the client could not describe this failure";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function stringList(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  const items = value.filter((item): item is string => typeof item === "string");
  return items.length === value.length ? items : null;
}

/**
 * The API's error body, or an honest admission that this was not one.
 *
 * A gateway, a proxy or a crash can answer with HTML or with nothing at all.
 * Those become `TRANSPORT_ERROR` carrying the HTTP status rather than being
 * dressed up as a code the server never issued.
 */
function toStudioApiError(
  what: string,
  body: unknown,
  response: Response | undefined,
): StudioApiError {
  const status = response?.status ?? 0;
  if (
    isRecord(body) &&
    typeof body.code === "string" &&
    typeof body.detail === "string"
  ) {
    return new StudioApiError({
      status,
      code: body.code,
      detail: body.detail,
      question: typeof body.question === "string" ? body.question : null,
      acceptedForms: stringList(body.acceptedForms),
    });
  }
  const said =
    typeof body === "string" && body.trim()
      ? ` It said: ${body.trim().slice(0, 400)}`
      : "";
  return new StudioApiError({
    status,
    code: TRANSPORT_ERROR,
    detail:
      `${what} answered HTTP ${status || "(no response)"} with a body this ` +
      `client could not read as the API's {code, detail}.${said}`,
  });
}

interface FieldsResult<T> {
  data?: T;
  error?: unknown;
  response?: Response;
}

/** Run one generated SDK call and give back its data, or throw. */
async function call<T>(
  what: string,
  pending: Promise<FieldsResult<T>>,
): Promise<T> {
  let result: FieldsResult<T>;
  try {
    result = await pending;
  } catch (cause) {
    // The client only rejects when something outside the response cycle broke.
    throw new StudioApiError({
      status: 0,
      code: NETWORK_ERROR,
      detail: `${what} never reached the API: ${describe(cause)}`,
    });
  }
  if (result.error !== undefined) {
    if (result.response === undefined) {
      throw new StudioApiError({
        status: 0,
        code: NETWORK_ERROR,
        detail: `${what} never reached the API: ${describe(result.error)}`,
      });
    }
    throw toStudioApiError(what, result.error, result.response);
  }
  if (result.data === undefined) {
    throw new StudioApiError({
      status: result.response?.status ?? 0,
      code: TRANSPORT_ERROR,
      detail: `${what} succeeded but carried no body.`,
    });
  }
  return result.data;
}

export const studio = {
  health(): Promise<StudioHealth> {
    return call("GET /api/health", readHealthApiHealthGet());
  },

  project(): Promise<ProjectBindingDto> {
    return call("GET /api/project", readProjectApiProjectGet());
  },

  state(run?: string): Promise<StateProjectionDto> {
    return call(
      "GET /api/state",
      readStateApiStateGet(run === undefined ? {} : { query: { run } }),
    );
  },

  artifacts(): Promise<ArtifactListDto> {
    return call("GET /api/artifacts", readArtifactsApiArtifactsGet());
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

  resolvePick(body: PickRequestDto): Promise<PickResolutionDto> {
    return call("POST /api/pick/resolve", resolveApiPickResolvePost({ body }));
  },

  createProposal(body: ProposalRequestDto): Promise<ProposalDto> {
    return call("POST /api/proposals", createProposalApiProposalsPost({ body }));
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
};
