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
 *  - it writes nothing to the project and issues nothing;
 *  - it keeps no version history: reloading the tab loses the view, not the
 *    work.
 *
 * Every failed call ends in a card showing the server's code and detail. The
 * one error handled rather than merely displayed is `STALE_BASE`: the project
 * moved, so the projection is read again and the fact is said in the
 * transcript.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  BLOCKED_NEEDS_HUMAN,
  MISSING_EDITABLE_CONTROL,
  STALE_CLARIFICATION,
  UNSUPPORTED_REQUEST,
  asStudioApiError,
  studio,
  type StudioApiError,
} from "../api/client";
import type { ServerIdentity } from "../api/connection";
import type {
  ArtifactListDto,
  CandidateDto,
  CompareDto,
  GestureDto,
  MassingOptionRequestDto,
  OptionsDto,
  PendingIntentDto,
  FrameDto,
  ProgramDto,
  ProgramSheetDto,
  ProjectArtifactDto,
  ProposalDto,
  SemanticsDto,
  StateProjectionDto,
  ValidationDto,
  VolumesDto,
} from "../api/generated";
import type { GestureTool } from "../features/stage/Annotate";
import {
  canonicalRunSourceLabel,
  canonicalSourceLabel,
  candidateSourceLabel,
  receiptDocumentStrings,
  seatOf,
} from "../features/artifacts/artifactLabels";
import { Conversation } from "../features/conversation/Conversation";
import type { Choice } from "../features/conversation/cards/QuestionCard";
import type { Selection } from "../features/conversation/Composer";
import { EvidenceDrawer } from "../features/evidence/EvidenceDrawer";
import { OptionsPanel } from "../features/options/OptionsPanel";
import { honestyCount } from "../features/evidence/HonestyTab";
import { SettingsPanel } from "../features/settings/SettingsPanel";
import { FrameEditor } from "../features/stage/FrameEditor";
import {
  ProgramPanel,
  edited,
  type SpaceEdit,
} from "../features/program/ProgramPanel";
import type { ViewState } from "../features/stage/SourceChip";
import { Stage, type HomeModel, type PickedFacts } from "../features/stage/Stage";
import type { VersionExport, VersionGroup } from "../features/stage/VersionsStrip";
import { useT } from "../i18n/useT";
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
import { LoadingOverlay } from "./LoadingOverlay";
import { useSession } from "./useSession";
import { useTranscript, type SystemTextPart } from "./transcript";

/** The three refusing outcomes of an intent, and the two that end an exchange. */
const TERMINAL_OUTCOMES = [MISSING_EDITABLE_CONTROL, UNSUPPORTED_REQUEST];

function systemText(parts: readonly SystemTextPart[]): {
  readonly text: string;
  readonly parts: readonly SystemTextPart[];
} {
  return {
    text: parts.map((part) => part.text).join(""),
    parts,
  };
}

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

/** Home, with the bytes it is made of; the toolbar sees only the first two. */
interface HomeArtifacts extends HomeModel {
  readonly artifacts: readonly ProjectArtifactDto[];
  /** The reference run, which a fallback has to name to explain itself. */
  readonly referenceRunId: string;
}

