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
  CompareDto,
  GestureDto,
  ProjectArtifactDto,
  ProposalDto,
  StateProjectionDto,
  ValidationDto,
} from "../api/generated";
import type { GestureTool } from "../features/stage/Annotate";
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
import {
  seatOf,
  type VersionExport,
  type VersionGroup,
} from "../features/stage/VersionsStrip";
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
  const { append, remove: removeEntry, noteJobStatus: noteTranscriptStatus } = transcript;
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
  // Select + draw + say: the armed tool and the marks made on this picture,
  // sent with the next sentence and cleared when a proposal answers it.
  const [tool, setTool] = useState<GestureTool | null>(null);
  const [gestures, setGestures] = useState<readonly GestureDto[]>([]);
  // A cross-fade in the viewer: the loaded export as 'before', a candidate's
  // export of the same seat as 'after', and where the slider stands.
  const [blendState, setBlendState] = useState<{
    candidateId: string;
    against: string;
    t: number;
    meshes: number;
  } | null>(null);
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

  // The first ten seconds: a bound project shows its own certified model
  // without being asked - the reference run's export when it has one, else
  // the last export the listing names (the server lists runs by id; nothing
  // here claims it is the newest), said so in the conversation. A file from
  // this machine stays a secondary door; it is the one with no receipt.
  const autoLoadedRef = useRef(false);
  useEffect(() => {
    if (autoLoadedRef.current) return;
    if (artifacts.status !== "ready" || projection === null) return;
    if (loadedArtifact !== null || pendingArtifact.current !== null) return;
    const rows = artifacts.value.artifacts.filter(
      (row) => row.available && row.sha256 !== null,
    );
    if (rows.length === 0) return;
    const reference = rows.find((row) => row.runId === projection.referenceRun.runId);
    const pick = reference ?? rows[rows.length - 1];
    autoLoadedRef.current = true;
    append({
      kind: "system",
      text: reference
        ? `Showing the reference run's export · ${pick.fileName}`
        : `The reference run ${projection.referenceRun.runId} left no export; showing the last export listed, ${pick.fileName} from run ${pick.runId}`,
    });
    void loadArtifactIntoViewer(pick, canonicalSourceLabel(pick));
  }, [append, artifacts, loadArtifactIntoViewer, loadedArtifact, projection]);

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
    // A new picture, or none: whatever ghost was drawn belonged to the old one,
    // and so did the marks and the cross-fade.
    setGhostProposalId(null);
    setGestures([]);
    setTool(null);
    setBlendState(null);
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
      // The architect's sentence, exactly as said; the marks on their own line.
      append({ kind: "you", text: utterance });
      if (gestures.length > 0) {
        append({
          kind: "system",
          text: `with ${gestures.length} ${gestures.length === 1 ? "mark" : "marks"} on the model: ${gestures.map((gesture) => gesture.kind).join(", ")}`,
        });
      }
      setProposalBusy(true);
      // The waiting half, on screen: who is reading what, and for how long.
      // The answer - card, question or refusal - replaces this line.
      const readingId = append({
        kind: "reading",
        subject:
          selection?.elementId ??
          selection?.componentId ??
          (gestures.some((gesture) => gesture.kind === "circle")
            ? "what you circled"
            : "the record"),
        recordSize: `${projection?.counts.components ?? "?"} components, ${projection?.elements.length ?? "?"} elements`,
        provider: project.intentProvider,
        startedAt: Date.now(),
      });
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
          gestures: [...gestures],
        });
        // What the server read off the marks, in the record's names — printed
        // before the proposal so the reader sees what the sentence was said with.
        for (const fact of answer.gestures ?? []) {
          append({ kind: "system", text: `read from the model: ${fact}` });
        }
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
          // The picked chip described the old subject; it must not outlive it.
          setPicked(null);
        }
        setDraft("");
        // The marks were said; a new sentence starts clean. A question keeps
        // them, so the reply is made with the same marks.
        setGestures([]);
        setTool(null);
      } catch (cause) {
        const error = asStudioApiError(cause);
        // No proposal came back, so no card claims the ghost that may still
        // stand from the last one: the picture goes back to the loaded model.
        viewportRef.current?.ghost(null);
        setGhostProposalId(null);
        if (error.code === BLOCKED) {
          append({ kind: "question", error, utterance });
        } else {
          recoverFromStaleBase(error);
          append({ kind: "refusal", error, what: "POST /api/intents" });
        }
      } finally {
        removeEntry(readingId);
        proposingRef.current = false;
        setProposalBusy(false);
      }
    },
    [
      append,
      gestures,
      project,
      projection,
      recoverFromStaleBase,
      removeEntry,
      selection,
      sourceLabel,
      stateDigest,
    ],
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
        // Drawn under the same condition as the first proposal — a model on
        // screen — not only when a ghost already stood: the first attempt may
        // have found no objects in the file loaded then, and a later file may
        // carry them.
        const spec = projection ? ghostSpecFor(projection, answer.proposal) : null;
        const copied =
          spec && sourceLabel !== null ? (viewportRef.current?.ghost(spec) ?? 0) : 0;
        setGhostProposalId(copied > 0 ? answer.proposal.proposalId : null);
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
      project,
      projection,
      recoverFromStaleBase,
      sourceLabel,
      stateDigest,
      transcript,
    ],
  );

  /**
   * Before / After / Why: this card's run against the run on screen, from
   * the inspection records both retained. The answer is a card in the
   * conversation; the counts are the server's.
   */
  const compareVersions = useCallback(
    async (artifact: ProjectArtifactDto) => {
      const against = loadedArtifact?.runId ?? null;
      if (against === null || artifact.runId === against) return;
      try {
        const comparison = await studio.compare(artifact.runId, against);
        append({ kind: "compare", comparison });
      } catch (cause) {
        append({
          kind: "refusal",
          error: asStudioApiError(cause),
          what: `GET /api/candidates/${artifact.runId}/compare`,
        });
      }
    },
    [append, loadedArtifact],
  );

  /**
   * Compare in the model: the candidate's export of the seat on screen is
   * loaded beside the loaded one and cross-faded. When the candidate left no
   * export of that seat, the conversation says so rather than showing another.
   */
  const compareInModel = useCallback(
    async (comparison: CompareDto) => {
      const shown = loadedArtifact;
      if (shown === null || shown.runId !== comparison.against) {
        append({
          kind: "system",
          text: `to cross-fade, load an export of ${comparison.against} first; the comparison was counted against it`,
        });
        return;
      }
      const rows = artifacts.status === "ready" ? artifacts.value.artifacts : [];
      const twin = rows.find(
        (row) =>
          row.runId === comparison.candidateId &&
          row.stageId === shown.stageId &&
          row.available &&
          row.sha256 !== null,
      );
      if (!twin || !twin.sha256) {
        append({
          kind: "system",
          text: `${comparison.candidateId} left no servable export of ${shown.stageId ?? "this seat"}; nothing to cross-fade`,
        });
        return;
      }
      try {
        const file = await studio.artifactFile(twin.sha256, twin.fileName);
        const meshes = (await viewportRef.current?.loadSecondary(file)) ?? 0;
        setBlendState({
          candidateId: comparison.candidateId,
          against: comparison.against,
          t: 0.5,
          meshes,
        });
      } catch (cause) {
        append({
          kind: "refusal",
          error: asStudioApiError(cause),
          what: `GET /api/artifacts/${twin.sha256}/bytes`,
        });
      }
    },
    [append, artifacts, loadedArtifact],
  );

  const runCandidate = useCallback(
    async (proposalId: string) => {
      setCandidateBusy(true);
      // The approximation has done its work: from here the picture is the
      // loaded model until the exact geometry arrives.
      viewportRef.current?.ghost(null);
      setGhostProposalId(null);
      manualLoadRef.current = false;
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

  // The transcript as of the last render, for callbacks that must stay
  // stable: a card's poll restarts whenever its reporter changes identity,
  // so the reporter reads the entries through a ref instead of closing over
  // them.
  const entriesRef = useRef(transcript.entries);
  entriesRef.current = transcript.entries;

  const noteJobStatus = useCallback(
    (candidateId: string, status: string) => {
      noteTranscriptStatus(candidateId, status);
      if (status === "succeeded" && !verdictsRef.current.has(candidateId)) {
        verdictsRef.current.add(candidateId);
        // The card's Protected line quotes what the sentence asked to keep;
        // that is the proposal's, found through the candidate it became.
        const candidateEntry = entriesRef.current.find(
          (entry) => entry.kind === "candidate" && entry.candidateId === candidateId,
        );
        const proposalEntry =
          candidateEntry && candidateEntry.kind === "candidate"
            ? entriesRef.current.find(
                (entry) =>
                  entry.kind === "proposal" &&
                  entry.proposal.proposalId === candidateEntry.proposalId,
              )
            : undefined;
        append({
          kind: "verdict",
          candidateId,
          protectedRefs:
            proposalEntry && proposalEntry.kind === "proposal"
              ? proposalEntry.proposal.protected
              : [],
        });
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

  // A verdict answers "what happened"; the exact model is the rest of the
  // answer. Once a candidate's verdict is read, its export of the seat on
  // screen is loaded in place of the picture the change was drawn over.
  const autoShowRef = useRef<string | null>(null);
  // Whether the architect chose an export to look at since the last Apply:
  // then the verdict's model is announced, not swapped in over their choice.
  const manualLoadRef = useRef(false);
  const noteValidation = useCallback((validation: ValidationDto) => {
    setValidations((current) => ({
      ...current,
      [validation.candidateId]: validation,
    }));
    autoShowRef.current = validation.candidateId;
  }, []);

  useEffect(() => {
    const candidateId = autoShowRef.current;
    if (candidateId === null || artifacts.status !== "ready") return;
    const validation = validations[candidateId];
    if (!validation) return;
    const rows = artifacts.value.artifacts.filter(
      (row) => row.runId === candidateId && row.available && row.sha256 !== null,
    );
    if (rows.length === 0) return;
    const twin =
      rows.find((row) => loadedArtifact !== null && row.stageId === loadedArtifact.stageId) ??
      rows[0];
    autoShowRef.current = null;
    if (manualLoadRef.current && loadedArtifact !== null && loadedArtifact.runId !== candidateId) {
      append({
        kind: "system",
        text: `the exact model is ready · ${twin.fileName} · not shown: you chose ${loadedArtifact.fileName} to look at; its card can show it`,
      });
      return;
    }
    append({
      kind: "system",
      text: `the exact model is on screen · ${twin.fileName} · ${
        validation.advance ? "may advance" : `blocked: ${validation.blockedBy.join(", ")}`
      }`,
    });
    void loadArtifactIntoViewer(twin, candidateSourceLabel(candidateId));
  }, [append, artifacts, loadArtifactIntoViewer, loadedArtifact, validations]);

  // Which candidate the drawer shows: the one whose card was clicked, else
  // the latest this tab launched. A card's "receipts" opens its own run.
  const [evidenceCandidateId, setEvidenceCandidateId] = useState<string | null>(null);
  const openEvidence = useCallback((tab: EvidenceTab, candidateId?: string) => {
    if (candidateId !== undefined) setEvidenceCandidateId(candidateId);
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
    evidenceCandidateId !== null &&
    candidateEntries.some((entry) => entry.candidateId === evidenceCandidateId)
      ? evidenceCandidateId
      : candidateEntries.length > 0
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

  // One card per run. The reference run first, then this tab's candidates
  // newest first, then every other run newest first (the run id carries its
  // stamp). Each card's exports are its seats' files; nothing here decides a
  // verdict — the word on a candidate is the one the server gave.
  const versions = useMemo<VersionGroup[]>(() => {
    if (artifacts.status !== "ready" || projection === null) return [];
    const launched = new Map(
      candidateEntries.map((entry) => [entry.candidateId, entry.proposalId]),
    );
    const byRun = new Map<string, ProjectArtifactDto[]>();
    for (const artifact of artifacts.value.artifacts) {
      byRun.set(artifact.runId, [...(byRun.get(artifact.runId) ?? []), artifact]);
    }
    const groups = [...byRun.entries()].map(([runId, rows]): VersionGroup => {
      const exports = rows.map(
        (artifact): VersionExport => ({
          artifact,
          seat: seatOf(artifact),
          sourceLabel:
            launched.has(runId)
              ? candidateSourceLabel(runId)
              : canonicalSourceLabel(artifact),
        }),
      );
      if (runId === projection.referenceRun.runId) {
        return {
          runId,
          label: "Reference",
          title: `HEAD v${projection.referenceRun.baseVersion}`,
          detail: null,
          exports,
        };
      }
      const proposalId = launched.get(runId);
      if (proposalId !== undefined) {
        const validation = validations[runId];
        return {
          runId,
          label: "Candidate",
          title: utteranceOf.get(proposalId) ?? runId,
          detail: validation
            ? validation.advance
              ? "may advance"
              : `blocked: ${validation.blockedBy.join(", ")}`
            : "verdict not read yet",
          exports,
        };
      }
      return {
        runId,
        label: "Run",
        title: runId.slice(-13),
        detail: "not launched from this tab",
        exports,
      };
    });
    const rank = { Reference: 0, Candidate: 1, Run: 2 } as const;
    const launchedOrder = candidateEntries.map((entry) => entry.candidateId);
    return groups.sort((a, b) => {
      if (rank[a.label] !== rank[b.label]) return rank[a.label] - rank[b.label];
      if (a.label === "Candidate") {
        return launchedOrder.indexOf(b.runId) - launchedOrder.indexOf(a.runId);
      }
      return b.runId.localeCompare(a.runId);
    });
  }, [artifacts, candidateEntries, projection, utteranceOf, validations]);

  // A sentence needs a subject: the pick, the picker, or a circle on the
  // model (the server makes a circle the selection). With none of them the
  // agent would choose the subject, which is the guessing the owner ruled out.
  const hasSubject =
    selection !== null || gestures.some((gesture) => gesture.kind === "circle");
  const disabledReason =
    session.status === "failed"
      ? `the binding refused: ${session.error.code}`
      : projection === null
        ? "reading the projection…"
        : projection.stateDigest === null
          ? "the kernel refused this record's bound view; fix the record before proposing"
          : !hasSubject
            ? "pick something in the model first, or choose a component"
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
            ? loadedValidation.advance
              ? { state: "validated", label: "Validated", detail: "may advance" }
              : {
                  // The strongest word on the picture is the verdict's, in the
                  // verdict's colour: a blocked candidate is checked, not validated.
                  state: "blocked",
                  label: "Checked",
                  detail: `blocked: ${loadedValidation.blockedBy.join(", ")}`,
                }
            : { state: "current", label: "Candidate export", detail: "verdict not read yet" }
          : { state: "current", label: "Current", detail: null };

  // The sentence a candidate was made from, as this tab heard it: for the
  // drawer's title and for naming a queued candidate's blocker by its words.
  const sentenceOfCandidate = useCallback(
    (candidateId: string | null): string | null => {
      if (candidateId === null) return null;
      const entry = candidateEntries.find(
        (row) => row.kind === "candidate" && row.candidateId === candidateId,
      );
      return entry && entry.kind === "candidate"
        ? (utteranceOf.get(entry.proposalId) ?? null)
        : null;
    },
    [candidateEntries, utteranceOf],
  );
  const selectedSentence = sentenceOfCandidate(selectedCandidateId);

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
      sentence={selectedSentence}
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
                <span className="mono toolbar__item" title={project.projectDir}>
                  {project.projectId}
                </span>
                <span className="mono toolbar__item">HEAD v{project.head.version}</span>
                <span
                  className="pill pill--plain"
                  title="proposal only · every run is a harness beside the project; nothing is written to HEAD"
                >
                  proposal only · nothing is written to the project
                </span>
                <span className="toolbar__spacer" />
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
            gestures={gestures}
            onRemoveGesture={(index) =>
              setGestures((current) => current.filter((_, i) => i !== index))
            }
            intentProvider={project?.intentProvider ?? null}
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
              onAdjust: (sentence) => {
                // Adjust puts the compiled sentence in the composer to edit.
                // Text already there is not lost silently, and the ghost of
                // the proposal being adjusted comes off the model.
                if (draft.trim() !== "" && draft !== sentence) {
                  append({
                    kind: "system",
                    text: `the unsent text "${draft}" was replaced by the proposal's sentence`,
                  });
                }
                setDraft(sentence);
                viewportRef.current?.ghost(null);
                setGhostProposalId(null);
              },
              onRefine: refine,
              onCompareInModel: (comparison) => void compareInModel(comparison),
              labelOf: sentenceOfCandidate,
              onJobStatus: noteJobStatus,
              onCandidate: noteCandidate,
              onPreview: (artifact, label) => {
                manualLoadRef.current = true;
                void loadArtifactIntoViewer(artifact, label);
              },
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
            tool={tool}
            gestures={gestures}
            onTool={setTool}
            onGesture={(gesture) => setGestures((current) => [...current, gesture])}
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
            onOpenVersion={(artifact, label) => {
              manualLoadRef.current = true;
              void loadArtifactIntoViewer(artifact, label);
            }}
            loadedRunId={loadedArtifact?.runId ?? null}
            onCompareVersion={(artifact) => void compareVersions(artifact)}
            blend={blendState}
            onBlend={(t) => {
              viewportRef.current?.blend(t);
              setBlendState((current) => (current ? { ...current, t } : current));
            }}
            onEndBlend={() => {
              viewportRef.current?.clearSecondary();
              setBlendState(null);
            }}
            onEvidence={openEvidence}
          />
        }
        pinnedDrawer={evidencePinned ? drawer : null}
      />
    </>
  );
}
