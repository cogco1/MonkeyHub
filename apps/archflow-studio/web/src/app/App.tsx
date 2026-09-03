/**
 * The studio shell: one slice of the round-1 chain, end to end, over real
 * routes.
 *
 * What this file does is hold the few pieces of state the panels share — the
 * selection, the current proposal, the candidate runs this tab launched — and
 * pass every question to the API. What it deliberately does not do:
 *
 *  - it imports nothing from archflow and computes no geometry;
 *  - it derives no impact, no relation counts and no advance verdict;
 *  - it writes nothing to the project or to HEAD;
 *  - it keeps no version history: the panels are a view of a running server,
 *    and reloading the tab loses the view, not the work.
 *
 * Every failed call ends in a panel showing the server's code and detail. The
 * one error handled rather than merely displayed is `STALE_BASE`: the project
 * moved, so the projection is read again and the fact is announced.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { asStudioApiError, studio, type StudioApiError } from "../api/client";
import type {
  ArtifactListDto,
  PickResolutionDto,
  ProjectArtifactDto,
  ProposalDto,
} from "../api/generated";
import {
  LOCAL_SOURCE_LABEL,
  type ViewportController,
  type ViewportPick,
  type ViewportStatus,
} from "../viewer/ThreeDmViewport";
import { ViewerPanel } from "../viewer/ViewerPanel";
import type { SceneInspection } from "../viewer/sceneInspection";
import {
  ArtifactList,
  canonicalSourceLabel,
  receiptDocumentStrings,
} from "../features/artifacts/ArtifactList";
import { CandidatePanel } from "../features/candidate/CandidatePanel";
import {
  CandidateRuns,
  type LaunchedCandidate,
} from "../features/candidate/CandidateRuns";
import { ReviewPanel } from "../features/candidate/ReviewPanel";
import { EventStream } from "../features/events/EventStream";
import { ImpactPanel } from "../features/impact/ImpactPanel";
import { IntentPanel } from "../features/intent/IntentPanel";
import { PickPanel } from "../features/pick/PickPanel";
import { ProposalPanel } from "../features/proposal/ProposalPanel";
import { TopBar } from "../features/project/TopBar";
import { ComponentTree } from "../features/state/ComponentTree";
import { HonestyLines } from "../features/state/HonestyLines";
import { SelectionPanel } from "../features/state/SelectionPanel";
import { ValidationPanel } from "../features/validation/ValidationPanel";
import { ErrorPanel } from "./ErrorPanel";
import { Panel, Shell } from "./Shell";
import { failed, idle, loading, ready, valueOf, type Loadable } from "./loadable";
import { useSession } from "./useSession";

const NOTICE_LIMIT = 50;

export default function App() {
  const [notices, setNotices] = useState<readonly string[]>([]);
  const pushNotice = useCallback((line: string) => {
    setNotices((current) =>
      [...current, `${new Date().toISOString()} · ${line}`].slice(-NOTICE_LIMIT),
    );
  }, []);

  const { session, stateDigest, reload, recoverFromStaleBase } =
    useSession(pushNotice);

  const [componentId, setComponentId] = useState<string | null>(null);
  const [elementId, setElementId] = useState<string | null>(null);

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
  // themselves carry document strings the loader does not surface, and the
  // receipt that certified those bytes does.
  const [loadedArtifact, setLoadedArtifact] =
    useState<ProjectArtifactDto | null>(null);
  // The artifact whose bytes were handed to the viewer and which the viewer has
  // not yet accepted. It is held here rather than committed straight to
  // `loadedArtifact` because the viewer can refuse a file — a name that is not
  // `.3dm`, an empty one, one over its parse ceiling — and a refusal leaves the
  // previous model on screen. Committing on hand-off would leave the shell
  // answering picks on that still-visible model with the receipt of a file that
  // never loaded.
  const pendingArtifact = useRef<ProjectArtifactDto | null>(null);

  const [pick, setPick] = useState<Loadable<PickResolutionDto>>(idle);
  const [proposal, setProposal] = useState<Loadable<ProposalDto>>(idle);
  const [proposalBusy, setProposalBusy] = useState(false);
  const [proposalError, setProposalError] = useState<StudioApiError | null>(
    null,
  );

  const [launched, setLaunched] = useState<readonly LaunchedCandidate[]>([]);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(
    null,
  );
  const [candidateBusy, setCandidateBusy] = useState(false);
  const [candidateError, setCandidateError] = useState<StudioApiError | null>(
    null,
  );

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

  const selectComponent = useCallback((id: string) => {
    setComponentId(id);
    setElementId(null);
  }, []);

  const openLocalFile = useCallback((file: File) => {
    void viewportRef.current?.openFile(file);
  }, []);

  /**
   * Put one certified artifact in the viewer, under the chip that says where it
   * came from. Canonical exports and a candidate's own export take the same
   * route — same digest-addressed bytes, same viewer, different label.
   */
  const loadArtifactIntoViewer = useCallback(
    async (artifact: ProjectArtifactDto, sourceLabel: string) => {
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
        await viewportRef.current?.openFile(file, sourceLabel);
      } catch (cause) {
        setArtifactError(asStudioApiError(cause));
      } finally {
        // Whatever happened, this hand-off is over. If the viewer accepted the
        // file it has already taken the artifact under its label; if it refused
        // it never will, and the row must not be left waiting to be claimed by
        // some later load.
        pendingArtifact.current = null;
        setArtifactLoadingSha(null);
      }
    },
    [],
  );

  /**
   * The viewer says which file it holds, and that is when the shell writes down
   * what the file answers for.
   *
   * A non-local label is only ever reported once the viewer has actually parsed
   * the bytes it was given, so it — and nothing earlier — is the moment the
   * pending artifact becomes the loaded one. A local file, a cleared viewport
   * and a failed parse all report no non-local source, and each of them leaves
   * the shell with no receipt to answer picks from.
   */
  const noteSource = useCallback((label: string | null) => {
    setSourceLabel(label);
    setLoadedArtifact(
      label === null || label === LOCAL_SOURCE_LABEL
        ? null
        : pendingArtifact.current,
    );
    pendingArtifact.current = null;
  }, []);

  const resolvePick = useCallback(
    async (picked: ViewportPick) => {
      if (stateDigest === null) {
        pushNotice(
          "a pick is resolved against a state; the projection has not loaded yet",
        );
        return;
      }
      setPick(loading);
      try {
        const resolution = await studio.resolvePick({
          stateDigest,
          userStrings: picked.userStrings,
          // What the file says about itself: the loader's own document strings
          // when it exposes them, and otherwise the receipt that certified
          // these exact bytes. A locally dropped file has neither and sends
          // null, which the server reads as "not shown to be current".
          documentUserStrings:
            picked.documentUserStrings ?? receiptDocumentStrings(loadedArtifact),
          objectName: picked.objectName,
        });
        setPick(ready(resolution));
        if (resolution.status === "resolved" && resolution.componentId) {
          setComponentId(resolution.componentId);
          setElementId(resolution.elementId);
        }
      } catch (cause) {
        const error = asStudioApiError(cause);
        recoverFromStaleBase(error);
        setPick(failed(error));
      }
    },
    [loadedArtifact, pushNotice, recoverFromStaleBase, stateDigest],
  );

  const propose = useCallback(
    async (utterance: string) => {
      if (stateDigest === null || componentId === null) return;
      setProposalBusy(true);
      setProposalError(null);
      try {
        const value = await studio.createProposal({
          stateDigest,
          targetComponentId: componentId,
          elementId,
          utterance,
          projectId:
            session.status === "ready" ? session.value.project.projectId : null,
        });
        setProposal(ready(value));
        // A candidate belongs to a proposal; a new proposal starts with none.
        setLaunched([]);
        setSelectedCandidateId(null);
        setCandidateError(null);
      } catch (cause) {
        const error = asStudioApiError(cause);
        recoverFromStaleBase(error);
        setProposalError(error);
      } finally {
        setProposalBusy(false);
      }
    },
    [componentId, elementId, recoverFromStaleBase, session, stateDigest],
  );

  const runCandidate = useCallback(async () => {
    const current = valueOf(proposal);
    if (current === null) return;
    setCandidateBusy(true);
    setCandidateError(null);
    try {
      const accepted = await studio.startCandidate(current.proposalId);
      setLaunched((current2) => [
        {
          candidateId: accepted.candidateId,
          jobId: accepted.jobId,
          proposalId: current.proposalId,
          startedAt: new Date().toISOString(),
          status: accepted.status,
        },
        ...current2,
      ]);
      setSelectedCandidateId(accepted.candidateId);
    } catch (cause) {
      const error = asStudioApiError(cause);
      recoverFromStaleBase(error);
      setCandidateError(error);
    } finally {
      setCandidateBusy(false);
    }
  }, [proposal, recoverFromStaleBase]);

  const noteJobStatus = useCallback((candidate: string, status: string) => {
    setLaunched((current) =>
      current.map((run) =>
        run.candidateId === candidate && run.status !== status
          ? { ...run, status }
          : run,
      ),
    );
  }, []);

  const selected = launched.find(
    (run) => run.candidateId === selectedCandidateId,
  );
  const projection =
    session.status === "ready" ? session.value.projection : null;
  const currentProposal = valueOf(proposal);

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
      <Shell
        top={
          session.status === "ready" ? (
            <TopBar
              project={session.value.project}
              projection={session.value.projection}
            />
          ) : session.status === "failed" ? (
            <ErrorPanel error={session.error} what="GET /api/project · /api/state" />
          ) : (
            <p className="topbar topbar--pending">reading the binding…</p>
          )
        }
        left={
          <>
            <Panel
              title="component tree"
              aside={
                <button
                  type="button"
                  className="button button--small"
                  onClick={() => void reload()}
                >
                  re-project
                </button>
              }
            >
              {session.status === "failed" ? (
                <ErrorPanel error={session.error} what="GET /api/state" />
              ) : projection ? (
                <ComponentTree
                  projection={projection}
                  selectedComponentId={componentId}
                  onSelect={selectComponent}
                />
              ) : (
                <p className="panel__note">reading the projection…</p>
              )}
            </Panel>

            <Panel title="selection">
              {projection ? (
                <SelectionPanel
                  projection={projection}
                  selectedComponentId={componentId}
                  selectedElementId={elementId}
                  onSelectElement={setElementId}
                />
              ) : (
                <p className="panel__note">reading the projection…</p>
              )}
            </Panel>

            <Panel title="record honesty">
              {projection ? (
                <HonestyLines lines={projection.honesty} />
              ) : (
                <p className="panel__note">reading the projection…</p>
              )}
            </Panel>

            <Panel
              title="artifacts"
              aside={
                <button
                  type="button"
                  className="button button--small"
                  onClick={() => void loadArtifacts()}
                >
                  reload
                </button>
              }
            >
              {/* The listing is only asked for once the binding answers, so a
                  failed session is why this panel is empty — saying "reading
                  the receipts…" forever would be a pending state that is never
                  going to resolve. */}
              {session.status === "failed" ? (
                <ErrorPanel
                  error={session.error}
                  what="GET /api/project · /api/state"
                />
              ) : artifacts.status === "failed" ? (
                <ErrorPanel error={artifacts.error} what="GET /api/artifacts" />
              ) : artifacts.status === "ready" ? (
                <ArtifactList
                  listing={artifacts.value}
                  loadingSha={artifactLoadingSha}
                  onLoad={(artifact) =>
                    void loadArtifactIntoViewer(
                      artifact,
                      canonicalSourceLabel(artifact),
                    )
                  }
                />
              ) : (
                <p className="panel__note">reading the receipts…</p>
              )}
            </Panel>
          </>
        }
        center={
          <>
            <ViewerPanel
              viewportRef={viewportRef}
              sourceLabel={sourceLabel}
              message={viewerMessage}
              status={viewerStatus}
              inspection={inspection}
              artifactError={artifactError}
              onInspection={setInspection}
              onStatus={(status, message) => {
                setViewerStatus(status);
                setViewerMessage(message);
              }}
              onRequestFile={() => fileInputRef.current?.click()}
              onSource={noteSource}
              onPick={(picked) => void resolvePick(picked)}
            />
            <Panel title="pick">
              {pick.status === "failed" ? (
                <ErrorPanel error={pick.error} what="POST /api/pick/resolve" />
              ) : pick.status === "ready" ? (
                <PickPanel pick={pick.value} />
              ) : pick.status === "loading" ? (
                <p className="panel__note">resolving the pick…</p>
              ) : (
                <p className="panel__note">
                  click an object in a loaded model. The viewer reads the
                  object's user strings and the server says what they mean.
                </p>
              )}
            </Panel>
          </>
        }
        right={
          <>
            <Panel title="intent">
              <IntentPanel
                selectedComponentId={componentId}
                selectedElementId={elementId}
                busy={proposalBusy}
                error={proposalError}
                onSubmit={(utterance) => void propose(utterance)}
              />
            </Panel>

            <Panel title="typed proposal">
              {currentProposal ? (
                <ProposalPanel proposal={currentProposal} />
              ) : (
                <p className="panel__note">no proposal yet.</p>
              )}
            </Panel>

            <Panel title="impact">
              {currentProposal ? (
                <ImpactPanel impact={currentProposal.impact} />
              ) : (
                <p className="panel__note">
                  impact is the kernel's closure of a proposal; there is no
                  proposal yet.
                </p>
              )}
            </Panel>

            <Panel title="human review">
              <ReviewPanel
                proposal={currentProposal}
                busy={candidateBusy}
                error={candidateError}
                onRun={() => void runCandidate()}
              />
            </Panel>

            <Panel title="validation">
              {/* The server's own word for the job, passed through. Which of
                  the three things it means — still running, finished without a
                  verdict, or a verdict to read — is the panel's to say, and
                  this shell does not decide it on the way. */}
              <ValidationPanel
                candidateId={selected?.candidateId ?? null}
                jobStatus={selected?.status ?? null}
              />
            </Panel>
          </>
        }
        bottom={
          <>
            <Panel title="candidate runs (this tab)">
              <CandidateRuns
                launched={launched}
                selectedCandidateId={selectedCandidateId}
                onSelect={setSelectedCandidateId}
              />
              {selected && (
                <CandidatePanel
                  key={selected.candidateId}
                  candidateId={selected.candidateId}
                  jobId={selected.jobId}
                  loadingSha={artifactLoadingSha}
                  onJobStatus={noteJobStatus}
                  onOpenArtifact={(artifact, label) =>
                    void loadArtifactIntoViewer(artifact, label)
                  }
                />
              )}
            </Panel>
            <Panel title="events">
              <EventStream notices={notices} />
            </Panel>
          </>
        }
      />
    </>
  );
}
