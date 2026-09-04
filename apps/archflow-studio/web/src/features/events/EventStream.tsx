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
 */

import { useEffect, useRef, useState } from "react";

import { EVENTS_URL } from "../../api/client";
import type { StudioEventDto } from "../../api/generated";
import { BilingualText } from "../../i18n/BilingualText";
import type { MessageKey } from "../../i18n/messages.en";
import { useT, type MessageParameters } from "../../i18n/useT";

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

interface StreamLine {
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
  if (event.advance !== null && event.advance !== undefined) {
    parts.push(`advance=${event.advance}`);
  }
  if (event.blockedBy) {
    parts.push(`blockedBy=[${event.blockedBy.join(", ")}]`);
  }
  if (event.error) parts.push(`error=${event.error}`);
  return parts.join(" · ");
}

export function EventStream({
  notices,
  onCount,
}: {
  notices: readonly string[];
  /** How many lines the panel holds, for the tab that names it. */
  onCount?(count: number): void;
}) {
  const t = useT();
  const [lines, setLines] = useState<readonly StreamLine[]>([]);
  useEffect(() => {
    onCount?.(lines.length);
  }, [lines.length, onCount]);
  const lastSeqRef = useRef<number | null>(null);
  // Every seq shown on the current connection. A reconnect can replay them, and
  // a replayed line must not appear twice. Cleared on every open, and capped at
  // the same bound as the panel itself so a connection that lives for days does
  // not grow a set of numbers larger than the lines it is protecting.
  const seenSeqRef = useRef<Set<number>>(new Set());
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
    const source = new EventSource(EVENTS_URL);

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
    const remember = (seq: number) => {
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
      if (seenSeqRef.current.has(event.seq)) return;
      remember(event.seq);
      const previous = lastSeqRef.current;
      if (previous !== null && event.seq > previous + 1) {
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
        key: `seq:${connectionRef.current}:${event.seq}`,
        seq: event.seq,
        text: summarise(event),
        kind: "event",
      });
    };

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
  }, []);

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
