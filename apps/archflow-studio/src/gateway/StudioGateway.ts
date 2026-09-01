import type {
  StudioCapabilities,
  StudioGatewaySnapshot,
  StudioHealth,
  StudioSessionSnapshot,
} from "../contracts/studio";

export interface StudioGateway {
  health(signal?: AbortSignal): Promise<StudioHealth>;
  capabilities(signal?: AbortSignal): Promise<StudioCapabilities>;
  session(signal?: AbortSignal): Promise<StudioSessionSnapshot>;
  snapshot(signal?: AbortSignal): Promise<StudioGatewaySnapshot>;
}

