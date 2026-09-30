import { createContext } from "react";
import type { DocumentTracingSourceDto, FrameLevelDto } from "../../api/generated";

/** The page editor hands exact saved ink to the existing application candidate flow. */
export const DocumentTracingContext = createContext<{
  blockedReason: string | null;
  /**
   * The explicit Continue from the model on screen, offered beside the reason when
   * tracing is blocked only because that model is being viewed; null otherwise.
   */
  continueViewed: (() => Promise<void>) | null;
  /**
   * Record edits and continue (#302), offered beside the reason when tracing is
   * blocked only because the model has unrecorded edits; null otherwise.
   */
  recordEdits: (() => Promise<void>) | null;
  levels: readonly FrameLevelDto[];
  generate(source: DocumentTracingSourceDto, height: number, baseLevel: string): Promise<void>;
  showModel(): void;
} | null>(null);
