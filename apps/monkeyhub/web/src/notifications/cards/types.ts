/**
 * The attention queue's cards as the Hub serves them (`/api/attention*`, `monkeyhub_api.attention.models`,
 * #679 and #681). Written by hand until the Hub's generated client is made again with these routes (after
 * 0-2); then these types come from `api/generated` and this file goes.
 */

export type AttentionChannel = "decide" | "annotate" | "guess" | "notice";
export type CardKind = "ab" | "choice" | "plan" | "review_frame" | "change_list" | "ask" | "suggestion" | "auto_fixed" | "info";
export type CardState = "open" | "review" | "later" | "working" | "done" | "dismissed" | "superseded" | "expired";
export type ArgValue = string | number | boolean | null | string[];

/** One command of the command tree and the arguments it runs with. */
export interface CommandRef {
  commandId: string;
  args: Record<string, ArgValue>;
}

export interface CardOption {
  id: string;
  label: string;
  labelZh?: string | null;
  consequence: string;
  consequenceZh?: string | null;
  action: CommandRef;
  undoable?: boolean;
  recommended?: boolean;
}

export interface CardSource {
  runIds?: string[];
  studyId?: string | null;
  chatSessionId?: string | null;
  frameId?: string | null;
}

export interface CardMedia {
  kind: "model_view" | "board_frame" | "crop";
  runId?: string | null;
  view?: string | null;
  frameId?: string | null;
  caption?: string | null;
  captionZh?: string | null;
}

export interface EvidenceRow {
  key: string;
  label: string;
  labelZh?: string | null;
  text: string;
  textZh?: string | null;
  status: "ok" | "warn" | "unknown";
}

export interface Recommendation {
  optionId: string;
  reason: string;
  reasonZh?: string | null;
}

export interface Resolution {
  optionId?: string | null;
  by: "person" | "machine" | "bulk";
  at: string;
  undo?: { action: CommandRef } | null;
}

export interface AttentionCard {
  id: string;
  projectId: string;
  projectDir: string;
  channel: AttentionChannel;
  kind: CardKind;
  state: CardState;
  title: string;
  titleZh?: string | null;
  summary?: string | null;
  summaryZh?: string | null;
  source: CardSource;
  media: CardMedia[];
  evidence: EvidenceRow[];
  options: CardOption[];
  opens: CommandRef[];
  recommendation?: Recommendation | null;
  resolution?: Resolution | null;
  snoozedUntil?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ProjectCount {
  projectId: string;
  projectDir: string;
  open: number;
  later: number;
  working: number;
}

export interface AttentionSummary {
  projects: ProjectCount[];
  hub?: ProjectCount | null;
  open: number;
  later: number;
  working: number;
}

/** "All as recommended" over the cards in view, and taking it back: what it handled, and what it left with the reason. */
export interface BulkResult {
  bulkId: string;
  items: AttentionCard[];
  skipped: { id: string; code: string; detail: string }[];
}

/** What answering, or taking an answer back, returns: the card, and a new one when the action asked instead of guessing. */
export interface CardActionResult {
  item: AttentionCard;
  followUp?: AttentionCard | null;
}
