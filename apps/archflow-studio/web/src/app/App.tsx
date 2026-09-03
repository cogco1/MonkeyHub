/**
 * The studio shell: one conversation beside one model, and everything the
 * server said one click behind.
 *
 * What this file does is hold the few pieces of state the surfaces share — the
 * transcript, the selection, the model in the viewer, the candidates this tab
 * launched — and pass every question to the API. What it deliberately does
 * not do:
 *
 *  - it imports nothing from archflow and computes no geometry;
 *  - it derives no impact, no relation counts and no advance verdict;
 *  - it writes nothing to the project or to HEAD;
 *  - it keeps no version history: reloading the tab loses the view, not the
 *    work.
 *
 * Every failed call ends in a card showing the server's code and detail. The
 * one error handled rather than merely displayed is `STALE_BASE`: the project
 * moved, so the projection is read again and the fact is said in the
 * transcript.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { asStudioApiError, studio, type StudioApiError } from "../api/client";
import type {
  ArtifactListDto,
  CandidateDto,
  ProjectArtifactDto,
  ProposalDto,
  StateProjectionDto,
  ValidationDto,
} from "../api/generated";
import {
  canonicalSourceLabel,
  candidateSourceLabel,
  receiptDocumentStrings,
} from "../features/artifacts/artifactLabels";
import { Conversation } from "../features/conversation/Conversation";
import type { Selection } from "../features/conversation/Composer";
import { EvidenceDrawer } from "../features/evidence/EvidenceDrawer";
import { honestyCount } from "../features/evidence/HonestyTab";
import type { ViewState } from "../features/stage/SourceChip";
import { Stage, type PickedFacts } from "../features/stage/Stage";
import type { VersionCard } from "../features/stage/VersionsStrip";
import type { SceneInspection } from "../viewer/sceneInspection";
import {
  LOCAL_SOURCE_LABEL,
  type GhostSpec,
  type GhostTarget,
  type ViewportController,
  type ViewportPick,
  type ViewportStatus,
} from "../viewer/ThreeDmViewport";
import { AppShell } from "./AppShell";
import { EVIDENCE_PINNED_KEY, type EvidenceTab } from "./evidence";
import { sha8 } from "./format";
import { failed, idle, loading, ready, type Loadable } from "./loadable";
import { useSession } from "./useSession";
import { useTranscript } from "./transcript";

const BLOCKED = "BLOCKED_NEEDS_HUMAN";

function readPinned(): boolean {
  try {
    return window.localStorage.getItem(EVIDENCE_PINNED_KEY) === "true";
  } catch {
    return false;
  }
}

function writePinned(pinned: boolean): void {
  try {
    window.localStorage.setItem(EVIDENCE_PINNED_KEY, String(pinned));
  } catch {
    // a browser that refuses site data keeps the default; nothing to say
  }
}

/** The element as the projection names it, for the viewer to find its objects. */
function ghostTarget(
  projection: StateProjectionDto,
  elementId: string,
): GhostTarget | null {
  const element = projection.elements.find((row) => row.elementId === elementId);
  return element ? { elementId, componentId: element.componentId } : null;
}

/**
 * The ghost a proposal is drawn as: the target's objects, stretched along Z
 * when the field is a height, with the closure's elements as a faint cloud.
 * Nothing here is a claim about geometry — it is the proposal's two numbers
 * and the record's element ids, drawn approximately and labelled so.
 */
function ghostSpecFor(
  projection: StateProjectionDto,
  proposal: ProposalDto,
): GhostSpec | null {
  if (proposal.target.elementId === null) return null;
  const target = ghostTarget(projection, proposal.target.elementId);
  if (target === null) return null;
  const isHeight = proposal.target.key === "height";
  const old = Number(proposal.change.old);
  const next = Number(proposal.change.new);
  const factor = isHeight && old > 0 && Number.isFinite(next) ? next / old : 1;
  const affected = proposal.impact.propagated
    .filter((ref) => ref.startsWith("entity:"))
    .map((ref) => ghostTarget(projection, ref.slice("entity:".length)))
    .filter((item): item is GhostTarget => item !== null);
  return { target, factor, scaleAxis: isHeight ? "z" : null, affected };
}

