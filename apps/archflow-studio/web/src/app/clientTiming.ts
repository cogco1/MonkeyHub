import { studio, type OperationTrace } from "../api/client";
import type { ClientTimingDetailsDto, ClientTimingDto } from "../api/generated";

export type TimingBinding = Pick<ClientTimingDto, "projectId" | "runId" | "sourceRef">;
export type TimingStatus = Exclude<ClientTimingDto["status"], "running">;

/** One measured browser interval; telemetry delivery never blocks the UI. */
export function startClientTiming(
  phase: ClientTimingDto["phase"], binding: TimingBinding,
  parent?: OperationTrace, details: ClientTimingDetailsDto = {},
) {
  const eventId = crypto.randomUUID();
  const operationId = parent?.operationId ?? crypto.randomUUID();
  const started = performance.now();
  const body: ClientTimingDto = { ...binding, eventId, operationId,
    parentEventId: parent?.parentEventId, phase, startedAt: new Date().toISOString(),
    status: "running", details };
  let closed = false;
  let elapsed = 0;
  // Keep a fast final record from being overwritten by a slower initial write.
  let delivery = Promise.resolve();
  const report = (record: ClientTimingDto) => {
    delivery = delivery.then(() => studio.recordClientTiming(record)).then(() => undefined, () => undefined);
  };
  report(body);
  return {
    binding, trace: { operationId, parentEventId: eventId },
    get closed() { return closed; },
    finish(status: TimingStatus, extra: ClientTimingDetailsDto = {}) {
      if (closed) return elapsed;
      closed = true;
      elapsed = Math.round(performance.now() - started);
      report({ ...body, status, endedAt: new Date().toISOString(), durationMs: elapsed,
        details: { ...details, ...extra } });
      return elapsed;
    },
  };
}
export type ClientTimingSpan = ReturnType<typeof startClientTiming>;

export interface EditTimingTicket {
  root: ClientTimingSpan;
  intent?: ClientTimingSpan;
  candidate?: ClientTimingSpan;
  intentFinishedAt?: number;
  intentMs: number;
  betweenActionsMs: number;
}

export function finishEditTiming(ticket: EditTimingTicket | null | undefined, status: TimingStatus) {
  if (!ticket || ticket.root.closed) return;
  if (ticket.intent && !ticket.intent.closed) ticket.intentMs = ticket.intent.finish(status);
  if (ticket.intentFinishedAt !== undefined && !ticket.candidate) {
    ticket.betweenActionsMs = Math.round(performance.now() - ticket.intentFinishedAt);
  }
  ticket.root.finish(status, {
    active_wait_ms: ticket.intentMs + (ticket.candidate?.finish(status) ?? 0),
    between_actions_ms: ticket.betweenActionsMs,
  });
}
