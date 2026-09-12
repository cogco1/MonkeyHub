export const HOST_MESSAGE_SOURCE: "archflow-studio";
export const START_MODELING: "start-modeling";
export function hostOrigin(search?: string): string | null;
export function requestStartModeling(origin?: string | null): boolean;
export function readHostRequest(
  event: MessageEvent,
  expected: { origin: string; window?: Window | null } | null,
): typeof START_MODELING | null;