export default function App() {
  const transcript = useTranscript();
  const { append, noteJobStatus: noteTranscriptStatus } = transcript;
  const pushNotice = useCallback(
    (line: string) => {
      append({ kind: "system", text: line });
    },
    [append],
  );
  const { session, stateDigest, recoverFromStaleBase } = useSession(pushNotice);

  const [selection, setSelection] = useState<Selection | null>(null);
  const [picked, setPicked] = useState<PickedFacts | null>(null);
  const [draft, setDraft] = useState("");

  const [artifacts, setArtifacts] = useState<Loadable<ArtifactListDto>>(idle);
  const [artifactLoadingSha, setArtifactLoadingSha] = useState<string | null>(
    null,
  );
  const [artifactError, setArtifactError] = useState<StudioApiError | null>(
    null,
  );

  const viewportRef = useRef<ViewportController>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [inspection, setInspection] = useState<SceneInspection | null>(null);
  const [viewerMessage, setViewerMessage] = useState("");
  const [viewerStatus, setViewerStatus] = useState<ViewportStatus>("idle");
  const [sourceLabel, setSourceLabel] = useState<string | null>(null);
  // The listing row of the artifact currently in the viewer, kept beside its
  // source label. It is what the loaded file can be asked about: the bytes
  // carry document strings the loader does not surface, and the receipt that
  // certified those bytes does.
  const [loadedArtifact, setLoadedArtifact] =
    useState<ProjectArtifactDto | null>(null);
  // The artifact whose bytes were handed to the viewer and which the viewer
  // has not yet accepted. Committed only when the viewer reports the label,
  // because a refused file leaves the previous model on screen.
  const pendingArtifact = useRef<ProjectArtifactDto | null>(null);

  const [proposalBusy, setProposalBusy] = useState(false);
  const [candidateBusy, setCandidateBusy] = useState(false);
  // The proposal drawn as a ghost over the loaded model, if any. The viewer
  // drops the drawing itself whenever a model loads or is cleared; this is the
  // shell's record of which proposal the drawing was of.
  const [ghostProposalId, setGhostProposalId] = useState<string | null>(null);
  // Refinements on the wire, one per proposal entry. A move while one is in
  // flight is kept as the value to send next; only the last one is sent.
  const [refiningEntryId, setRefiningEntryId] = useState<string | null>(null);
  const refineInFlight = useRef<string | null>(null);
  const refinePending = useRef<Map<string, number>>(new Map());
  const [candidates, setCandidates] = useState<Record<string, CandidateDto>>({});
  const [validations, setValidations] = useState<
    Record<string, ValidationDto>
  >({});
  // Which candidates already have a verdict entry; a ref so the job reporter
  // never reads a stale transcript.
  const verdictsRef = useRef<Set<string>>(new Set());

  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [evidencePinned, setEvidencePinned] = useState(readPinned);
  const [evidenceTab, setEvidenceTab] = useState<EvidenceTab>("honesty");
  const [eventCount, setEventCount] = useState(0);

  const projection: StateProjectionDto | null =
    session.status === "ready" ? session.value.projection : null;
  const project = session.status === "ready" ? session.value.project : null;

  // The binding, said once per projection the tab reads.
  const announcedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!projection || !project) return;
    const key = `${projection.recordDigest}:${projection.stateDigest ?? "-"}`;
    if (announcedRef.current === key) return;
    announcedRef.current = key;
    append({
      kind: "system",
      text:
        `Bound to ${project.projectId} at HEAD v${project.head.version} · ` +
        `reference run ${projection.referenceRun.runId} (${projection.referenceRunSource})` +
        (projection.matchesReferenceReceipt === true
          ? " · receipt reproduced"
          : projection.matchesReferenceReceipt === false
            ? " · receipt not reproduced"
            : "") +
        (projection.stateDigest === null
          ? " · the kernel refused the bound view; nothing can be proposed"
          : ""),
    });
  }, [append, project, projection]);

  const loadArtifacts = useCallback(async () => {
    setArtifacts(loading);
    try {
      setArtifacts(ready(await studio.artifacts()));
    } catch (cause) {
      setArtifacts(failed(asStudioApiError(cause)));
    }
  }, []);

  useEffect(() => {
    if (session.status === "ready") void loadArtifacts();
  }, [session.status, loadArtifacts]);

  const openLocalFile = useCallback((file: File) => {
    void viewportRef.current?.openFile(file);
  }, []);

  /**
   * Put one certified artifact in the viewer, under the chip that says where
   * it came from. Canonical exports and a candidate's own export take the
   * same route — same digest-addressed bytes, same viewer, different label.
   */
  const loadArtifactIntoViewer = useCallback(
    async (artifact: ProjectArtifactDto, label: string) => {
      setArtifactError(null);
      if (!artifact.sha256) {
        setArtifactError(
          asStudioApiError(
            new Error(
              "this receipt certifies no sha256, so there are no bytes to " +
                "address; the row says so and the studio will not guess one.",
            ),
          ),
        );
        return;
      }
      setArtifactLoadingSha(artifact.sha256);
      try {
        const file = await studio.artifactFile(
          artifact.sha256,
          artifact.fileName,
        );
        pendingArtifact.current = artifact;
        await viewportRef.current?.openFile(file, label);
      } catch (cause) {
        setArtifactError(asStudioApiError(cause));
      } finally {
        pendingArtifact.current = null;
        setArtifactLoadingSha(null);
      }
    },
    [],
  );

  /** The viewer says which file it holds; that is when the shell writes it down. */
  const noteSource = useCallback((label: string | null) => {
    setSourceLabel(label);
    setLoadedArtifact(
      label === null || label === LOCAL_SOURCE_LABEL
        ? null
        : pendingArtifact.current,
    );
    pendingArtifact.current = null;
    setPicked(null);
    // A new picture, or none: whatever ghost was drawn belonged to the old one.
    setGhostProposalId(null);
  }, []);

  const resolvePick = useCallback(
    async (pick: ViewportPick) => {
      if (stateDigest === null) {
        append({
          kind: "system",
          text: "a pick is resolved against a state; the projection has not loaded yet",
        });
        return;
      }
      try {
        const resolution = await studio.resolvePick({
          stateDigest,
          userStrings: pick.userStrings,
          documentUserStrings:
            pick.documentUserStrings ?? receiptDocumentStrings(loadedArtifact),
          objectName: pick.objectName,
        });
        const subject =
          resolution.elementId ?? resolution.componentId ?? "nothing resolvable";
        append({
          kind: "system",
          text:
            `You picked ${subject} in the model · ${resolution.status} · ` +
            `source ${resolution.sourceState}` +
            (resolution.detail ? ` · ${resolution.detail}` : ""),
        });
        const element = resolution.elementId
          ? projection?.elements.find(
              (row) => row.elementId === resolution.elementId,
            )
          : undefined;
        setPicked({
          componentId: resolution.componentId,
          elementId: resolution.elementId,
          status: resolution.status,
          sourceState: resolution.sourceState,
          fields: element
            ? Object.entries(element.numericFields).map(
                ([key, value]) => [key, value] as const,
              )
            : [],
        });
        if (resolution.status === "resolved" && resolution.componentId) {
          setSelection({
            componentId: resolution.componentId,
            elementId: resolution.elementId,
          });
        }
      } catch (cause) {
        const error = asStudioApiError(cause);
        recoverFromStaleBase(error);
        append({ kind: "refusal", error, what: "POST /api/pick/resolve" });
      }
    },
    [append, loadedArtifact, projection, recoverFromStaleBase, stateDigest],
  );

  // One proposal in flight at a time. The busy flag renders the button; this
  // ref is what stops a second send that arrives before React has re-rendered
  // with it — Enter and the form's own submission can both fire for one key.
  const proposingRef = useRef(false);
  const propose = useCallback(
    async (utterance: string) => {
      if (stateDigest === null || project === null) return;
      if (proposingRef.current) return;
      proposingRef.current = true;
      append({ kind: "you", text: utterance });
      setProposalBusy(true);
      try {
        // The sentence goes to the intent compiler: the process's agent reads
        // it against the record sheet and compiles it into the grammar, or
        // asks. With no agent configured the server takes the sentence as
        // already typed. Either way the proposal that comes back is the
        // record's, and the agent's reading travels beside it, kept apart.
        const answer = await studio.compileIntent({
          stateDigest,
          targetComponentId: selection?.componentId ?? null,
          elementId: selection?.elementId ?? null,
          utterance,
          projectId: project.projectId,
        });
        append({
          kind: "proposal",
          proposal: answer.proposal,
          agent: answer.agent,
          refinements: 0,
        });
        // The fast stage's picture: a ghost of this proposal over the loaded
        // model, drawn the moment the typed change exists. With no model on
        // screen there is nothing to draw over, and nothing is claimed.
        const spec = projection ? ghostSpecFor(projection, answer.proposal) : null;
        const copied =
          spec && sourceLabel !== null ? (viewportRef.current?.ghost(spec) ?? 0) : 0;
        if (copied > 0) {
          setGhostProposalId(answer.proposal.proposalId);
        } else {
          viewportRef.current?.ghost(null);
          setGhostProposalId(null);
          if (spec && sourceLabel !== null) {
            append({
              kind: "system",
              text: `nothing to preview here: the loaded file carries no objects of ${spec.target.elementId}`,
            });
          }
        }
        const { target } = answer.proposal;
        if (
          selection === null ||
          target.componentId !== selection.componentId ||
          target.elementId !== selection.elementId
        ) {
          setSelection({
            componentId: target.componentId,
            elementId: target.elementId,
          });
          append({
            kind: "system",
            text: `Now talking about ${target.elementId ?? target.componentId} · the proposal's target`,
          });
        }
        setDraft("");
      } catch (cause) {
        const error = asStudioApiError(cause);
        if (error.code === BLOCKED) {
          append({ kind: "question", error, utterance });
        } else {
          recoverFromStaleBase(error);
          append({ kind: "refusal", error, what: "POST /api/intents" });
        }
      } finally {
        proposingRef.current = false;
        setProposalBusy(false);
      }
    },
    [append, project, projection, recoverFromStaleBase, selection, sourceLabel, stateDigest],
  );

  /**
   * The hand moves a proposal's number. The sentence is the grammar's own —
   * ``set <key> to <n>`` with the proposal's keep clause carried — so the
   * server answers without an agent, and the answer replaces the entry's
   * proposal in place. The ghost is redrawn from the proposal that came back,
   * never from the slider: the picture is always the server's number.
   */
  const refine = useCallback(
    async (entryId: string, value: number) => {
      const entry = transcript.entries.find((row) => row.id === entryId);
      if (!entry || entry.kind !== "proposal") return;
      if (stateDigest === null || project === null) return;
      if (refineInFlight.current !== null) {
        refinePending.current.set(entryId, value);
        return;
      }
      const { proposal } = entry;
      const keep =
        proposal.protected.length > 0 ? ` keep ${proposal.protected.join(", ")}` : "";
      const utterance = `set ${proposal.target.key} to ${Number(value.toFixed(6))}${keep}`;
      refineInFlight.current = entryId;
      setRefiningEntryId(entryId);
      try {
        const answer = await studio.compileIntent({
          stateDigest,
          targetComponentId: proposal.target.componentId,
          elementId: proposal.target.elementId,
          utterance,
          projectId: project.projectId,
        });
        transcript.replaceProposal(entryId, answer.proposal, answer.agent);
        if (ghostProposalId === proposal.proposalId) {
          const spec = projection ? ghostSpecFor(projection, answer.proposal) : null;
          const copied = spec ? (viewportRef.current?.ghost(spec) ?? 0) : 0;
          setGhostProposalId(copied > 0 ? answer.proposal.proposalId : null);
        }
      } catch (cause) {
        const error = asStudioApiError(cause);
        if (error.code === BLOCKED) {
          append({ kind: "question", error, utterance });
        } else {
          recoverFromStaleBase(error);
          append({ kind: "refusal", error, what: "POST /api/intents" });
        }
      } finally {
        refineInFlight.current = null;
        setRefiningEntryId(null);
      }
      const next = refinePending.current.get(entryId);
      if (next !== undefined) {
        refinePending.current.delete(entryId);
        void refine(entryId, next);
      }
    },
    [
      append,
      ghostProposalId,
      project,
      projection,
      recoverFromStaleBase,
      stateDigest,
      transcript,
    ],
  );

  const runCandidate = useCallback(
    async (proposalId: string) => {
      setCandidateBusy(true);
      try {
        const accepted = await studio.startCandidate(proposalId);
        append({
          kind: "candidate",
          candidateId: accepted.candidateId,
          jobId: accepted.jobId,
          proposalId,
          status: accepted.status,
        });
      } catch (cause) {
        const error = asStudioApiError(cause);
        recoverFromStaleBase(error);
        append({
          kind: "refusal",
          error,
          what: `POST /api/proposals/${proposalId}/candidate`,
        });
      } finally {
        setCandidateBusy(false);
      }
    },
    [append, recoverFromStaleBase],
  );

  const noteJobStatus = useCallback(
    (candidateId: string, status: string) => {
      noteTranscriptStatus(candidateId, status);
      if (status === "succeeded" && !verdictsRef.current.has(candidateId)) {
        verdictsRef.current.add(candidateId);
        append({ kind: "verdict", candidateId });
        void loadArtifacts();
      }
    },
    [append, loadArtifacts, noteTranscriptStatus],
  );

  const noteCandidate = useCallback((candidate: CandidateDto) => {
    setCandidates((current) => ({
      ...current,
      [candidate.candidateId]: candidate,
    }));
  }, []);

  const noteValidation = useCallback((validation: ValidationDto) => {
    setValidations((current) => ({
      ...current,
      [validation.candidateId]: validation,
    }));
  }, []);

  const openEvidence = useCallback((tab: EvidenceTab) => {
    setEvidenceTab(tab);
    setEvidenceOpen(true);
  }, []);

  const pinEvidence = useCallback((pinned: boolean) => {
    setEvidencePinned(pinned);
    writePinned(pinned);
    if (pinned) setEvidenceOpen(true);
  }, []);

  // ---- derived views ---------------------------------------------------

  const candidateEntries = transcript.entries.filter(
    (entry) => entry.kind === "candidate",
  );
  const selectedCandidateId =
    candidateEntries.length > 0
      ? candidateEntries[candidateEntries.length - 1].candidateId
      : null;
  const selectedCandidate =
    selectedCandidateId === null ? null : (candidates[selectedCandidateId] ?? null);
  const selectedValidation =
    selectedCandidateId === null
      ? null
      : (validations[selectedCandidateId] ?? null);

  const utteranceOf = useMemo(() => {
    const byProposal = new Map<string, string>();
    for (const entry of transcript.entries) {
      if (entry.kind === "proposal") {
        byProposal.set(entry.proposal.proposalId, entry.proposal.utterance);
      }
    }
    return byProposal;
  }, [transcript.entries]);

  const versions = useMemo<VersionCard[]>(() => {
    if (artifacts.status !== "ready" || projection === null) return [];
    const launched = new Map(
      candidateEntries.map((entry) => [entry.candidateId, entry.proposalId]),
    );
    const cards = artifacts.value.artifacts.map((artifact): VersionCard => {
      if (artifact.runId === projection.referenceRun.runId) {
        return {
          artifact,
          label: "Reference",
          title: `HEAD v${projection.referenceRun.baseVersion} · ${artifact.fileName}`,
          meta: `${artifact.runId} · ${sha8(artifact.sha256)}`,
          verdict: null,
          sourceLabel: canonicalSourceLabel(artifact),
        };
      }
      const proposalId = launched.get(artifact.runId);
      if (proposalId !== undefined) {
        const validation = validations[artifact.runId];
        return {
          artifact,
          label: "Candidate",
          title: utteranceOf.get(proposalId) ?? artifact.fileName,
          meta: `${artifact.fileName} · ${sha8(artifact.sha256)}`,
          verdict: validation
            ? validation.advance
              ? "may advance"
              : `blocked: ${validation.blockedBy.join(", ")}`
            : null,
          sourceLabel: candidateSourceLabel(artifact.runId),
        };
      }
      return {
        artifact,
        label: "Run",
        title: artifact.runId,
        meta: `${artifact.fileName} · ${sha8(artifact.sha256)}`,
        verdict: null,
        sourceLabel: canonicalSourceLabel(artifact),
      };
    });
    const rank = { Reference: 0, Candidate: 1, Run: 2 } as const;
    return cards.sort((a, b) => rank[a.label] - rank[b.label]);
  }, [artifacts, candidateEntries, projection, utteranceOf, validations]);

  const disabledReason =
    session.status === "failed"
      ? `the binding refused: ${session.error.code}`
      : projection === null
        ? "reading the projection…"
        : projection.stateDigest === null
          ? "the kernel refused this record's bound view; fix the record before proposing"
          : null;

  // CURRENT / GHOST PREVIEW / VALIDATED — what the picture is, with the
  // server's word as its detail. A candidate's export is VALIDATED only once
  // its verdict was read; before that it is a candidate export, and says so.
  const loadedValidation =
    loadedArtifact && candidates[loadedArtifact.runId]
      ? (validations[loadedArtifact.runId] ?? null)
      : null;
  const view: ViewState | null =
    sourceLabel === null
      ? null
      : ghostProposalId !== null
        ? { state: "ghost", label: "Ghost preview", detail: "approximate" }
        : loadedArtifact && candidates[loadedArtifact.runId]
          ? loadedValidation
            ? {
                state: "validated",
                label: "Validated",
                detail: loadedValidation.advance
                  ? "may advance"
                  : `blocked: ${loadedValidation.blockedBy.join(", ")}`,
              }
            : { state: "current", label: "Candidate export", detail: "verdict not read yet" }
          : { state: "current", label: "Current", detail: null };

  const evidenceCounts = {
    honesty: honestyCount(projection, selectedCandidate, selectedValidation),
    receipts: candidateEntries.length,
    events: eventCount,
  };
  // The review summary on the evidence tab, in the reviewer's words: every
  // candidate this tab launched is a change; a change is checked once its
  // verdict was read; it needs review when the verdict refused or the run
  // failed. Nothing here is decided — each number is a count of server words.
  const review = candidateEntries.reduce(
    (sum, entry) => {
      const verdict = validations[entry.candidateId];
      if (verdict) {
        return {
          ...sum,
          checked: sum.checked + 1,
          needsReview: sum.needsReview + (verdict.advance ? 0 : 1),
        };
      }
      return {
        ...sum,
        needsReview: sum.needsReview + (entry.status === "failed" ? 1 : 0),
      };
    },
    { changes: candidateEntries.length, checked: 0, needsReview: 0 },
  );

  const drawer = (
    <EvidenceDrawer
      open={evidenceOpen}
      pinned={evidencePinned}
      tab={evidenceTab}
      counts={evidenceCounts}
      projection={projection}
      candidate={selectedCandidate}
      validation={selectedValidation}
      notices={[]}
      onTab={setEvidenceTab}
      onClose={() => setEvidenceOpen(false)}
      onPin={pinEvidence}
      onEventCount={setEventCount}
    />
  );

  return (
    <>
      <input
        ref={fileInputRef}
        className="visually-hidden"
        type="file"
        accept=".3dm"
        onChange={(event) => {
          const file = event.target.files?.item(0);
          if (file) openLocalFile(file);
          event.target.value = "";
        }}
      />
      <AppShell
        toolbar={
          <>
            <span className="wordmark">ArchFlow Studio</span>
            <span className="toolbar__sep" />
            {project ? (
              <>
                <span className="mono toolbar__item">{project.projectId}</span>
                <span className="mono toolbar__item">HEAD v{project.head.version}</span>
                <span className="pill pill--plain">proposal-only</span>
                <span className="toolbar__spacer" />
                <span className="mono toolbar__item">{project.projectDir}</span>
              </>
            ) : session.status === "failed" ? (
              <>
                <span className="toolbar__item">
                  not bound · {session.error.code}
                </span>
                <span className="toolbar__spacer" />
              </>
            ) : (
              <>
                <span className="toolbar__item">reading the binding…</span>
                <span className="toolbar__spacer" />
              </>
            )}
            <button
              type="button"
              className="toolbar__btn"
              aria-pressed={evidenceOpen || evidencePinned}
              onClick={() =>
                evidenceOpen && !evidencePinned
                  ? setEvidenceOpen(false)
                  : openEvidence(evidenceTab)
              }
            >
              Evidence
            </button>
          </>
        }
        conversation={
          <Conversation
            entries={transcript.entries}
            sessionError={session.status === "failed" ? session.error : null}
            projection={projection}
            selection={selection}
            disabledReason={disabledReason}
            busy={proposalBusy}
            runBusy={candidateBusy}
            loadingSha={artifactLoadingSha}
            ghostProposalId={ghostProposalId}
            refiningEntryId={refiningEntryId}
            draft={draft}
            onDraft={setDraft}
            onSubmit={(utterance) => void propose(utterance)}
            onSelect={(componentId, elementId) => {
              setSelection({ componentId, elementId });
              append({
                kind: "system",
                text: `Talking about ${elementId ?? componentId} · chosen from the record`,
              });
            }}
            callbacks={{
              onRun: (proposalId) => void runCandidate(proposalId),
              onReply: setDraft,
              onAdjust: setDraft,
              onRefine: refine,
              onJobStatus: noteJobStatus,
              onCandidate: noteCandidate,
              onPreview: (artifact, label) =>
                void loadArtifactIntoViewer(artifact, label),
              onValidation: noteValidation,
              onEvidence: openEvidence,
            }}
          />
        }
        stage={
          <Stage
            viewportRef={viewportRef}
            sourceLabel={sourceLabel}
            message={viewerMessage}
            status={viewerStatus}
            inspection={inspection}
            artifactError={artifactError}
            view={view}
            picked={picked}
            versions={versions}
            loadingSha={artifactLoadingSha}
            loadedSha={loadedArtifact?.sha256 ?? null}
            evidenceCounts={evidenceCounts}
            review={review}
            drawer={evidencePinned ? null : drawer}
            onInspection={setInspection}
            onStatus={(status, message) => {
              setViewerStatus(status);
              setViewerMessage(message);
            }}
            onRequestFile={() => fileInputRef.current?.click()}
            onSource={noteSource}
            onPick={(pick) => void resolvePick(pick)}
            onOpenVersion={(artifact, label) =>
              void loadArtifactIntoViewer(artifact, label)
            }
            onEvidence={openEvidence}
          />
        }
        pinnedDrawer={evidencePinned ? drawer : null}
      />
    </>
  );
}
