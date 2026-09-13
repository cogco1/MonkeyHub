/**
 * Which server this client talks to, and whether it may.
 *
 * Until now the answer was "the one behind this page": every request went to a
 * same-origin `/api`, which the Vite dev proxy forwards to the API started
 * beside it. That is still the default and nothing about it changes. What this
 * module adds is the seam a second client — a desktop shell, another web
 * client, this one pointed at a remote server — arrives through:
 *
 *  - **a base URL**, `VITE_ARCHFLOW_API_URL` at build time, else same origin;
 *  - **a token**, optional, in memory only. A `?token=` on the first load is
 *    read once and removed from the address bar, so it is not left in history,
 *    in a bookmark or in a `Referer`. It is never written to storage.
 *  - **`probe()`**, the handshake: `GET /api/protocol` before anything else.
 *    A server whose protocol *major* is not this client's is refused with a
 *    typed error, because a client that guessed at a major it does not speak
 *    would report the server's answers as if it had understood them.
 *
 * The generated SDK's client is configured from here, once, at module load —
 * so every call in `client.ts` goes to this base URL and carries this token
 * without repeating either.
 */

import { client } from "./generated/client.gen";
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
 * Empty string means "this origin", which is what the dev proxy and a
 * co-served build both want: the generated paths already begin with `/api`.
 * A configured URL is the server's *origin*; a trailing `/` or `/api` is
 * dropped so the one prefix is not sent twice.
 */
export function readBaseUrl(configured: string | undefined): string {
  const raw = (configured ?? "").trim();
  if (raw === "") return "";
  const withoutSlash = raw.replace(/\/+$/, "");
  return withoutSlash.replace(/\/api$/, "");
}

/** An embedded Studio may use only the loopback Hub that actually framed it. */
export function embeddedHubBaseUrl(href: string, referrer: string): string | null {
  try {
    const page = new URL(href);
    if (page.searchParams.get("embedded") !== "tool") return null;
    const raw = page.searchParams.get("hubApi");
    if (!raw) return null;
    const hub = new URL(raw), parent = new URL(referrer);
    if (hub.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(hub.hostname)
      || hub.origin !== parent.origin || hub.username || hub.password || hub.search || hub.hash
      || !/^\/api\/runtime\/projects\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/studio$/i.test(hub.pathname)) return null;
    return hub.href;
  } catch { return null; }
}

/** A key belongs to one submitted request, never to the parent's diagnostic trace. */
async function operationFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = new Request(input, init);
  if (request.method !== "GET" && request.method !== "HEAD" && !request.headers.has("Idempotency-Key")) {
    request.headers.set("Idempotency-Key", crypto.randomUUID());
  }
  return globalThis.fetch(request);
}

/**
 * The token in `?token=`, taken once and wiped from the address bar.
 *
 * A token in a URL is a token in the history, in the next `Referer` and in
 * whatever the user pastes to a colleague. Reading it here and replacing the
 * entry means the page holds it and the address bar does not.
 */
export function claimTokenFromLocation(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const url = new URL(window.location.href);
    const token = url.searchParams.get("token");
    if (token === null || token.trim() === "") return null;
    url.searchParams.delete("token");
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
    return token.trim();
  } catch {
    // A browser that refuses `replaceState` (a sandboxed frame) still gets a
    // working client; it simply keeps the token visible, which is the
    // browser's decision and not this client's.
    return null;
  }
}

/** One server, and everything this client needs in order to talk to it. */
export class ServerConnection {
  readonly baseUrl: string;
  private token: string | null;
  private identity: ServerIdentity | null = null;

  constructor(baseUrl: string, token: string | null = null) {
    this.baseUrl = baseUrl;
    this.token = token;
  }

  /** The absolute URL of one API path — for `EventSource`, which takes no client. */
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

  /** Point the generated SDK at this server. Called once, at module load. */
  configure(): void {
    client.setConfig({
      baseUrl: this.baseUrl,
      fetch: operationFetch,
      ...(this.token === null
        ? {}
        : { headers: { Authorization: `Bearer ${this.token}` } }),
    });
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
      identity = await call("GET /api/protocol", readProtocolApiProtocolGet());
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

/** The one connection this app uses, configured before any call is made. */
export const connection = new ServerConnection(
  (typeof window === "undefined" || typeof document === "undefined" ? null
    : embeddedHubBaseUrl(window.location.href, document.referrer)) ?? readBaseUrl(import.meta.env.VITE_ARCHFLOW_API_URL),
  claimTokenFromLocation(),
);

connection.configure();

/** One line for the honesty tab: which server answered, and in which mode. */
export function connectionLine(identity: ServerIdentity, baseUrl: string): string {
  const where = baseUrl === "" ? "this origin" : baseUrl;
  return (
    `connected to ${identity.server} ${identity.serverVersion} · ` +
    `${identity.mode} · ${identity.protocol} · ${where}`
  );
}
