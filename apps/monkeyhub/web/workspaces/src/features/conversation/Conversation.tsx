/**
 * The conversation column: what this tab said and what the server answered,
 * in order, with the composer under it.
 *
 * Every entry is rendered by its kind and nothing else is inferred from it. The
 * list follows the newest entry unless the reader has scrolled up to read an
 * older one, in which case it stays where they are.
 */

import { useEffect, useRef, type ReactNode } from "react";

import type { StudioApiError } from "../../api/client";
import type {
  CompareDto,
  GestureDto,
  ProjectArtifactDto,
  StateProjectionDto,
} from "../../api/generated";
import type { EvidenceTab } from "../../app/evidence";
import type { Entry } from "../../app/transcript";
import { EMPTY_CANDIDATE_READBACK, type CandidateReadback } from "../../app/useCandidateRuns";
import { useT, type TFunction } from "../../i18n/useT";
import { usePreferences } from "../settings/preferences";
import { CandidateCard } from "./cards/CandidateCard";
import { CompareCard } from "./cards/CompareCard";
import { MissingControlCard } from "./cards/MissingControlCard";
import { ProposalCard } from "./cards/ProposalCard";
import { QuestionCard, type Choice } from "./cards/QuestionCard";
import { ReadingLine } from "./cards/ReadingLine";
import { RefusalCard } from "./cards/RefusalCard";
import { SystemLine } from "./cards/Verbatim";
import { VerdictCard } from "./cards/VerdictCard";
import { Composer, type Selection } from "./Composer";

/** How far from the bottom still counts as "following the newest entry". */
const FOLLOW_SLOP_PX = 40;

export interface ConversationCallbacks {
  onRun(proposalId: string): void;
  onReply(text: string): void;
  /**
   * Answer a question with one of the server's own candidates: the selection
   * moves and the pending intent is continued in one step, so the target the
   * next request is about is never the stale one.
   */
  onChoose(choice: Choice): void;
  /** Put a proposal's compiled sentence back in the composer to edit it. */
  onAdjust(utterance: string): void;
  /** Move a proposal's number by hand; the entry is refined in place. */
  onRefine(entryId: string, value: number): void;
  onRetryCandidate(candidateId: string): void;
  onPreview(artifact: ProjectArtifactDto, sourceLabel: string): void;
  /** Open the drawer on a tab; a card names its own candidate so the drawer shows that run. */
  onEvidence(tab: EvidenceTab, candidateId?: string): void;
  /** Cross-fade a comparison's two exports in the viewer. */
  onCompareInModel(comparison: CompareDto): void;
  /** The sentence a candidate this tab launched was made from, or null. */
  labelOf(candidateId: string): string | null;
}

export function Conversation({
  entries,
  candidateRuns,
  sessionError,
  projection,
  currentStateDigest,
  selection,
  disabledReason,
  busy,
  runBusy,
  loadingSha,
  ghostProposalId,
  refiningEntryId,
  gestures,
  onRemoveGesture,
  intentProvider,
  draft,
  onDraft,
  onSubmit,
  onSelect,
  callbacks,
  onClose,
}: {
  entries: readonly Entry[];
  candidateRuns: Readonly<Record<string, CandidateReadback>>;
  sessionError: StudioApiError | null;
  projection: StateProjectionDto | null;
  currentStateDigest: string | null;
  selection: Selection | null;
  disabledReason: string | null;
  busy: boolean;
  runBusy: boolean;
  loadingSha: string | null;
  /** The proposal currently drawn as a ghost, if any. */
  ghostProposalId: string | null;
  /** The proposal entry whose refinement is on the wire, if any. */
  refiningEntryId: string | null;
  /** Marks drawn on the model, to go with the next sentence. */
  gestures: readonly GestureDto[];
  onRemoveGesture(index: number): void;
  /** Who reads sentences in this process, as GET /api/project said; null before the binding. */
  intentProvider: string | null;
  draft: string;
  onDraft(text: string): void;
  onSubmit(utterance: string): void;
  onSelect(componentId: string, elementId: string | null): void;
  callbacks: ConversationCallbacks;
  onClose?: () => void;
}) {
  const t = useT();
  const { developerMode } = usePreferences();
  const scrollRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);

  useEffect(() => {
    const node = scrollRef.current;
    if (node && followRef.current) node.scrollTop = node.scrollHeight;
  }, [entries]);

  return (
    <section
      id="conversation-panel"
      className="chat"
      aria-label={t("conversation.ariaLabel")}
    >
      <header className="chat__head">
        <span className="label">{t("conversation.title")}</span>
        {developerMode && <span className="chat__head-meta mono">{t("conversation.scope")}</span>}
        {onClose && <button type="button" className="btn btn--small" onClick={onClose} aria-label={t("common.close")}>×</button>}
      </header>
      <div
        ref={scrollRef}
        className="chat__scroll"
        onScroll={(event) => {
          const node = event.currentTarget;
          followRef.current =
            node.scrollHeight - node.scrollTop - node.clientHeight <
            FOLLOW_SLOP_PX;
        }}
      >
        {sessionError && (
          <RefusalCard
            error={sessionError}
            what="GET /api/project · GET /api/state"
          />
        )}
        {entries.map((entry) => (
          <div key={entry.id} className={`msg msg--${entry.kind}`}>
            {renderEntry(entry, {
              runBusy,
              loadingSha,
              ghostProposalId,
              refiningEntryId,
              candidateRuns,
              callbacks,
              currentStateDigest,
              t,
              developerMode,
            })}
          </div>
        ))}
      </div>
      <Composer
        selection={selection}
        projection={projection}
        disabledReason={disabledReason}
        busy={busy}
        gestures={gestures}
        onRemoveGesture={onRemoveGesture}
        intentProvider={intentProvider}
        draft={draft}
        onDraft={onDraft}
        onSubmit={onSubmit}
        onSelect={onSelect}
      />
    </section>
  );
}

