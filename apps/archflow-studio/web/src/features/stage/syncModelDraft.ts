/** Submit one frozen local edit prefix. Only its final proposal becomes a run. */
import type { CandidateAcceptedDto, FrameDto } from "../../api/generated/types.gen";
import type { studio } from "../../api/client";
import type { DraftCommand, DraftSnapshot } from "./modelDraft";

export interface ModelDraftSource {
  readonly projectId: string;
  readonly stateDigest: string;
  readonly sourceRunId: string | null;
  readonly sourceStageRef: string | null;
}

export interface ModelDraftSyncAttempt {
  readonly requestId: string;
  nextCommand: number;
  sourceProposalId: string | null;
  /** Non-null once candidate submission begins; its request must never be replaced on retry. */
  finalProposalId: string | null;
  accepted?: CandidateAcceptedDto;
  inFlight?: Promise<CandidateAcceptedDto | null>;
}

export type ModelDraftSyncApi = Pick<typeof studio,
  "sketch" | "transform" | "pushPull" | "removeElement" | "startCandidate" | "runtime">;

export function createModelDraftSyncAttempt(requestId: string = crypto.randomUUID()): ModelDraftSyncAttempt {
  return { requestId, nextCommand: 0, sourceProposalId: null, finalProposalId: null };
}

const buildingVector = ([x, y, z]: readonly [number, number, number]): [number, number, number] => [x, z, y];
const errorCode = (error: unknown): string | undefined =>
  typeof error === "object" && error !== null && "code" in error ? String(error.code) : undefined;

function propose(command: DraftCommand, source: ModelDraftSource, frame: FrameDto,
  sourceProposalId: string | null, api: ModelDraftSyncApi) {
  const base = { ...source, sourceProposalId, elementId: command.elementId };
  if (command.kind === "delete") return api.removeElement(base);
  if (command.kind === "sketch") {
    const action = command.action;
    const elevation = action.plane?.origin[2] ?? action.base;
    const level = frame.levels.reduce<FrameDto["levels"][number] | undefined>((nearest, row) =>
      !nearest || Math.abs(row.elevation - elevation) < Math.abs(nearest.elevation - elevation) ? row : nearest, undefined);
    if (!level) throw new Error("The drawing needs an existing base level.");
    return api.sketch({ ...base, componentId: command.componentId,
      profile: action.profile.map(([x, y]) => [x, y]), height: action.height,
      closed: action.closed ?? true, baseLevel: level.levelId,
      plane: action.plane ? {
        origin: [action.plane.origin[0], elevation - level.elevation, action.plane.origin[1]],
        xAxis: buildingVector(action.plane.xAxis), yAxis: buildingVector(action.plane.yAxis),
        normal: buildingVector(action.plane.normal),
      } : { origin: [0, elevation - level.elevation, 0],
        xAxis: [1, 0, 0], yAxis: [0, 0, 1], normal: [0, 1, 0] },
    });
  }
  const action = command.action;
  if (action.kind === "pushPull") {
    if (!action.normal) throw new Error("The frozen Push/Pull action needs its selected face normal.");
    return api.pushPull({ ...base, distance: action.distance, normal: buildingVector(action.normal) });
  }
  if (action.kind === "copy" && !command.copyElementId) throw new Error("The frozen copy needs a stable element id.");
  return api.transform({ ...base, kind: action.kind,
    ...(action.kind === "move" || action.kind === "copy" ? { translation: buildingVector(action.translation) } : {}),
    ...(action.kind === "copy" ? { copyElementId: command.copyElementId } : {}),
    // Swapping Y/Z reverses handedness, including the sign of a rotation.
    ...(action.kind === "rotate" ? { angleDegrees: -action.angleDegrees, axis: buildingVector(action.axis) } : {}),
    ...(action.kind === "scale" ? { scale: buildingVector(action.scale) } : {}),
  });
}

async function submit(snapshot: DraftSnapshot, source: ModelDraftSource, frame: FrameDto,
  attempt: ModelDraftSyncAttempt, api: ModelDraftSyncApi): Promise<CandidateAcceptedDto | null> {
  if (attempt.accepted) return attempt.accepted;
  if (!attempt.finalProposalId) {
    let replayed = false;
    while (attempt.nextCommand < snapshot.commands.length) {
      try {
        const proposal = await propose(snapshot.commands[attempt.nextCommand]!, source, frame, attempt.sourceProposalId, api);
        attempt.sourceProposalId = proposal.proposalId;
        attempt.nextCommand += 1;
      } catch (error) {
        if (errorCode(error) === "PROPOSAL_CHAIN_NO_CHANGE") {
          // This checked prefix now equals the original base; the preceding
          // nonempty proposal must not accidentally become its candidate.
          attempt.sourceProposalId = null;
          attempt.nextCommand += 1;
        } else if (errorCode(error) === "PROPOSAL_NOT_FOUND" && attempt.sourceProposalId && !replayed) {
          attempt.nextCommand = 0;
          attempt.sourceProposalId = null;
          replayed = true;
        } else throw error;
      }
    }
    if (!attempt.sourceProposalId) return null;
    attempt.finalProposalId = attempt.sourceProposalId;
  }
  try {
    attempt.accepted = await api.startCandidate(attempt.finalProposalId, undefined, attempt.requestId);
  } catch (error) {
    // Hub already maps this UUID to one run. A lost response or restarted
    // worker never permits rebuilding the proposal and changing that request.
    const code = errorCode(error);
    if (["NETWORK_ERROR", "TRANSPORT_ERROR", "OPERATION_RUNNING", "OPERATION_NEEDS_RECOVERY", "OPERATION_RETAINED"].includes(code ?? "")) {
      const candidateId = `hub-cand-${attempt.requestId.replaceAll("-", "")}`;
      try {
        const runtime = await api.runtime(candidateId);
        if (runtime.projectId === source.projectId) {
          const job = runtime.jobs.find(row => row.candidateId === candidateId && row.proposalId === attempt.finalProposalId);
          if (job) attempt.accepted = { jobId: job.jobId, candidateId, status: job.status };
        }
      } catch { /* The original request failure remains the actionable error. */ }
    }
    if (!attempt.accepted) throw error;
  }
  return attempt.accepted;
}

/** The caller pairs this attempt with the same frozen snapshot, frame and base until it resolves. */
export function syncModelDraft(snapshot: DraftSnapshot, source: ModelDraftSource, frame: FrameDto,
  attempt: ModelDraftSyncAttempt, api: ModelDraftSyncApi): Promise<CandidateAcceptedDto | null> {
  if (attempt.inFlight) return attempt.inFlight;
  const pending = submit(snapshot, source, frame, attempt, api);
  attempt.inFlight = pending;
  void pending.finally(() => { delete attempt.inFlight; }).catch(() => {});
  return pending;
}
