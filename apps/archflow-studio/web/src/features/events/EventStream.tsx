/**
 * What this API process is doing, as it does it.
 *
 * The stream is the browser's own unbounded `EventSource`, so reconnection and
 * `Last-Event-ID` resume are the platform's rather than this app's. Each frame
 * is named by its event type, and `EventSource` has no catch-all listener — so
 * the types below are registered by name. That list is a mirror of the server's
 * documented set, and a mirror can fall behind, so the panel watches `seq` for
 * gaps and says out loud when one appears instead of quietly showing fewer
 * events than the server sent.
 *
 * This is a live view of a process, not a transcript and not version history:
 * it is bounded, it is dropped on reload, and nothing here is a record of what
 * the project is.
 */

import { useEffect, useRef, useState } from "react";

import { EVENTS_URL } from "../../api/client";
import type { StudioEventDto } from "../../api/generated";

/** The event names the API documents on `StudioEventDto.type`. */
const EVENT_TYPES: readonly string[] = [
  "candidate.queued",
  "candidate.running",
  "candidate.succeeded",
  "candidate.failed",
  "validation.computed",
];

/** How many frames the panel keeps. Older ones are dropped, not summarised. */
const KEEP = 200;

export interface StreamLine {
  readonly key: string;
  readonly seq: number | null;
  readonly text: string;
  readonly kind: "event" | "gap" | "transport";
}

function summarise(event: StudioEventDto): string {
  const parts: string[] = [event.at, event.type];
  if (event.candidateId) parts.push(`candidate=${event.candidateId}`);
  if (event.jobId) parts.push(`job=${event.jobId}`);
  if (event.proposalId) parts.push(`proposal=${event.proposalId}`);
  if (event.runId) parts.push(`run=${event.runId}`);
  if (event.wallTimeS !== null && event.wallTimeS !== undefined) {
    parts.push(`wall=${event.wallTimeS}s`);
  }
  if (event.advance !== null && event.advance !== undefined) {
    parts.push(`advance=${event.advance}`);
  }
  if (event.blockedBy) {
    parts.push(`blockedBy=[${event.blockedBy.join(", ")}]`);
  }
  if (event.error) parts.push(`error=${event.error}`);
  return parts.join(" · ");
}

export function EventStream({ notices }: { notices: readonly string[] }) {
  const [lines, setLines] = useState<readonly StreamLine[]>([]);
  const lastSeqRef = useRef<number | null>(null);

  useEffect(() => {
    const source = new EventSource(EVENTS_URL);

    const push = (line: StreamLine) => {
      setLines((current) => [...current, line].slice(-KEEP));
    };

    const receive = (message: MessageEvent<string>) => {
      let event: StudioEventDto;
      try {
        event = JSON.parse(message.data) as StudioEventDto;
      } catch {
        push({
          key: `bad:${message.lastEventId}:${Date.now()}`,
          seq: null,
          text: `a frame arrived that this client could not parse: ${message.data}`,
          kind: "transport",
        });
        return;
      }
      const previous = lastSeqRef.current;
      if (previous !== null && event.seq > previous + 1) {
        push({
          key: `gap:${previous}:${event.seq}`,
          seq: null,
          text:
            `seq ${previous + 1}…${event.seq - 1} arrived under an event name ` +
            "this panel does not listen for and were not shown",
          kind: "gap",
        });
      }
      lastSeqRef.current = event.seq;
      push({
        key: `seq:${event.seq}`,
        seq: event.seq,
        text: summarise(event),
        kind: "event",
      });
    };

    for (const type of EVENT_TYPES) {
      source.addEventListener(type, receive as EventListener);
    }
    source.onmessage = receive;
    source.onerror = () => {
      push({
        key: `transport:${Date.now()}`,
        seq: null,
        text:
          "the event stream dropped; the browser will retry. Nothing about the " +
          "project changed because of this.",
        kind: "transport",
      });
    };

    return () => {
      for (const type of EVENT_TYPES) {
        source.removeEventListener(type, receive as EventListener);
      }
      source.close();
    };
  }, []);

  return (
    <div className="events">
      {notices.length > 0 && (
        <ul className="events__notices">
          {notices.map((notice, index) => (
            <li key={`${index}:${notice}`}>{notice}</li>
          ))}
        </ul>
      )}
      {lines.length === 0 ? (
        <p className="panel__note">
          the stream is open and this process has published nothing yet.
        </p>
      ) : (
        <ul className="events__lines mono">
          {lines.map((line) => (
            <li key={line.key} className={`events__line events__line--${line.kind}`}>
              <span className="events__seq">
                {line.seq === null ? "—" : line.seq}
              </span>
              <span>{line.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
