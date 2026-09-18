/** A project runtime connection with its own SDK configuration and identity. */

import { createClient, type Client } from "./generated/client";
import { readProtocolApiProtocolGet } from "./generated";
import type { ServerIdentityDto } from "./generated";
import { StudioApiError, call } from "./error";

/** The protocol major this client speaks. `archflow/2`. */
export const PROTOCOL_MAJOR = 2;

/** A server this client will not talk to, because it does not speak its major. */
export const PROTOCOL_MISMATCH = "PROTOCOL_MISMATCH";

/** A server that does not answer the handshake at all. */
export const PROTOCOL_UNSUPPORTED = "PROTOCOL_UNSUPPORTED";

/** What the server said it is. The generated shape, named for what it means. */
export type ServerIdentity = ServerIdentityDto;

/**
 * The refusal to speak to a server, as the same error every panel renders.
 *
 * `identity` is the server's own answer when there was one — a mismatch has it
 * and can print the major it offered; a server with no `/api/protocol` at all
 * has none.
 */
export class ProtocolRefusal extends StudioApiError {
  readonly identity: ServerIdentity | null;

  constructor(init: {
    code: string;
    detail: string;
    status?: number;
    identity?: ServerIdentity | null;
  }) {
    super({ status: init.status ?? 0, code: init.code, detail: init.detail });
    this.name = "ProtocolRefusal";
    this.identity = init.identity ?? null;
  }
}

/** `archflow/2` → 2. `null` when the string is not a protocol identity. */
export function protocolMajor(protocol: string): number | null {
  const match = /^archflow\/(\d+)$/.exec(protocol.trim());
  if (match === null) return null;
  const major = Number(match[1]);
  return Number.isInteger(major) ? major : null;
}

/**
 * Where the API is.
 *
 * The host supplies an API origin or a Hub project forwarding prefix.
 * Generated paths already begin with `/api`, so a trailing `/` or `/api`
 * is dropped. An empty string addresses this origin.
 */
export function readBaseUrl(configured: string | undefined): string {
  const raw = (configured ?? "").trim();
  if (raw === "") return "";
  const withoutSlash = raw.replace(/\/+$/, "");
  return withoutSlash.replace(/\/api$/, "");
}

/** A key belongs to one submitted request, never to the parent's diagnostic trace. */
async function operationFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = new Request(input, init);
  const target = new URL(request.url);
  const forwarding = target.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(target.hostname)
    && /^\/api\/runtime\/projects\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/studio\/api(?:\/|$)/i.test(target.pathname);
  if (forwarding && request.method !== "GET" && request.method !== "HEAD" && !request.headers.has("Idempotency-Key")) {
    request.headers.set("Idempotency-Key", crypto.randomUUID());
  }
  return globalThis.fetch(request);
}

/** One server, and everything this client needs in order to talk to it. */
export class ServerConnection {
  readonly baseUrl: string;
  readonly client: Client;
  private readonly token: string | null;
  private identity: ServerIdentity | null = null;

  constructor(baseUrl: string, token: string | null = null) {
    this.baseUrl = readBaseUrl(baseUrl);
    this.token = token;
    this.client = createClient({
      baseUrl: this.baseUrl,
      fetch: operationFetch,
      ...(token === null ? {} : { headers: { Authorization: `Bearer ${token}` } }),
    });
  }

  /** A project-bound API URL for browser APIs such as `EventSource`. */
  url(path: string): string {
    return `${this.baseUrl}${path}`;
  }

  /** Whether a token was supplied for this connection. Never the token itself. */
  get authenticated(): boolean {
    return this.token !== null;
  }

  /** What the server said it is, or null before `probe()` has answered. */
  get server(): ServerIdentity | null {
    return this.identity;
  }

  /**
   * The handshake. Answers the server's identity, or throws.
   *
   * Three refusals, and each says which one it is: the server has no
   * `/api/protocol` (it is not one of these servers), it answered something
   * that is not a protocol identity, or it speaks a major this client does
   * not. Anything else — unreachable, 401, a proxy's HTML — arrives as the
   * ordinary `StudioApiError` it already is.
   */
  async probe(): Promise<ServerIdentity> {
    let identity: ServerIdentity;
    try {
      identity = await call("GET /api/protocol", readProtocolApiProtocolGet({ client: this.client }));
    } catch (cause) {
      if (cause instanceof StudioApiError && (cause.status === 404 || cause.status === 405)) {
        throw new ProtocolRefusal({
          code: PROTOCOL_UNSUPPORTED,
          status: cause.status,
          detail:
            `${this.where()} answered HTTP ${cause.status} for /api/protocol, ` +
            "so it does not serve the open ArchFlow protocol. This client " +
            `speaks archflow/${PROTOCOL_MAJOR} and asks that route first.`,
        });
      }
      throw cause;
    }
    const major = protocolMajor(identity.protocol);
    if (major === null) {
      throw new ProtocolRefusal({
        code: PROTOCOL_UNSUPPORTED,
        detail:
          `${this.where()} named its protocol "${identity.protocol}", which ` +
          "is not an ArchFlow protocol identity (archflow/<major>).",
        identity,
      });
    }
    if (major !== PROTOCOL_MAJOR) {
      throw new ProtocolRefusal({
        code: PROTOCOL_MISMATCH,
        detail:
          `${this.where()} speaks ${identity.protocol}; this client speaks ` +
          `archflow/${PROTOCOL_MAJOR}. A major version is where resources ` +
          "change meaning, so this client refuses rather than report answers " +
          "it may have misread. Use a client built for " +
          `${identity.protocol}, or a server that serves archflow/${PROTOCOL_MAJOR}.`,
        identity,
      });
    }
    this.identity = identity;
    return identity;
  }

  /** How a refusal names the server: its URL, or "the server behind this page". */
  private where(): string {
    return this.baseUrl === "" ? "the server behind this page" : this.baseUrl;
  }
}

/** One line for the honesty tab: which server answered, and in which mode. */
export function connectionLine(identity: ServerIdentity, baseUrl: string): string {
  const where = baseUrl === "" ? "this origin" : baseUrl;
  return (
    `connected to ${identity.server} ${identity.serverVersion} · ` +
    `${identity.mode} · ${identity.protocol} · ${where}`
  );
}
