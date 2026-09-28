/**
 * What this API process is doing, as it does it.
 *
 * The stream is the browser's own unbounded `EventSource`, so reconnection and
 * `Last-Event-ID` resume are the platform's rather than this app's. Each frame
 * is named by its event type, and `EventSource` has no catch-all listener — so
 * the types below are registered by name. That list is a mirror of the server's
 * documented set, and a mirror can fall behind, so the panel watches `seq` for
 * gaps and says out loud when one appears instead of quietly showing fewer
 * events than the server sent. What it does *not* do is name the cause: a gap
 * is equally consistent with an unlistened name, a reconnect that resumed past
 * something, and a drop on the server, and this panel can tell none of the
 * three apart. Saying which one it was would be a guess dressed as a fact.
 *
 * A reconnect may replay frames this panel already showed, so an event whose
 * `seq` has been seen on *this connection* is dropped rather than appended
 * twice. The scope matters: `seq` is one API process's own counter and a
 * restarted process begins again at 1, so a dedupe that outlived the connection
 * would recognise the new process's first frames as ones it had already shown
 * and drop every one of them — the panel would go silent for as long as it took
 * the new process to climb past the old one's last number, and the gap detector,
 * sitting behind the dedupe, could not say so. So both the seen set and the
 * last-seq watermark are cleared when a connection opens, and a re-open after an
 * error says out loud that a frame may repeat and the numbering may restart.
 * Repeats are the price of never going silent, and this panel would rather show
 * a line twice than not show it at all.
 *
 * The reset happens on `open`, never before the request: the browser puts the
 * last id it saw in `Last-Event-ID` when it retries, which is how a process that
 * is still alive knows where to resume, and forgetting it early would ask a live
 * process to replay from the beginning.
 *
 * This is a live view of a process, not a transcript and not version history:
 * it is bounded, it is dropped on reload, and nothing here is a record of what
 * the project is.
 *
 * Inside the Hub the panel opens no stream of its own (#366): the Hub already
 * follows the project's stream and relays its events on the one stream the
 * page holds (`projectStores.onStudioEvent`), which keeps the project's latest
 * ones, so the panel opens with them as it opened with a replay before. Each
 * relayed event names the worker stream that numbered it, and a line is known
 * by `<stream>:<seq>`: a worker that restarted numbers from 1 again, and its
 * events are new ones, never repeats of the last worker's. The numbering has
 * holes by design - the project index's own announcements are not relayed as
 * lines - so there the panel names no gaps.
 */

import { useEffect, useRef, useState } from "react";

import { useConnection, useRuntimeKey } from "../../api/ProjectRuntimeContext";
import { projectStores } from "../../api/projectStore";
import type { StudioEventDto } from "../../api/generated";
import { BilingualText } from "../../i18n/BilingualText";
import type { MessageKey } from "../../../../src/i18n/messages.en";
import { useT, type MessageParameters } from "../../i18n/useT";

/** The event names the API documents on `StudioEventDto.type`. */
const EVENT_TYPES: readonly string[] = [
  "candidate.queued",
  "candidate.running",
  "candidate.succeeded",
  "candidate.failed",
  "validation.computed",
  "model_asset.registered",
  "working_copy.option_added",
];

/** How many frames the panel keeps. Older ones are dropped, not summarised. */
const KEEP = 200;

export interface StreamLine {
  readonly key: string;
  readonly seq: number | null;
  readonly text?: string;
  readonly messageKey?: MessageKey;
  readonly parameters?: MessageParameters;
  readonly kind: "event" | "gap" | "transport" | "notice";
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
  if (event.reviewReady !== null && event.reviewReady !== undefined) {
    parts.push(`reviewReady=${event.reviewReady}`);
  }
  if (event.blockedBy) {
    parts.push(`blockedBy=[${event.blockedBy.join(", ")}]`);
  }
  if (event.error) parts.push(`error=${event.error}`);
  return parts.join(" · ");
}

