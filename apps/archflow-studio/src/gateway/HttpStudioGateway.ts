import type {
  StudioCapabilities,
  StudioGatewaySnapshot,
  StudioHealth,
  StudioSessionSnapshot,
} from "../contracts/studio";
import type { StudioGateway } from "./StudioGateway";

export class StudioGatewayError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "StudioGatewayError";
  }
}

export class HttpStudioGateway implements StudioGateway {
  constructor(private readonly baseUrl = "/api") {}

  health(signal?: AbortSignal): Promise<StudioHealth> {
    return this.get<StudioHealth>("/health", signal);
  }

  capabilities(signal?: AbortSignal): Promise<StudioCapabilities> {
    return this.get<StudioCapabilities>("/capabilities", signal);
  }

  session(signal?: AbortSignal): Promise<StudioSessionSnapshot> {
    return this.get<StudioSessionSnapshot>("/session", signal);
  }

  async snapshot(signal?: AbortSignal): Promise<StudioGatewaySnapshot> {
    const [health, capabilities, session] = await Promise.all([
      this.health(signal),
      this.capabilities(signal),
      this.session(signal),
    ]);
    return { health, capabilities, session };
  }

  private async get<T>(path: string, signal?: AbortSignal): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      headers: { Accept: "application/json" },
      signal,
    });
    if (!response.ok) {
      throw new StudioGatewayError(
        `Studio gateway returned HTTP ${response.status}`,
        response.status,
      );
    }
    return (await response.json()) as T;
  }
}

