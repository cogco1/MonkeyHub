import { createContext } from "react";
import type { DocumentTracingSourceDto, FrameLevelDto } from "../../api/generated";

/** The page editor hands exact saved ink to the existing application candidate flow. */
export const DocumentTracingContext = createContext<{
  blockedReason: string | null;
  levels: readonly FrameLevelDto[];
  generate(source: DocumentTracingSourceDto, height: number, baseLevel: string): Promise<void>;
  showModel(): void;
} | null>(null);
