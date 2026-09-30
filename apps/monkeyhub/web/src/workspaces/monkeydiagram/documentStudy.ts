import type { DocumentGestureDto, StudySourceRequestDto, StudyViewDto } from "../../api/generated";

export type StudyEvidenceKind = "envelope" | "mass" | "void" | "floor_plate";
export interface StudyEvidence {
  evidenceId: string;
  kind: StudyEvidenceKind;
  status: "proposed" | "confirmed" | "rejected";
  origin: "user" | "machine" | "imported";
  confidence: number;
  geometry: { type: "polygon"; points: [number, number][] };
}
export interface StudyHistoricalSource {
  sourceId: string; citation: string; url: string; locator: string; summary: string;
}
export interface StudyHypothesis {
  hypothesisId: string; statement: string; evidenceIds: string[]; historicalSourceIds: string[];
  counterEvidenceIds?: string[];
  assumptions: string[]; falsification: string; competesWith: string[]; status: "open" | "revised" | "rejected";
}
export interface StudyGap { gapId: string; description: string; evidenceIds: string[] }
export interface StudyCounterfactual {
  counterfactualId: string; hypothesisIds: string[]; targetEvidenceId: string;
  operation: "translate" | "scale" | "remove";
  parameters: { dx?: number; dy?: number; scale?: number };
  conditions: string[]; prediction: string; execute: boolean;
}
export interface StudyCounterfactualActual {
  status: "computed" | "unsupported"; method: string; reason?: string;
  evidence?: StudyEvidence[]; measurements?: Record<string, unknown>[]; relations?: Record<string, unknown>[];
  baselineMeasurements?: Record<string, unknown>[]; baselineRelations?: Record<string, unknown>[];
  interpretation?: "underdetermined";
  removedFacts?: string[]; addedFacts?: string[]; relationSignatureSimilarity?: number;
}
export interface StudyCompositionPattern {
  patternId: string; name: string; rule: string; evidenceIds: string[]; conditions: string[]; exceptions: string[];
}
export interface StudyDesignPrior {
  priorId: string; statement: string; patternId: string; hypothesisIds: string[]; conditions: string[];
  preference: string; preferenceStatus: "unresolved" | "stated";
  changedContext: null | { changedConditions: string[]; decision: "unresolved" | "retain" | "revise" | "reject";
    reason: string; revisedStatement: string };
}
export interface StudyComparisonDefinition { studies: { studyId: string; ledgerRef: string }[] }
export interface StudyResearch {
  question: string; historicalSources: StudyHistoricalSource[]; hypotheses: StudyHypothesis[];
  gaps: StudyGap[]; counterfactuals: StudyCounterfactual[];
  compositionPattern: StudyCompositionPattern | null; designPrior: StudyDesignPrior | null;
  comparisons: StudyComparisonDefinition[];
}
export interface StudyResearchResult extends Omit<StudyResearch, "counterfactuals"> {
  counterfactuals: (StudyCounterfactual & { actual: StudyCounterfactualActual | null })[];
  completion?: { ready: boolean; missing: string[] }; method?: string; sourceBinding?: Record<string, unknown>;
  observations?: { category: "computed"; method: string; metricFrame: string;
    measurements: Record<string, unknown>[]; relations: Record<string, unknown>[] } | null;
  comparisonResults?: StudyComparison[];
}
export interface StudyDraft { studyId: string; evidence: StudyEvidence[]; research: StudyResearch }
export interface StudyComparisonTarget { studyId: string; ledgerRef: string; label: string }
export interface StudyComparison {
  method?: string; metricFrame?: unknown; sharedTopology?: Record<string, unknown>[];
  pairwise?: { left: { studyId: string; ledgerRef: string }; right: { studyId: string; ledgerRef: string };
    topologySimilarity?: number; maxProportionDelta?: number;
    leftOnlyTopology?: { fact: string; count: number }[]; rightOnlyTopology?: { fact: string; count: number }[]; proportionDeltas?: unknown }[];
}

export function createStudyDraft(studyId: string): StudyDraft {
  return { studyId, evidence: [], research: { question: "", historicalSources: [], hypotheses: [], gaps: [],
    counterfactuals: [], compositionPattern: null, designPrior: null, comparisons: [] } };
}

const lines = (values: readonly string[]) => values.map(value => value.trim()).filter(Boolean);

