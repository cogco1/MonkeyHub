/**
 * How a failure becomes something this app can show.
 *
 * Every non-2xx becomes a thrown `StudioApiError` carrying the server's own
 * `code` and `detail` (and `question` / `acceptedForms` when the server sent
 * them). A failed call never resolves — least of all to `[]` or `null` —
 * because an empty list is the one way a browser can turn a server's refusal
 * into a silent, confident-looking answer.
 *
 * This lives beside `client.ts` rather than inside it because the connection
 * (`connection.ts`) refuses servers before any call in `client.ts` is made,
 * and it refuses them in the same shape. One error type, read by one panel.
 */

import type { AuthoredControlDraftDto, PendingIntentDto } from "./generated";

/** A failure this client could not read as the API's error body. */
export const TRANSPORT_ERROR = "TRANSPORT_ERROR";

/** A request that never reached the API at all. */
export const NETWORK_ERROR = "NETWORK_ERROR";

/** The codes an intent's three refusing outcomes arrive under. */
export const BLOCKED_NEEDS_HUMAN = "BLOCKED_NEEDS_HUMAN";
export const MISSING_EDITABLE_CONTROL = "MISSING_EDITABLE_CONTROL";
export const UNSUPPORTED_REQUEST = "UNSUPPORTED_REQUEST";
export const STALE_CLARIFICATION = "STALE_CLARIFICATION";

/** The one error every call in this app throws. */
export class StudioApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string;
  readonly question: string | null;
  readonly acceptedForms: string[];
  /**
   * Which of the four closed answers this refusal is, when the server said.
   * Not every refusal belongs to an intent, so it can be null.
   */
  readonly outcome: string | null;
  /**
   * The exchange this refusal belongs to. Its `continuationToken` is the whole
   * of the continuity — the next request carries it back and nothing else, so
   * no transcript is sent — and a null token means the answer was terminal and
   * there is nothing left to ask.
   */
  readonly pendingIntent: PendingIntentDto | null;
  /** What would have to be authored, when the answer is that nothing can be. */
  readonly authoredControlDraft: AuthoredControlDraftDto | null;

  constructor(init: {
    status: number;
    code: string;
    detail: string;
    question?: string | null;
    acceptedForms?: string[] | null;
    outcome?: string | null;
    pendingIntent?: PendingIntentDto | null;
    authoredControlDraft?: AuthoredControlDraftDto | null;
  }) {
    super(`${init.code}: ${init.detail}`);
    this.name = "StudioApiError";
    this.status = init.status;
    this.code = init.code;
    this.detail = init.detail;
    this.question = init.question ?? null;
    this.acceptedForms = init.acceptedForms ?? [];
    this.outcome = init.outcome ?? null;
    this.pendingIntent = init.pendingIntent ?? null;
    this.authoredControlDraft = init.authoredControlDraft ?? null;
  }

  /** Whether this refusal can still be answered, or has ended the exchange. */
  get continuationToken(): string | null {
    return this.pendingIntent?.continuationToken ?? null;
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

export function describe(cause: unknown): string {
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
 * The pending intent out of an error body, or null.
 *
 * The shape is the server's — `PendingIntentDto` is generated from its schema
 * and nothing here declares it — but an error body arrives as JSON the SDK does
 * not type, so the fields this client actually branches on are checked before
 * it is read as one. A body missing them is treated as carrying no pending
 * intent at all rather than as a half-read one.
 */
function pendingIntent(value: unknown): PendingIntentDto | null {
  if (!isRecord(value)) return null;
  if (typeof value.requestId !== "string") return null;
  if (typeof value.reasonCode !== "string") return null;
  if (!Array.isArray(value.missingSlots) || !Array.isArray(value.candidates)) {
    return null;
  }
  return value as unknown as PendingIntentDto;
}

function authoredControlDraft(value: unknown): AuthoredControlDraftDto | null {
  if (!isRecord(value)) return null;
  if (typeof value.targetComponentId !== "string") return null;
  return value as unknown as AuthoredControlDraftDto;
}

/**
 * The API's error body, or an honest admission that this was not one.
 *
 * A gateway, a proxy or a crash can answer with HTML or with nothing at all.
 * Those become `TRANSPORT_ERROR` carrying the HTTP status rather than being
 * dressed up as a code the server never issued.
 */
export function toStudioApiError(
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
      outcome: typeof body.outcome === "string" ? body.outcome : null,
      pendingIntent: pendingIntent(body.pendingIntent),
      authoredControlDraft: authoredControlDraft(body.authoredControlDraft),
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

export interface FieldsResult<T> {
  data?: T;
  error?: unknown;
  response?: Response;
}

/** Run one generated SDK call and give back its data, or throw. */
export async function call<T>(
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
