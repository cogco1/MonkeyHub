/**
 * Future Studio integration seams. None is implemented by the first preview
 * slice, and none grants canonical write or validator override authority.
 */

export interface IntentProposalRequest {
  sessionRef: string;
  message: string;
  contextRefs: readonly string[];
}

export interface IntentProvider {
  propose(request: IntentProposalRequest, signal?: AbortSignal): Promise<unknown>;
}

export interface RetrievalRequest {
  query: string;
  stage: 1 | 2 | 3 | 4;
  limit: number;
}

export interface RetrievalProvider {
  retrieve(request: RetrievalRequest, signal?: AbortSignal): Promise<readonly unknown[]>;
}

export interface PreviewRequest {
  compiledProgram: unknown;
  expectedBase: unknown;
}

export interface PreviewBackend {
  preview(request: PreviewRequest, signal?: AbortSignal): Promise<unknown>;
}

export interface ViewerAssetProvider {
  resolve(artifactRef: string, signal?: AbortSignal): Promise<Blob>;
}

export interface HumanReviewPort {
  recordReview(
    proposalRef: string,
    disposition: "approve" | "reject" | "revise",
    signal?: AbortSignal,
  ): Promise<unknown>;
}

export interface StudioEventStream {
  subscribe(sessionRef: string, signal?: AbortSignal): AsyncIterable<unknown>;
}

