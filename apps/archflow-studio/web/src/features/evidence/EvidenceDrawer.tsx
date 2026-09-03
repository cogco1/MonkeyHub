/**
 * Everything the server said, verbatim, one click away.
 *
 * Closed by default so the conversation carries the story; pinned, it stands
 * beside the stage as a third column and stays open across actions. The event
 * stream is mounted whatever tab is showing, so the connection it holds is
 * never dropped by a tab switch.
 */

import type {
  CandidateDto,
  StateProjectionDto,
  ValidationDto,
} from "../../api/generated";
import type { EvidenceTab } from "../../app/evidence";
import { EventStream } from "../events/EventStream";
import { HonestyTab } from "./HonestyTab";
import { ReceiptsTab } from "./ReceiptsTab";

const TABS: ReadonlyArray<{ id: EvidenceTab; title: string }> = [
  { id: "honesty", title: "Honesty" },
  { id: "receipts", title: "Receipts" },
  { id: "events", title: "Events" },
];

export function EvidenceDrawer({
  open,
  pinned,
  tab,
  counts,
  projection,
  candidate,
  validation,
  notices,
  onTab,
  onClose,
  onPin,
  onEventCount,
}: {
  open: boolean;
  pinned: boolean;
  tab: EvidenceTab;
  counts: { honesty: number; receipts: number; events: number };
  projection: StateProjectionDto | null;
  candidate: CandidateDto | null;
  validation: ValidationDto | null;
  notices: readonly string[];
  onTab(tab: EvidenceTab): void;
  onClose(): void;
  onPin(pinned: boolean): void;
  onEventCount(count: number): void;
}) {
  return (
    <aside
      className="drawer"
      data-open={String(open || pinned)}
      data-pinned={String(pinned)}
      aria-label="evidence"
      aria-hidden={!(open || pinned)}
    >
      <div className="drawer__head">
        <div>
          <p className="label">Evidence</p>
          <p className="drawer__title">Everything the server said, verbatim</p>
        </div>
        <div className="drawer__controls">
          <button
            type="button"
            className="btn btn--small"
            aria-pressed={pinned}
            onClick={() => onPin(!pinned)}
          >
            {pinned ? "unpin" : "pin"}
          </button>
          {!pinned && (
            <button
              type="button"
              className="icon-x"
              aria-label="close the evidence drawer"
              onClick={onClose}
            >
              ×
            </button>
          )}
        </div>
      </div>
      <div className="drawer__tabs" role="tablist">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            onClick={() => onTab(item.id)}
          >
            {item.title}
            <span className="drawer__count mono">{counts[item.id]}</span>
          </button>
        ))}
      </div>
      <div className="drawer__body">
        <div hidden={tab !== "honesty"}>
          <HonestyTab
            projection={projection}
            candidate={candidate}
            validation={validation}
          />
        </div>
        <div hidden={tab !== "receipts"}>
          <ReceiptsTab candidate={candidate} validation={validation} />
        </div>
        <div hidden={tab !== "events"}>
          <EventStream notices={notices} onCount={onEventCount} />
        </div>
      </div>
    </aside>
  );
}