export default function App({ server }: { server: ServerIdentity }) {
  const t = useT();
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
  // The one clarification this tab is in the middle of, as the server described
  // it. A ref rather than state because it is not drawn: the cards show what
  // the server said, and this holds only the token the next request carries
  // back. There is deliberately no second copy of the conversation here — the
  // transcript is history, and the pending intent is the server's.
  const pendingIntentRef = useRef<PendingIntentDto | null>(null);

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
  // The listing rows of the artifacts currently in the viewer, kept beside
  // their source label: one seat's export, or every seat of a run shown at
  // once. They are what the loaded picture can be asked about — the bytes
  // carry document strings the loader does not surface, and the receipts that
  // certified those bytes do.
  const [loadedArtifacts, setLoadedArtifacts] = useState<
    readonly ProjectArtifactDto[]
  >([]);
  // The artifacts whose bytes were handed to the viewer and which the viewer
  // has not yet accepted. Committed only when the viewer reports the label,
  // because a refused file leaves the previous model on screen.
  const pendingArtifacts = useRef<readonly ProjectArtifactDto[]>([]);
  // The first seat on screen answers for the picture wherever one row is
  // wanted: the run it belongs to, the receipt a pick is resolved against,
  // the seat a cross-fade is loaded beside.
  const loadedArtifact = loadedArtifacts[0] ?? null;
  // Every digest on screen, for the strip to say which of its buttons is the
  // picture: one seat's, or all of a run's.
  const loadedShas = loadedArtifacts
    .map((artifact) => artifact.sha256)
    .filter((sha): sha is string => sha !== null);

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

  // The record's frame — its levels and axes — read once per projection while
  // the panel is open. It is a read of the record, not of the picture, so it
  // is fetched on demand rather than at boot.
  const [frame, setFrame] = useState<Loadable<FrameDto>>(idle);
  const [frameOpen, setFrameOpen] = useState(false);

  // The massing on the table: the record's own volumes, and the options this
  // server process is holding beside them. Read on demand like the frame, and
  // read again whenever the record underneath moves — a card measuring a
  // record the tab has left is a number about a different building.
  const [optionsTable, setOptionsTable] = useState<Loadable<OptionsDto>>(idle);
  const [volumes, setVolumes] = useState<Loadable<VolumesDto>>(idle);
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [optionsBusy, setOptionsBusy] = useState(false);
  // The program sheet: what the server answered, and the copy this tab is
  // editing. They are two values on purpose — `program` is the server's
  // document and stays as it was answered, `sheet` is what will be sent back,
  // and keeping one would lose the ability to say what has been changed.
  const [program, setProgram] = useState<Loadable<ProgramDto>>(idle);
  const [programOpen, setProgramOpen] = useState(false);
  const [sheet, setSheet] = useState<ProgramSheetDto | null>(null);
  const [applyingProgram, setApplyingProgram] = useState(false);
  // The record's semantic vocabulary, read once: it is the framework's, not
  // this project's, so it does not change when the record does.
  const [semantics, setSemantics] = useState<Loadable<SemanticsDto>>(idle);

  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [evidencePinned, setEvidencePinned] = useState(readPinned);
  const [evidenceTab, setEvidenceTab] = useState<EvidenceTab>("honesty");
  const [eventCount, setEventCount] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [conversationOpen, setConversationOpen] = useState(true);

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
    const parts: SystemTextPart[] = [
      { kind: "prose", text: "Bound to " },
      { kind: "technical", text: project.projectId },
      { kind: "prose", text: " at issue " },
      { kind: "technical", text: String(project.published.version) },
      { kind: "prose", text: " · reference run " },
      { kind: "technical", text: projection.referenceRun.runId },
      { kind: "prose", text: " (" },
      { kind: "technical", text: projection.referenceRunSource },
      { kind: "prose", text: ")" },
    ];
    if (projection.matchesReferenceReceipt === true) {
      parts.push({ kind: "prose", text: " · receipt reproduced" });
    } else if (projection.matchesReferenceReceipt === false) {
      parts.push({ kind: "prose", text: " · receipt not reproduced" });
    }
    if (projection.stateDigest === null) {
      parts.push({
        kind: "prose",
        text: " · the kernel refused the bound view; nothing can be proposed",
      });
    }
    append({
      kind: "system",
      ...systemText(parts),
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

  const loadFrame = useCallback(async () => {
    setFrame(loading);
    try {
      setFrame(ready(await studio.frame()));
    } catch (cause) {
      setFrame(failed(asStudioApiError(cause)));
    }
  }, []);

  // Read while the panel is open, and read again when the record underneath it
  // changes: a frame from a record the tab has left is a picture of a building
  // that is no longer the one on screen.
  useEffect(() => {
    if (!frameOpen || projection === null) return;
    void loadFrame();
  }, [frameOpen, projection?.recordDigest, loadFrame]);

  const loadOptions = useCallback(async () => {
    setOptionsTable(loading);
    setVolumes(loading);
    try {
      setOptionsTable(ready(await studio.options()));
    } catch (cause) {
      setOptionsTable(failed(asStudioApiError(cause)));
    }
    try {
      setVolumes(ready(await studio.volumes()));
    } catch (cause) {
      setVolumes(failed(asStudioApiError(cause)));
    }
  }, []);

  const loadProgram = useCallback(async () => {
    setProgram(loading);
    try {
      const answer = await studio.program();
      setProgram(ready(answer));
      // The edited copy is replaced by what the server just said. Anything
      // typed and not applied is lost, and that is the honest outcome: the
      // sheet on screen has to be one the server would accept back.
      setSheet(answer.sheet);
    } catch (cause) {
      setProgram(failed(asStudioApiError(cause)));
    }
  }, []);

  useEffect(() => {
    if (!optionsOpen || projection === null) return;
    void loadOptions();
  }, [optionsOpen, projection?.recordDigest, loadOptions]);

  useEffect(() => {
    if (!programOpen || projection === null) return;
    void loadProgram();
  }, [programOpen, projection?.recordDigest, loadProgram]);

  useEffect(() => {
    if (!programOpen || semantics.status !== "idle") return;
    setSemantics(loading);
    void (async () => {
      try {
        setSemantics(ready(await studio.semantics()));
      } catch (cause) {
        setSemantics(failed(asStudioApiError(cause)));
      }
    })();
  }, [programOpen, semantics.status]);

  /**
   * Send the edited sheet to be run as a candidate.
   *
   * It appends a system line rather than a candidate card: a card polls
   * `GET /api/candidates/{id}`, which reads a candidate *against its
   * proposal*, and a sheet is not a proposal. The line names the run and
   * repeats the server's own honesty verbatim.
   */
  const applyProgram = useCallback(
    async (current: ProgramSheetDto, saveInput: boolean) => {
      if (stateDigest === null) return;
      setApplyingProgram(true);
      try {
        const answer = await studio.applyProgram({
          stateDigest,
          sheet: current,
          saveInput,
        });
        append({
          kind: "system",
          ...systemText([
            { kind: "prose", text: "Program applied as candidate " },
            { kind: "technical", text: answer.candidateId },
            { kind: "prose", text: " · job " },
            { kind: "technical", text: answer.jobId },
            {
              kind: "prose",
              text: answer.savedInput ? " · sheet saved" : " · sheet not saved",
            },
          ]),
        });
        for (const line of answer.honesty) {
          append({ kind: "system", ...systemText([{ kind: "prose", text: line }]) });
        }
      } catch (cause) {
        const error = asStudioApiError(cause);
        recoverFromStaleBase(error);
        append({ kind: "refusal", error, what: "POST /api/program" });
      } finally {
        setApplyingProgram(false);
      }
    },
    [append, recoverFromStaleBase, stateDigest],
  );

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
        pendingArtifacts.current = [artifact];
        await viewportRef.current?.openFile(file, label);
      } catch (cause) {
        setArtifactError(asStudioApiError(cause));
      } finally {
        pendingArtifacts.current = [];
        setArtifactLoadingSha(null);
      }
    },
    [],
  );

  /**
   * Put a whole run on the stage: every seat it exported, in the listing's
   * order, as one picture. The seats are certified separately and shown
   * together; the chip names the run and the seats rather than one digest,
   * because there is no one file on screen to address.
   */
  const loadRunIntoViewer = useCallback(
    async (rows: readonly ProjectArtifactDto[], label: string) => {
      setArtifactError(null);
      const servable = rows.filter(
        (row): row is ProjectArtifactDto & { sha256: string } =>
          row.available && row.sha256 !== null,
      );
      if (servable.length === 0) return;
      if (servable.length === 1) {
        await loadArtifactIntoViewer(servable[0], label);
        return;
      }
      setArtifactLoadingSha(servable[0].sha256);
      try {
        // Every seat, or none: a picture missing a seat that nobody was told
        // about would read as the run being smaller than it is.
        const files = await Promise.all(
          servable.map((row) => studio.artifactFile(row.sha256, row.fileName)),
        );
        pendingArtifacts.current = servable;
        await viewportRef.current?.openFiles(files, label);
      } catch (cause) {
        setArtifactError(asStudioApiError(cause));
      } finally {
        pendingArtifacts.current = [];
        setArtifactLoadingSha(null);
      }
    },
    [loadArtifactIntoViewer],
  );

  /**
   * The chip a run wears on the stage: the candidate's own, one seat's
   * digest, or the run and the seats it put there.
   */
  const runSourceLabel = useCallback(
    (runId: string, rows: readonly ProjectArtifactDto[]): string => {
      const launched = transcript.entries.some(
        (entry) => entry.kind === "candidate" && entry.candidateId === runId,
      );
      if (launched) return candidateSourceLabel(runId);
      return rows.length === 1
        ? canonicalSourceLabel(rows[0])
        : canonicalRunSourceLabel(runId, rows.map(seatOf));
    },
    [transcript.entries],
  );

  /** Every export of the reference run this project can serve, as listed. */
  const referenceExports = useMemo<readonly ProjectArtifactDto[]>(() => {
    if (artifacts.status !== "ready" || projection === null) return [];
    return artifacts.value.artifacts.filter(
      (row) =>
        row.runId === projection.referenceRun.runId &&
        row.available &&
        row.sha256 !== null,
    );
  }, [artifacts, projection]);

  /**
   * Home: the picture the stage opens on, and the one thing that brings it
   * back. Every export of the reference run when it left any; failing that,
   * the last export the listing names (the server lists runs by id; nothing
   * here claims it is the newest), which is the fallback the conversation is
   * told about. Derived once, here, so the auto-load, the toolbar button and
   * the Reference card cannot disagree about what going back means.
   */
  const homeArtifacts = useMemo<HomeArtifacts | null>(() => {
    if (artifacts.status !== "ready" || projection === null) return null;
    if (referenceExports.length > 0) {
      return {
        kind: "reference",
        runId: projection.referenceRun.runId,
        artifacts: referenceExports,
        referenceRunId: projection.referenceRun.runId,
      };
    }
    const rows = artifacts.value.artifacts.filter(
      (row) => row.available && row.sha256 !== null,
    );
    if (rows.length === 0) return null;
    const pick = rows[rows.length - 1];
    return {
      kind: "fallback",
      runId: pick.runId,
      artifacts: [pick],
      referenceRunId: projection.referenceRun.runId,
    };
  }, [artifacts, projection, referenceExports]);

  /**
   * Back home, from wherever the stage got to — one seat of another run, a
   * candidate's export, a local file, or nothing at all after clear. The
   * sentence written to the conversation is the auto-load's own, because this
   * is the same act: the reference run's seats, or the fallback export named
   * with the run it came from.
   *
   * `manual` is false for the auto-load alone, which is nobody's choice: a
   * verdict may still put its exact model on screen over it.
   */
  const showHome = useCallback(
    (manual: boolean) => {
      if (homeArtifacts === null) return;
      manualLoadRef.current = manual;
      const rows = homeArtifacts.artifacts;
      if (homeArtifacts.kind === "reference") {
        const seats = rows.map(seatOf);
        append({
          kind: "system",
          ...systemText([
            {
              kind: "prose",
              text:
                rows.length === 1
                  ? "Showing the reference run's export · "
                  : "Showing the reference run's exports · ",
            },
            {
              kind: "technical",
              text: rows.length === 1 ? rows[0].fileName : seats.join(" + "),
            },
          ]),
        });
        void loadRunIntoViewer(rows, runSourceLabel(homeArtifacts.runId, rows));
        return;
      }
      const pick = rows[0];
      append({
        kind: "system",
        ...systemText([
          { kind: "prose", text: "The reference run " },
          { kind: "technical", text: homeArtifacts.referenceRunId },
          {
            kind: "prose",
            text: " left no export; showing the last export listed, ",
          },
          { kind: "technical", text: pick.fileName },
          { kind: "prose", text: " from run " },
          { kind: "technical", text: pick.runId },
        ]),
      });
      void loadArtifactIntoViewer(pick, canonicalSourceLabel(pick));
    },
    [
      append,
      homeArtifacts,
      loadArtifactIntoViewer,
      loadRunIntoViewer,
      runSourceLabel,
    ],
  );

  // The first ten seconds: a bound project shows its own certified model
  // without being asked — home, and the conversation is told which of the two
  // home turned out to be. A file from this machine stays a secondary door;
  // it is the one with no receipt.
  const autoLoadedRef = useRef(false);
  useEffect(() => {
    if (autoLoadedRef.current) return;
    if (homeArtifacts === null) return;
    if (loadedArtifacts.length > 0 || pendingArtifacts.current.length > 0) return;
    autoLoadedRef.current = true;
    showHome(false);
  }, [homeArtifacts, loadedArtifacts, showHome]);

  /** The viewer says which file it holds; that is when the shell writes it down. */
  const noteSource = useCallback((label: string | null) => {
    setSourceLabel(label);
    setLoadedArtifacts(
      label === null || label === LOCAL_SOURCE_LABEL
        ? []
        : pendingArtifacts.current,
    );
    pendingArtifacts.current = [];
    setPicked(null);
    // The mark belonged to the picture going away, as the picked chip did.
    viewportRef.current?.highlight(null);
    // A new picture, or none: whatever ghost was drawn belonged to the old one,
    // and so did the marks and the cross-fade.
    setGhostProposalId(null);
    setGestures([]);
    setTool(null);
    setBlendState(null);
  }, []);

  const resolvePick = useCallback(
    async (pick: ViewportPick) => {
      // What the ray met, lit at once: the click has an answer on the model
      // before the server has said what it is. A resolved pick widens the mark
      // to every object of the element below; an unresolved one leaves it here.
      viewportRef.current?.highlight({ object: pick.object });
      if (stateDigest === null) {
        append({
          kind: "system",
          text: "a pick is resolved against a state; the projection has not loaded yet",
          parts: [
            {
              kind: "prose",
              text: "a pick is resolved against a state; the projection has not loaded yet",
            },
          ],
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
          ...systemText([
            { kind: "prose", text: "You picked " },
            { kind: "technical", text: subject },
            { kind: "prose", text: " in the model · " },
            { kind: "technical", text: resolution.status },
            { kind: "prose", text: " · source " },
            { kind: "technical", text: resolution.sourceState },
            ...(resolution.detail
              ? ([
                  { kind: "prose", text: " · " },
                  { kind: "technical", text: resolution.detail },
                ] satisfies SystemTextPart[])
              : []),
          ]),
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
        // The server named it, so the mark becomes the element's: every object
        // the export tagged with it, not only the face the ray met. When the
        // loaded picture carries none of them, the one hit object stays lit —
        // never nothing, which would read as the click having missed.
        if (resolution.elementId !== null || resolution.componentId !== null) {
          const lit =
            viewportRef.current?.highlight({
              componentId: resolution.componentId,
              elementId: resolution.elementId,
            }) ?? 0;
          if (lit === 0) viewportRef.current?.highlight({ object: pick.object });
        }
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
  /**
   * Take the target the server resolved, whatever this tab was pointing at.
   *
   * The selection and the pending target move together or not at all. That is
   * the whole of "no — the columns" working: the correction is not a hint the
   * next request may or may not act on, it is the state of this tab from the
   * moment the answer arrives.
   */
  const adoptTarget = useCallback(
    (pending: PendingIntentDto) => {
      const componentId = pending.targetComponentId;
      if (componentId === null) return;
      const elementId = pending.elementId ?? null;
      if (
        selection !== null &&
        selection.componentId === componentId &&
        selection.elementId === elementId
      ) {
        return;
      }
      setSelection({ componentId, elementId });
      // The picked chip described the old subject; it must not outlive it, and
      // neither must the mark on the model that went with it.
      setPicked(null);
      viewportRef.current?.highlight(null);
      append({
        kind: "system",
        ...systemText([
          { kind: "prose", text: "Now talking about " },
          { kind: "technical", text: elementId ?? componentId },
          { kind: "prose", text: " · the target the server resolved" },
        ]),
      });
    },
    [append, selection],
  );
  const propose = useCallback(
    async (utterance: string, override?: Selection | null) => {
      if (stateDigest === null || project === null) return;
      if (proposingRef.current) return;
      proposingRef.current = true;
      // What this request is asked against, and which exchange it belongs to.
      // The token is the whole of the continuity: no transcript is sent, and
      // the pending intent it names carries the original sentence, the target
      // resolved so far and everything already ruled out.
      const asked = override === undefined ? selection : override;
      const continuationToken =
        pendingIntentRef.current?.continuationToken ?? null;
      // The architect's sentence, exactly as said; the marks on their own line.
      append({ kind: "you", text: utterance });
      if (gestures.length > 0) {
        append({
          kind: "system",
          ...systemText([
            { kind: "prose", text: "with " },
            { kind: "technical", text: String(gestures.length) },
            {
              kind: "prose",
              text: gestures.length === 1 ? " mark on the model: " : " marks on the model: ",
            },
            {
              kind: "technical",
              text: gestures.map((gesture) => gesture.kind).join(", "),
            },
          ]),
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
            ? t("reading.subjectCircled")
            : t("reading.subjectRecord")),
        recordSize: t("reading.recordSize", {
          components: projection?.counts.components ?? "?",
          elements: projection?.elements.length ?? "?",
        }),
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
          targetComponentId: asked?.componentId ?? null,
          elementId: asked?.elementId ?? null,
          utterance,
          projectId: project.projectId,
          gestures: [...gestures],
          continuationToken,
        });
        // COMPILED, the one outcome that is a proposal: the exchange is over
        // and the token that got here is spent.
        pendingIntentRef.current = null;
        // What the server read off the marks, in the record's names — printed
        // before the proposal so the reader sees what the sentence was said with.
        for (const fact of answer.gestures ?? []) {
          append({
            kind: "system",
            ...systemText([
              { kind: "prose", text: "read from the model: " },
              { kind: "technical", text: fact },
            ]),
          });
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
              ...systemText([
                {
                  kind: "prose",
                  text: "nothing to preview here: the loaded file carries no objects of ",
                },
                { kind: "technical", text: spec.target.elementId },
              ]),
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
            ...systemText([
              { kind: "prose", text: "Now talking about " },
              {
                kind: "technical",
                text: target.elementId ?? target.componentId,
              },
              { kind: "prose", text: " · the proposal's target" },
            ]),
          });
          // The picked chip described the old subject; it must not outlive it,
          // and neither must the mark on the model that went with it.
          setPicked(null);
          viewportRef.current?.highlight(null);
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
        const pending = error.pendingIntent;
        if (error.code === STALE_CLARIFICATION) {
          // The pending intent was opened against a state the project has
          // left. It is void — an answer given about the old record is not
          // applied to the new one — so this tab drops it rather than
          // continuing an exchange nobody checked.
          pendingIntentRef.current = null;
          append({ kind: "refusal", error, what: "POST /api/intents" });
        } else if (pending !== null) {
          // A refusal that belongs to a clarification moves this tab with it:
          // the target the server resolved is the target the next sentence is
          // about, and the two are never allowed to disagree. A terminal
          // outcome leaves no token, so the card that shows it asks nothing.
          adoptTarget(pending);
          pendingIntentRef.current =
            pending.continuationToken === null ? null : pending;
          append({
            kind: TERMINAL_OUTCOMES.includes(error.code) ? "terminal" : "question",
            error,
            utterance,
          });
        } else if (error.code === BLOCKED_NEEDS_HUMAN) {
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
      adoptTarget,
      append,
      gestures,
      project,
      projection,
      recoverFromStaleBase,
      removeEntry,
      selection,
      sourceLabel,
      stateDigest,
      t,
    ],
  );

  /**
   * Answer a question with one of the server's own candidates.
   *
   * One click does all of it: the selection moves to what was chosen and the
   * original request is asked again against it, carrying the pending intent's
   * token. Nothing is retyped, and the request that follows cannot be about
   * the thing that was just ruled out.
   */
  const chooseCandidate = useCallback(
    (choice: Choice) => {
      const pending = pendingIntentRef.current;
      if (pending === null) return;
      const next: Selection = {
        componentId: choice.componentId,
        elementId: choice.elementId,
      };
      setSelection(next);
      setPicked(null);
      viewportRef.current?.highlight(null);
      append({
        kind: "system",
        ...systemText([
          { kind: "prose", text: "Now talking about " },
          { kind: "technical", text: choice.elementId ?? choice.componentId },
          {
            kind: "prose",
            text: " · chosen from the answers the server offered",
          },
        ]),
      });
      void propose(pending.originalUtterance, next);
    },
    [append, propose],
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
        // A refinement is a sentence already in the grammar against a target
        // the record just answered about, so it starts no clarification and
        // carries no token. It can still be refused, and a refusal that ends
        // the matter shows the card that ends it.
        if (TERMINAL_OUTCOMES.includes(error.code)) {
          append({ kind: "terminal", error, utterance });
        } else if (error.code === BLOCKED_NEEDS_HUMAN) {
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
          ...systemText([
            { kind: "prose", text: "to cross-fade, load an export of " },
            { kind: "technical", text: comparison.against },
            {
              kind: "prose",
              text: " first; the comparison was counted against it",
            },
          ]),
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
          ...systemText([
            { kind: "technical", text: comparison.candidateId },
            { kind: "prose", text: " left no servable export of " },
            { kind: "technical", text: shown.stageId ?? "this seat" },
            { kind: "prose", text: "; nothing to cross-fade" },
          ]),
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

  /**
   * One massing option, made by the server and put on the table.
   *
   * The browser computes nothing here: the transform and its parameters go to
   * the server, which applies it to the record's own pack, measures the
   * result, and refuses a massing the kernel would not build. A refusal is
   * shown as itself.
   */
  const makeOption = useCallback(
    async (body: MassingOptionRequestDto) => {
      setOptionsBusy(true);
      try {
        await studio.makeOption(body);
        setOptionsTable(ready(await studio.options()));
      } catch (cause) {
        const error = asStudioApiError(cause);
        recoverFromStaleBase(error);
        append({ kind: "refusal", error, what: "POST /api/options" });
      } finally {
        setOptionsBusy(false);
      }
    },
    [append, recoverFromStaleBase],
  );

  /**
   * Run one option as a candidate. It joins the transcript as the candidate
   * card every other run gets — same job, same polling, same verdict — because
   * a selected massing *is* a candidate run and a second kind of card for it
   * would be a second vocabulary for one thing.
   */
  const selectOption = useCallback(
    async (optionId: string) => {
      setOptionsBusy(true);
      manualLoadRef.current = false;
      try {
        const accepted = await studio.selectOption(optionId);
        append({
          kind: "candidate",
          candidateId: accepted.candidateId,
          jobId: accepted.jobId,
          proposalId: optionId,
          status: accepted.status,
        });
      } catch (cause) {
        const error = asStudioApiError(cause);
        recoverFromStaleBase(error);
        append({
          kind: "refusal",
          error,
          what: `POST /api/options/${optionId}/select`,
        });
      } finally {
        setOptionsBusy(false);
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
        ...systemText([
          { kind: "prose", text: "the exact model is ready · " },
          { kind: "technical", text: twin.fileName },
          { kind: "prose", text: " · not shown: you chose " },
          { kind: "technical", text: loadedArtifact.fileName },
          {
            kind: "prose",
            text: " to look at; its card can show it",
          },
        ]),
      });
      return;
    }
    append({
      kind: "system",
      ...systemText([
        { kind: "prose", text: "the exact model is on screen · " },
        { kind: "technical", text: twin.fileName },
        {
          kind: "prose",
          text: validation.advance ? " · may advance" : " · blocked: ",
        },
        ...(validation.advance
          ? []
          : ([
              {
                kind: "technical",
                text: validation.blockedBy.join(", "),
              },
            ] satisfies SystemTextPart[])),
      ]),
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
          // The run's base, not the published position: a reference run can
          // stand on an issue the project has already left.
          title: `based on issue ${projection.referenceRun.baseVersion}`,
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
      ? t("shell.bindingRefused", { code: session.error.code })
      : projection === null
        ? t("shell.readingProjection")
        : projection.stateDigest === null
          ? t("shell.boundViewRefused")
          : !hasSubject
            ? t("shell.pickFirst")
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
      server={server}
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

  // The tab is still starting up until the API has answered for the binding. The launcher's
  // splash said the same three things while the servers came up; this is the second half of
  // that wait, and it is over when the shell has a project to name.
  const booting = session.status === "idle" || session.status === "loading";

  return (
    <>
      {booting && (
        <LoadingOverlay
          mode="boot"
          status={
            session.status === "idle"
              ? t("loading.startingSession")
              : (
                  <>
                    {t("loading.readingBinding")} ·{" "}
                    <code lang="en">GET /api/project</code>
                  </>
                )
          }
        />
      )}
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
            <span
              className="wordmark"
              title={t("shell.wordmarkTitle")}
            >
              MonkeyArch
            </span>
            <span className="toolbar__sep" />
            {project ? (
              <>
                <span className="mono toolbar__item" title={project.projectDir}>
                  {project.projectId}
                </span>
                <span className="mono toolbar__item">
                  {t("shell.publishedIssue", { version: project.published.version })}
                </span>
                <span
                  className="pill pill--plain"
                  title={t("shell.proposalOnlyTitle")}
                >
                  {t("shell.proposalOnly")}
                </span>
                <span className="toolbar__spacer" />
              </>
            ) : session.status === "failed" ? (
              <>
                <span className="toolbar__item">
                  {t("shell.notBound", { code: session.error.code })}
                </span>
                <span className="toolbar__spacer" />
              </>
            ) : (
              <>
                <span className="toolbar__item">{t("shell.readingBinding")}</span>
                <span className="toolbar__spacer" />
              </>
            )}
            <button
              type="button"
              className="toolbar__btn"
              aria-controls="conversation-panel"
              aria-expanded={conversationOpen}
              onClick={() => setConversationOpen((open) => !open)}
            >
              {t("conversation.title")}
            </button>
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
              {t("nav.evidence")}
            </button>
            <button
              type="button"
              className="toolbar__btn"
              aria-haspopup="dialog"
              aria-expanded={settingsOpen}
              onClick={() => setSettingsOpen(true)}
            >
              {t("nav.settings")}
            </button>
          </>
        }
        conversation={conversationOpen ? (
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
                ...systemText([
                  { kind: "prose", text: "Talking about " },
                  { kind: "technical", text: elementId ?? componentId },
                  { kind: "prose", text: " · chosen from the record" },
                ]),
              });
            }}
            callbacks={{
              onRun: (proposalId) => void runCandidate(proposalId),
              // An accepted form is a shape to type a number into, so it goes
              // into the composer; sending it verbatim would earn the same
              // question back. A candidate is an answer, so it is sent — see
              // onChoose, which moves the selection with it.
              onReply: setDraft,
              onChoose: chooseCandidate,
              onAdjust: (sentence) => {
                // Adjust puts the compiled sentence in the composer to edit.
                // Text already there is not lost silently, and the ghost of
                // the proposal being adjusted comes off the model.
                if (draft.trim() !== "" && draft !== sentence) {
                  append({
                    kind: "system",
                    ...systemText([
                      { kind: "prose", text: "the unsent text “" },
                      { kind: "user", text: draft },
                      {
                        kind: "prose",
                        text: "” was replaced by the proposal's sentence",
                      },
                    ]),
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
        ) : null}
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
            loadedShas={loadedShas}
            evidenceCounts={evidenceCounts}
            review={review}
            drawer={evidencePinned ? null : drawer}
            frameOpen={frameOpen}
            onToggleFrame={() => setFrameOpen((open) => !open)}
            framePanel={
              frameOpen ? (
                <FrameEditor
                  frame={frame}
                  projection={projection}
                  onPick={(componentId, elementId) => {
                    setSelection({ componentId, elementId });
                    append({
                      kind: "system",
                      ...systemText([
                        { kind: "prose", text: "Talking about " },
                        { kind: "technical", text: elementId ?? componentId },
                        {
                          kind: "prose",
                          text: " · chosen from the frame's closure",
                        },
                      ]),
                    });
                  }}
                  onClose={() => setFrameOpen(false)}
                />
              ) : null
            }
            optionsOpen={optionsOpen}
            onToggleOptions={() => setOptionsOpen((open) => !open)}
            optionsPanel={
              optionsOpen ? (
                <OptionsPanel
                  table={optionsTable}
                  volumes={volumes}
                  stateDigest={projection?.stateDigest ?? null}
                  busy={optionsBusy}
                  onMake={(body) => void makeOption(body)}
                  onSelect={(optionId) => void selectOption(optionId)}
                  onClose={() => setOptionsOpen(false)}
                />
              ) : null
            }
            programOpen={programOpen}
            onToggleProgram={() => setProgramOpen((open) => !open)}
            programPanel={
              programOpen ? (
                <ProgramPanel
                  program={program}
                  semantics={semantics}
                  sheet={sheet}
                  applying={applyingProgram}
                  // Only a local studio writes the architect's own file; a
                  // remote server refuses, so the box is not offered there.
                  canSave={server.mode === "local"}
                  onEdit={(edit: SpaceEdit) =>
                    setSheet((current) =>
                      current === null ? current : edited(current, edit),
                    )
                  }
                  onApply={(current, saveInput) =>
                    void applyProgram(current, saveInput)
                  }
                  onReread={() => void loadProgram()}
                  onClose={() => setProgramOpen(false)}
                />
              ) : null
            }
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
            onOpenRun={(group) => {
              // The Reference card's `show run` is the toolbar's button by
              // another name whenever home is the reference run's exports;
              // one act, one sentence about it.
              if (
                homeArtifacts !== null &&
                homeArtifacts.kind === "reference" &&
                group.runId === homeArtifacts.runId
              ) {
                showHome(true);
                return;
              }
              const rows = group.exports
                .map((item) => item.artifact)
                .filter((artifact) => artifact.available && artifact.sha256 !== null);
              if (rows.length === 0) return;
              manualLoadRef.current = true;
              append({
                kind: "system",
                ...systemText([
                  {
                    kind: "prose",
                    text: rows.length === 1 ? "Showing " : "Showing every seat of ",
                  },
                  { kind: "technical", text: group.runId },
                  { kind: "prose", text: " · " },
                  {
                    kind: "technical",
                    text:
                      rows.length === 1
                        ? rows[0].fileName
                        : rows.map(seatOf).join(" + "),
                  },
                ]),
              });
              void loadRunIntoViewer(rows, runSourceLabel(group.runId, rows));
            }}
            onShowHome={() => showHome(true)}
            home={homeArtifacts}
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
      <SettingsPanel
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        server={server}
        project={project}
      />
    </>
  );
}