function renderEntry(
  entry: Entry,
  {
    runBusy,
    loadingSha,
    ghostProposalId,
    refiningEntryId,
    candidateRuns,
    callbacks,
    currentStateDigest,
    t,
    developerMode,
  }: {
    runBusy: boolean;
    loadingSha: string | null;
    ghostProposalId: string | null;
    refiningEntryId: string | null;
    candidateRuns: Readonly<Record<string, CandidateReadback>>;
    callbacks: ConversationCallbacks;
  onClose?: () => void;
    currentStateDigest: string | null;
    t: TFunction;
    developerMode: boolean;
  },
): ReactNode {
  switch (entry.kind) {
    case "system":
      return developerMode ? <SystemLine text={entry.text} parts={entry.parts} /> : null;
    case "reading":
      return (
        <ReadingLine
          subject={entry.subject}
          recordSize={entry.recordSize}
          provider={entry.provider}
          startedAt={entry.startedAt}
        />
      );
    case "you":
      return <p className="bubble">{entry.text}</p>;
    case "proposal":
      return (
        <>
          <p className="msg__who">{t("conversation.who.proposed")}</p>
          <ProposalCard
            proposal={entry.proposal}
            agent={entry.agent}
            ghostShown={entry.proposal.proposalId === ghostProposalId}
            refinements={entry.refinements}
            refining={entry.id === refiningEntryId}
            busy={runBusy}
            inactive={entry.proposal.baseStateDigest !== currentStateDigest}
            onRun={() => callbacks.onRun(entry.proposal.proposalId)}
            onAdjust={callbacks.onAdjust}
            onRefine={(value) => callbacks.onRefine(entry.id, value)}
            onEvidence={callbacks.onEvidence}
          />
        </>
      );
    case "question":
      return (
        <>
          <p className="msg__who">{t("conversation.who.needsYou")}</p>
          <QuestionCard
            error={entry.error}
            onReply={callbacks.onReply}
            onChoose={callbacks.onChoose}
          />
        </>
      );
    case "terminal":
      // The exchange ended. This card asks nothing, so there is nobody it is
      // waiting on: it is the studio saying what it is missing.
      return (
        <>
          <p className="msg__who">{t("conversation.who.studio")}</p>
          <MissingControlCard error={entry.error} />
        </>
      );
    case "refusal":
      return (
        <>
          <p className="msg__who">{t("conversation.who.studio")}</p>
          <RefusalCard error={entry.error} what={entry.what} />
        </>
      );
    case "candidate":
      return (
        <>
          <p className="msg__who">{t("conversation.who.candidate")}</p>
          <CandidateCard
            candidateId={entry.candidateId}
            jobId={entry.jobId}
            readback={candidateRuns[entry.candidateId] ?? EMPTY_CANDIDATE_READBACK}
            loadingSha={loadingSha}
            onRetry={() => callbacks.onRetryCandidate(entry.candidateId)}
            onPreview={callbacks.onPreview}
            onEvidence={callbacks.onEvidence}
            labelOf={callbacks.labelOf}
          />
        </>
      );
    case "verdict":
      return (
        <>
          <p className="msg__who">{t("conversation.who.verdict")}</p>
          <VerdictCard
            candidateId={entry.candidateId}
            protectedRefs={entry.protectedRefs}
            validation={(candidateRuns[entry.candidateId] ?? EMPTY_CANDIDATE_READBACK).validation}
            onRetry={() => callbacks.onRetryCandidate(entry.candidateId)}
            onEvidence={callbacks.onEvidence}
          />
        </>
      );
    case "compare":
      return (
        <>
          <p className="msg__who">{t("conversation.who.compare")}</p>
          <CompareCard
            comparison={entry.comparison}
            onCompareInModel={() => callbacks.onCompareInModel(entry.comparison)}
          />
        </>
      );
  }
}
