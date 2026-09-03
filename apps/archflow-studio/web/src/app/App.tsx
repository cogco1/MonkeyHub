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
import { Stage, type PickedFacts } from "../features/stage/Stage";
import type { VersionCard } from "../features/stage/VersionsStrip";
import type { SceneInspection } from "../viewer/sceneInspection";
import {
  LOCAL_SOURCE_LABEL,
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
      if (stateDigest === null || selection === null || project === null) return;
      if (proposingRef.current) return;
      proposingRef.current = true;
      append({ kind: "you", text: utterance });
      setProposalBusy(true);
      try {
        const proposal = await studio.createProposal({
          stateDigest,
          targetComponentId: selection.componentId,
          elementId: selection.elementId,
          utterance,
          projectId: project.projectId,
        });
        append({ kind: "proposal", proposal });
        setDraft("");
      } catch (cause) {
        const error = asStudioApiError(cause);
        if (error.code === BLOCKED) {
          append({ kind: "question", error, utterance });
        } else {
          recoverFromStaleBase(error);
          append({ kind: "refusal", error, what: "POST /api/proposals" });
        }
      } finally {
        proposingRef.current = false;
        setProposalBusy(false);
      }
    },
    [append, project, recoverFromStaleBase, selection, stateDigest],
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
          : selection === null
            ? "pick an object in the model, or choose a component, before proposing"
            : null;

  const evidenceCounts = {
    honesty: honestyCount(projection, selectedCandidate, selectedValidation),
    receipts: candidateEntries.length,
    events: eventCount,
  };

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
                <span className="mono toolbar__item">
                  HEAD v{project.head.version} · {sha8(project.head.stateSha256)}
                </span>
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
            picked={picked}
            versions={versions}
            loadingSha={artifactLoadingSha}
            loadedSha={loadedArtifact?.sha256 ?? null}
            evidenceCounts={evidenceCounts}
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