export function useStudioEvents(
  enabled: boolean,
  onEvent?: (event: StudioEventDto) => void,
): readonly StreamLine[] {
  const connection = useConnection();
  const runtimeKey = useRuntimeKey();
  const [lines, setLines] = useState<readonly StreamLine[]>([]);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const lastSeqRef = useRef<number | null>(null);
  // Every seq shown on the current connection. A reconnect can replay them, and
  // a replayed line must not appear twice. Cleared on every open, and capped at
  // the same bound as the panel itself so a connection that lives for days does
  // not grow a set of numbers larger than the lines it is protecting.
  const seenSeqRef = useRef<Set<number | string>>(new Set());
  // Which connection a line arrived on. Two processes can both publish a seq 1,
  // and after a restart both may be on screen at once, so the key that tells
  // them apart has to name the connection as well as the number.
  const connectionRef = useRef(0);
  // Whether this connection follows a drop. The browser reconnects on its own,
  // and only a re-open that follows an error is worth a line.
  const droppedRef = useRef(false);
  // Lines that carry no seq of their own still need to be told apart.
  const lineIdRef = useRef(0);

  useEffect(() => {
    if (!enabled) return;
    const relayed = runtimeKey !== null;

    const push = (line: StreamLine) => {
      setLines((current) => [...current, line].slice(-KEEP));
    };

    const note = (
      messageKey: MessageKey,
      kind: StreamLine["kind"],
      parameters?: MessageParameters,
    ) => {
      lineIdRef.current += 1;
      push({
        key: `${kind}:${lineIdRef.current}`,
        seq: null,
        messageKey,
        parameters,
        kind,
      });
    };

    // A Set iterates in insertion order, so the first entry is the oldest seq
    // this connection saw and is the one that goes when the set is full.
    const remember = (seq: number | string) => {
      const seen = seenSeqRef.current;
      seen.add(seq);
      while (seen.size > KEEP) {
        const oldest = seen.values().next();
        if (oldest.done) break;
        seen.delete(oldest.value);
      }
    };

    const receive = (message: MessageEvent<string>) => {
      let event: StudioEventDto;
      try {
        event = JSON.parse(message.data) as StudioEventDto;
      } catch {
        note(
          "evidence.events.parseFailure",
          "transport",
          { frame: message.data },
        );
        return;
      }
      // Relayed: `<stream>:<seq>`, which a restarted worker never repeats. Direct: this connection's seq.
      const stream = relayed ? (event as StudioEventDto & { stream?: string | null }).stream ?? "" : null;
      const seen = stream === null ? event.seq : `${stream}:${event.seq}`;
      if (seenSeqRef.current.has(seen)) return;
      remember(seen);
      const previous = lastSeqRef.current;
      if (!relayed && previous !== null && event.seq > previous + 1) {
        const missing = event.seq - previous - 1;
        note(
          missing === 1 ? "evidence.events.gapOne" : "evidence.events.gapMany",
          "gap",
          {
            count: missing,
            start: previous + 1,
            end: event.seq - 1,
          },
        );
      }
      if (previous === null || event.seq > previous) lastSeqRef.current = event.seq;
      push({
        key: stream === null ? `seq:${connectionRef.current}:${event.seq}` : `seq:${stream}:${event.seq}`,
        seq: event.seq,
        text: summarise(event),
        kind: "event",
      });
      onEventRef.current?.(event);
    };

    if (relayed) {
      connectionRef.current += 1;
      seenSeqRef.current = new Set();
      lastSeqRef.current = null;
      return projectStores.onStudioEvent(runtimeKey, (event) => {
        if (typeof event.type === "string" && EVENT_TYPES.includes(event.type)) {
          receive(new MessageEvent("message", { data: JSON.stringify(event) }));
        }
      });
    }
    const source = new EventSource(connection.url("/api/events"));
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, receive as EventListener);
    }
    source.onmessage = receive;
    // The connection is what `seq` is counted against, so opening one starts the
    // count over: this is the moment the panel stops recognising the previous
    // process's numbers as its own. `Last-Event-ID` has already gone out with the
    // request by now, so a process that is still alive still resumes where it
    // left off.
    source.onopen = () => {
      connectionRef.current += 1;
      seenSeqRef.current = new Set();
      lastSeqRef.current = null;
      if (droppedRef.current) {
        droppedRef.current = false;
        note("evidence.events.reconnected", "notice");
      }
    };
    source.onerror = () => {
      droppedRef.current = true;
      note(
        "evidence.events.dropped",
        "transport",
      );
    };

    return () => {
      for (const type of EVENT_TYPES) {
        source.removeEventListener(type, receive as EventListener);
      }
      source.close();
    };
  }, [enabled, connection, runtimeKey]);

  return lines;
}

export function EventStream({
  notices,
  lines,
}: {
  notices: readonly string[];
  lines: readonly StreamLine[];
}) {
  const t = useT();

  return (
    <div className="events">
      {notices.length > 0 && (
        <ul className="events__notices">
          {notices.map((notice, index) => (
            <li key={`${index}:${notice}`}>
              <BilingualText source={notice} showSourceToggle />
            </li>
          ))}
        </ul>
      )}
      {lines.length === 0 ? (
        <p className="panel__note">{t("evidence.events.empty")}</p>
      ) : (
        <ul className="events__lines mono">
          {lines.map((line) => (
            <li key={line.key} className={`events__line events__line--${line.kind}`}>
              <span className="events__seq">
                {line.seq === null ? "—" : line.seq}
              </span>
              <span>
                {line.messageKey === undefined
                  ? line.text
                  : t(line.messageKey, line.parameters)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
