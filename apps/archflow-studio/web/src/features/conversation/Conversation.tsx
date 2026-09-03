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
  CandidateDto,
  CompareDto,
  GestureDto,
  ProjectArtifactDto,
  StateProjectionDto,
  ValidationDto,
} from "../../api/generated";
import type { EvidenceTab } from "../../app/evidence";
import type { Entry } from "../../app/transcript";
import { CandidateCard } from "./cards/CandidateCard";
import { CompareCard } from "./cards/CompareCard";
import { ProposalCard } from "./cards/ProposalCard";
import { QuestionCard } from "./cards/QuestionCard";
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
  /** Put a proposal's compiled sentence back in the composer to edit it. */
  onAdjust(utterance: string): void;
  /** Move a proposal's number by hand; the entry is refined in place. */
  onRefine(entryId: string, value: number): void;
  onJobStatus(candidateId: string, status: string): void;
  onCandidate(candidate: CandidateDto): void;
  onPreview(artifact: ProjectArtifactDto, sourceLabel: string): void;
  onValidation(validation: ValidationDto): void;
  /** Open the drawer on a tab; a card names its own candidate so the drawer shows that run. */
  onEvidence(tab: EvidenceTab, candidateId?: string): void;
  /** Cross-fade a comparison's two exports in the viewer. */
  onCompareInModel(comparison: CompareDto): void;
  /** The sentence a candidate this tab launched was made from, or null. */
  labelOf(candidateId: string): string | null;
}

export function Conversation({
  entries,
  sessionError,
  projection,
  selection,
  disabledReason,
  busy,
  runBusy,
  loadingSha,
  ghostProposalId,
  refiningEntryId,
  gestures,
  onRemoveGesture,
  draft,
  onDraft,
  onSubmit,
  onSelect,
  callbacks,
}: {
  entries: readonly Entry[];
  sessionError: StudioApiError | null;
  projection: StateProjectionDto | null;
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
  draft: string;
  onDraft(text: string): void;
  onSubmit(utterance: string): void;
  onSelect(componentId: string, elementId: string | null): void;
  callbacks: ConversationCallbacks;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);

  useEffect(() => {
    const node = scrollRef.current;
    if (node && followRef.current) node.scrollTop = node.scrollHeight;
  }, [entries]);

  return (
    <section className="chat" aria-label="conversation">
      <header className="chat__head">
        <span className="label">Conversation</span>
        <span className="chat__head-meta mono">this tab · not version history</span>
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
              callbacks,
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
    callbacks,
  }: {
    runBusy: boolean;
    loadingSha: string | null;
    ghostProposalId: string | null;
    refiningEntryId: string | null;
    callbacks: ConversationCallbacks;
  },
): ReactNode {
  switch (entry.kind) {
    case "system":
      return <SystemLine text={entry.text} />;
    case "reading":
      return (
        <ReadingLine
          subject={entry.subject}
          recordSize={entry.recordSize}
          startedAt={entry.startedAt}
        />
      );
    case "you":
      return <p className="bubble">{entry.text}</p>;
    case "proposal":
      return (
        <>
          <p className="msg__who">Studio · proposed change</p>
          <ProposalCard
            proposal={entry.proposal}
            agent={entry.agent}
            ghostShown={entry.proposal.proposalId === ghostProposalId}
            refinements={entry.refinements}
            refining={entry.id === refiningEntryId}
            busy={runBusy}
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
          <p className="msg__who">Studio · needs you</p>
          <QuestionCard error={entry.error} onReply={callbacks.onReply} />
        </>
      );
    case "refusal":
      return (
        <>
          <p className="msg__who">Studio</p>
          <RefusalCard error={entry.error} what={entry.what} />
        </>
      );
    case "candidate":
      return (
        <>
          <p className="msg__who">Studio · candidate</p>
          <CandidateCard
            candidateId={entry.candidateId}
            jobId={entry.jobId}
            loadingSha={loadingSha}
            onJobStatus={callbacks.onJobStatus}
            onCandidate={callbacks.onCandidate}
            onPreview={callbacks.onPreview}
            onEvidence={callbacks.onEvidence}
            labelOf={callbacks.labelOf}
          />
        </>
      );
    case "verdict":
      return (
        <>
          <p className="msg__who">Studio · verdict</p>
          <VerdictCard
            candidateId={entry.candidateId}
            protectedRefs={entry.protectedRefs}
            onValidation={callbacks.onValidation}
            onEvidence={callbacks.onEvidence}
          />
        </>
      );
    case "compare":
      return (
        <>
          <p className="msg__who">Studio · before / after</p>
          <CompareCard
            comparison={entry.comparison}
            onCompareInModel={() => callbacks.onCompareInModel(entry.comparison)}
          />
        </>
      );
  }
}
