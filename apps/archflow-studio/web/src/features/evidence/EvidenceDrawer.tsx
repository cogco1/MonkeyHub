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
import type { ServerIdentity } from "../../api/connection";
import type { EvidenceTab } from "../../app/evidence";
import type { MessageKey } from "../../i18n/messages.en";
import { usePreferences } from "../settings/preferences";
import { useT } from "../../i18n/useT";
import { EventStream } from "../events/EventStream";
import { HonestyTab } from "./HonestyTab";
import { ReceiptsTab } from "./ReceiptsTab";

const TABS: ReadonlyArray<{ id: EvidenceTab; title: MessageKey }> = [
  { id: "honesty", title: "evidence.tabs.honesty" },
  { id: "receipts", title: "evidence.tabs.receipts" },
  { id: "events", title: "evidence.tabs.events" },
];

export function EvidenceDrawer({
  open,
  pinned,
  tab,
  counts,
  server,
  projection,
  candidate,
  validation,
  sentence,
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
  /** The server this tab is connected to, as it named itself at the handshake. */
  server: ServerIdentity;
  projection: StateProjectionDto | null;
  candidate: CandidateDto | null;
  validation: ValidationDto | null;
  /** The sentence the shown candidate was made from, when this tab knows it. */
  sentence: string | null;
  notices: readonly string[];
  onTab(tab: EvidenceTab): void;
  onClose(): void;
  onPin(pinned: boolean): void;
  onEventCount(count: number): void;
}) {
  const t = useT();
  const { eventStreamVisible } = usePreferences();

  return (
    <aside
      className="drawer"
      data-open={String(open || pinned)}
      data-pinned={String(pinned)}
      aria-label={t("evidence.drawer.ariaLabel")}
      aria-hidden={!(open || pinned)}
    >
      <div className="drawer__head">
        <div>
          <p className="label">{t("nav.evidence")}</p>
          <p className="drawer__title">{t("evidence.drawer.title")}</p>
        </div>
        <div className="drawer__controls">
          <button
            type="button"
            className="btn btn--small"
            aria-pressed={pinned}
            onClick={() => onPin(!pinned)}
          >
            {pinned ? t("evidence.drawer.unpin") : t("evidence.drawer.pin")}
          </button>
          {!pinned && (
            <button
              type="button"
              className="icon-x"
              aria-label={t("evidence.drawer.close")}
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
            {t(item.title)}
            <span className="drawer__count mono">{counts[item.id]}</span>
          </button>
        ))}
      </div>
      <div className="drawer__body">
        <div hidden={tab !== "honesty"}>
          <HonestyTab
            server={server}
            projection={projection}
            candidate={candidate}
            validation={validation}
          />
        </div>
        <div hidden={tab !== "receipts"}>
          <ReceiptsTab candidate={candidate} validation={validation} sentence={sentence} />
        </div>
        <div hidden={tab !== "events" || !eventStreamVisible}>
          <EventStream notices={notices} onCount={onEventCount} />
        </div>
        {tab === "events" && !eventStreamVisible && (
          <p className="panel__note">
            {t("settings.fields.eventStreamVisible")} · {t("settings.options.hide")}
          </p>
        )}
      </div>
    </aside>
  );
}