/** Only human-editable inputs return to the API; a browser never supplies computed outcomes. */
export function studyResearchInput(research: StudyResearch): StudyResearch {
  const pattern = research.compositionPattern;
  const prior = research.designPrior;
  return {
    question: research.question,
    historicalSources: research.historicalSources.map(({ sourceId, citation, url, locator, summary }) =>
      ({ sourceId, citation, url, locator, summary })),
    hypotheses: research.hypotheses.map(({ hypothesisId, statement, evidenceIds, counterEvidenceIds, historicalSourceIds, assumptions,
      falsification, competesWith, status }) => ({ hypothesisId, statement, evidenceIds: [...evidenceIds],
      counterEvidenceIds: [...(counterEvidenceIds ?? [])],
      historicalSourceIds: [...historicalSourceIds], assumptions: lines(assumptions), falsification, competesWith: [...competesWith], status })),
    gaps: research.gaps.map(({ gapId, description, evidenceIds }) => ({ gapId, description, evidenceIds: [...evidenceIds] })),
    counterfactuals: research.counterfactuals.map(({ counterfactualId, hypothesisIds, targetEvidenceId, operation,
      parameters, conditions, prediction, execute }) => ({ counterfactualId, hypothesisIds: [...hypothesisIds], targetEvidenceId,
      operation, parameters: operation === "translate" ? { dx: parameters.dx ?? 0, dy: parameters.dy ?? 0 }
        : operation === "scale" ? { scale: parameters.scale ?? 1 } : {}, conditions: lines(conditions), prediction, execute })),
    compositionPattern: pattern ? { patternId: pattern.patternId, name: pattern.name, rule: pattern.rule,
      evidenceIds: [...pattern.evidenceIds], conditions: lines(pattern.conditions), exceptions: lines(pattern.exceptions) } : null,
    designPrior: prior ? { priorId: prior.priorId, statement: prior.statement, patternId: prior.patternId,
      hypothesisIds: [...prior.hypothesisIds], conditions: lines(prior.conditions), preference: prior.preference,
      preferenceStatus: prior.preferenceStatus, changedContext: prior.changedContext ? {
        changedConditions: lines(prior.changedContext.changedConditions), decision: prior.changedContext.decision,
        reason: prior.changedContext.reason, revisedStatement: prior.changedContext.revisedStatement } : null } : null,
    comparisons: (research.comparisons ?? []).map(row => ({ studies: row.studies.map(({ studyId, ledgerRef }) => ({ studyId, ledgerRef })) })),
  };
}

export function studyResearchResult(view: StudyViewDto | null): StudyResearchResult | null {
  const research = (view as (StudyViewDto & { research?: StudyResearchResult | null }) | null)?.research;
  return research ?? null;
}

export function studyDraftFromView(view: StudyViewDto): StudyDraft {
  const blank = createStudyDraft(view.studyId);
  const research = studyResearchResult(view);
  return { studyId: view.studyId, evidence: view.evidence.map(row => ({
    evidenceId: String(row.evidenceId), kind: row.kind as StudyEvidenceKind,
    status: row.status as StudyEvidence["status"], origin: row.origin as StudyEvidence["origin"],
    confidence: Number(row.confidence), geometry: { type: "polygon",
      points: (row.geometry as StudyEvidence["geometry"]).points.map(([x, y]) => [x, y]) },
  })), research: research ? studyResearchInput(research) : blank.research };
}

/** Matching a source uses its complete page identity, including an explicit unversioned upload. */
export function studyMatchesSource(view: StudyViewDto, source: StudySourceRequestDto): boolean {
  return view.source.runId === source.runId && view.source.assetSha256 === source.assetSha256
    && (view.source.revisionRef ?? null) === (source.revisionRef ?? null) && view.source.pageIndex === source.pageIndex;
}

/** Drawing uses the existing page canvas; these gestures are not document annotations. */
export function studyEvidenceToGestures(evidence: readonly StudyEvidence[]): DocumentGestureDto[] {
  return evidence.map(item => ({ id: item.evidenceId, kind: "polyline", closed: true,
    points: item.geometry.points.map(([x, y]) => [x, y]),
    color: item.status === "confirmed" ? "#18725c" : item.status === "rejected" ? "#8b6470" : "#95610d",
    lineWidth: item.status === "rejected" ? 0.002 : 0.004, label: `${item.kind} · ${item.status}` }));
}

export function studyTraceIsClosed(mark: DocumentGestureDto): boolean {
  return mark.kind === "polyline" && mark.closed === true && mark.points.length >= 3;
}

/** A corrected contour needs confirmation again. Erased traces remain as rejected evidence. */
export function studyEvidenceFromGestures(gestures: readonly DocumentGestureDto[], previous: readonly StudyEvidence[]): StudyEvidence[] {
  const byId = new Map(previous.map(item => [item.evidenceId, item]));
  const present = new Set(gestures.map(mark => mark.id));
  const rows = gestures.filter(studyTraceIsClosed).map(mark => {
    const before = byId.get(mark.id);
    const points = mark.points.map(([x, y]): [number, number] => [x, y]);
    return { evidenceId: mark.id, kind: before?.kind ?? "mass" as const, status: before
      && JSON.stringify(before.geometry.points) === JSON.stringify(points) ? before.status : "proposed" as const,
    origin: before?.origin ?? "user" as const, confidence: before?.confidence ?? 1,
    geometry: { type: "polygon" as const, points } };
  });
  const closed = new Set(rows.map(item => item.evidenceId));
  return [...rows, ...previous.filter(item => !closed.has(item.evidenceId)).map(item => ({ ...item,
    status: present.has(item.evidenceId) ? "proposed" as const : "rejected" as const }))];
}

export function addStudyTrace(draft: StudyDraft, trace: DocumentGestureDto): StudyDraft {
  if (!studyTraceIsClosed(trace)) return draft;
  return { ...draft, evidence: studyEvidenceFromGestures([
    ...studyEvidenceToGestures(draft.evidence.filter(item => item.evidenceId !== trace.id)), trace,
  ], draft.evidence) };
}

export function newStudyItemId(prefix: string): string { return `${prefix}-${crypto.randomUUID()}`; }

/** Find retained results by their exact inputs, never by a draft's mutable list position. */
export function studyComparisonDefinitionKey(definition: StudyComparisonDefinition): string {
  return JSON.stringify(definition.studies.map(({ studyId, ledgerRef }) => [studyId, ledgerRef]));
}

export function studyDraftChanged(draft: StudyDraft, view: StudyViewDto | null): boolean {
  return view === null || JSON.stringify({ ...draft, research: studyResearchInput(draft.research) })
    !== JSON.stringify(studyDraftFromView(view));
}
