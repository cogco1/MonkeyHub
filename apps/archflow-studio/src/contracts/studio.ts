export type CapabilityAvailability = "available" | "reserved" | "disabled";

export interface StudioHealth {
  schema: "StudioHealth@1";
  status: "ok" | "degraded";
  service: string;
  mode: "preview";
  readOnly: true;
  kernelImportable: boolean;
  canonicalWriteAuthority: false;
}

export interface StudioCapability {
  schema: "StudioCapability@1";
  id: string;
  label: string;
  availability: CapabilityAvailability;
  authority: string;
  detail: string;
}

export interface StudioCapabilities {
  schema: "StudioCapabilities@1";
  mode: "preview";
  capabilities: StudioCapability[];
}

export interface StudioSessionSnapshot {
  schema: "StudioSessionSnapshot@1";
  mode: "preview";
  projectId: string | null;
  runId: string | null;
  branchId: string | null;
  stage: 1 | 2 | 3 | 4 | null;
  projectBound: boolean;
  canonicalWriteAuthority: false;
  message: string;
}

export interface StudioGatewaySnapshot {
  health: StudioHealth;
  capabilities: StudioCapabilities;
  session: StudioSessionSnapshot;
}

