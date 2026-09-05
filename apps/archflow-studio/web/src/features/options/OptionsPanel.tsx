/**
 * Several massings side by side, with their numbers, and one of them chosen.
 *
 * The choice an architect makes at this stage is between shapes read as
 * quantities: how much ground, how much floor, how many storeys, how tall, and
 * how much of that the program asked for. Until this panel there was one
 * massing and no measurement of it, so the comparison happened in somebody's
 * head or in another tool.
 *
 * Every number here is the server's. The buttons send one deterministic
 * transform each — the studio computes no geometry in the browser — and
 * "select" starts a real candidate run, which is why it is the only button
 * here that says so. Nothing on this panel writes the authored record.
 */

import { useState, type ReactNode } from "react";

import type {
  MassingMetricsDto,
  MassingOptionDto,
  MassingOptionRequestDto,
  OptionsDto,
  VolumeDto,
  VolumesDto,
} from "../../api/generated";
import { ErrorPanel } from "../../app/ErrorPanel";
import type { Loadable } from "../../app/loadable";
import { BilingualText } from "../../i18n/BilingualText";
import { useT, type TFunction } from "../../i18n/useT";

/** One whole plan cell: the step every shift button moves a volume by. */
const STEP = 1;

/** What "widen" multiplies a volume's plan span by. */
const WIDEN = 1.25;

/** The shell's one way of shortening a server number for display. */
function num(value: number): string {
  return String(Number(value.toFixed(3)));
}

/** The cut a split button asks for: the first cell of the far half. */
export function midCell(volume: VolumeDto, axis: 0 | 2): number {
  const low = Math.ceil(volume.min[axis]);
  const high = Math.floor(volume.max[axis]);
  return Math.min(Math.max(low + 1, Math.round((low + high + 1) / 2)), high);
}

/** Whether a volume is wide enough on this axis to be cut in two at all. */
export function splittable(volume: VolumeDto, axis: 0 | 2): boolean {
  return Math.floor(volume.max[axis]) - Math.ceil(volume.min[axis]) >= 1;
}

function Metrics({ metrics, t }: { metrics: MassingMetricsDto; t: TFunction }) {
  return (
    <dl className="options__metrics">
      <div>
        <dt>{t("options.footprint")}</dt>
        <dd className="mono">{t("options.squareMetres", { n: num(metrics.footprintM2) })}</dd>
      </div>
      <div>
        <dt>{t("options.gfa")}</dt>
        <dd className="mono">
          {t("options.squareMetres", { n: num(metrics.grossFloorAreaM2) })}
        </dd>
      </div>
      <div>
        <dt>{t("options.floors")}</dt>
        <dd className="mono">{metrics.floorCount}</dd>
      </div>
      <div>
        <dt>{t("options.height")}</dt>
        <dd className="mono">{t("options.metres", { n: num(metrics.heightM) })}</dd>
      </div>
      <div>
        <dt>{t("options.efficiency")}</dt>
        <dd className="mono">
          {metrics.efficiency === null
            ? t("options.noEfficiency")
            : `${num(metrics.efficiency * 100)}%`}
        </dd>
      </div>
    </dl>
  );
}

function Card({
  title,
  subtitle,
  metrics,
  findings,
  honesty,
  action,
  t,
}: {
  title: string;
  subtitle: string;
  metrics: MassingMetricsDto;
  findings: MassingOptionDto["envelopeFindings"];
  honesty: readonly string[];
  action: React.ReactNode;
  t: TFunction;
}) {
  return (
    <li className="options__card">
      <div className="options__cardHead">
        <span className="options__cardTitle">{title}</span>
        <span className="quiet options__cardSubtitle mono">{subtitle}</span>
        {action}
      </div>
      <Metrics metrics={metrics} t={t} />
      {findings.length > 0 && (
        <ul className="options__findings">
          {findings.map((finding) => (
            <li key={`${finding.code}:${finding.subject ?? ""}:${finding.detail}`}>
              <span className="options__findingCode">{t(codeKey(finding.code))}</span>
              <BilingualText source={finding.detail} />
            </li>
          ))}
        </ul>
      )}
      {honesty.length > 0 && (
        <ul className="options__honesty">
          {honesty.map((line) => (
            <li key={line}>
              <BilingualText source={line} showSourceToggle />
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

const FINDING_KEYS = {
  volume_outside_envelope: "options.finding.outside",
  height_exceeded: "options.finding.height",
  far_exceeded: "options.finding.far",
} as const;

function codeKey(code: string) {
  return (
    (FINDING_KEYS as Record<string, (typeof FINDING_KEYS)[keyof typeof FINDING_KEYS] | undefined>)[
      code
    ] ?? "options.finding.other"
  );
}

export function OptionsPanel({
  table,
  volumes,
  stateDigest,
  busy,
  readOnlyReason,
  onMake,
  onSelect,
  onClose,
}: {
  table: Loadable<OptionsDto>;
  volumes: Loadable<VolumesDto>;
  /** The state the record projects now; null when the kernel refused its view. */
  stateDigest: string | null;
  /** An option or a selection is in flight; the buttons wait rather than queue. */
  busy: boolean;
  readOnlyReason?: ReactNode;
  onMake(body: MassingOptionRequestDto): void;
  onSelect(optionId: string): void;
  onClose(): void;
}) {
  const t = useT();
  const [volumeId, setVolumeId] = useState<string | null>(null);
  const rows = volumes.status === "ready" ? volumes.value.volumes : [];
  const chosen = rows.find((row) => row.volumeId === volumeId) ?? rows[0] ?? null;

  const make = (body: Omit<MassingOptionRequestDto, "stateDigest">) => {
    if (stateDigest === null) return;
    onMake({ ...body, stateDigest } as MassingOptionRequestDto);
  };
  const disabled = busy || stateDigest === null;

  return (
    <div className="options" role="dialog" aria-label={t("options.ariaLabel")}>
      <div className="options__head">
        <span className="options__title">{t("options.title")}</span>
        <span className="quiet options__note">{t("options.subtitle")}</span>
        <button type="button" className="btn btn--link options__close" onClick={onClose}>
          {t("common.close")}
        </button>
      </div>

      {readOnlyReason && <p className="options__note quiet">{readOnlyReason}</p>}
      {volumes.status === "failed" ? (
        <ErrorPanel error={volumes.error} what="GET /api/state/volumes" />
      ) : rows.length === 0 ? (
        <p className="options__note quiet">{t("options.noMassing")}</p>
      ) : (
        <div className="options__tools">
          <label className="options__volume">
            {t("options.volume")}
            <select
              value={chosen?.volumeId ?? ""}
              onChange={(event) => setVolumeId(event.target.value)}
            >
              {rows.map((row) => (
                <option key={row.volumeId} value={row.volumeId}>
                  {row.volumeId} · {t("options.squareMetres", { n: num(row.footprintM2) })}
                </option>
              ))}
            </select>
          </label>
          <button type="button" disabled={disabled} onClick={() => make({ transform: "add_floor" })}>
            {t("options.addFloor")}
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={() => make({ transform: "remove_floor" })}
          >
            {t("options.removeFloor")}
          </button>
          <button
            type="button"
            disabled={disabled || chosen === null}
            title={t("options.shiftTitle", { n: STEP, axis: "x" })}
            onClick={() =>
              chosen && make({ transform: "shift_volume", volumeId: chosen.volumeId, dx: STEP, dz: 0 })
            }
          >
            {t("options.shiftX", { n: STEP })}
          </button>
          <button
            type="button"
            disabled={disabled || chosen === null}
            title={t("options.shiftTitle", { n: STEP, axis: "z" })}
            onClick={() =>
              chosen && make({ transform: "shift_volume", volumeId: chosen.volumeId, dx: 0, dz: STEP })
            }
          >
            {t("options.shiftZ", { n: STEP })}
          </button>
          <button
            type="button"
            disabled={disabled || chosen === null}
            onClick={() =>
              chosen &&
              make({ transform: "scale_volume", volumeId: chosen.volumeId, sx: WIDEN, sz: 1 })
            }
          >
            {t("options.widen", { n: WIDEN })}
          </button>
          <button
            type="button"
            disabled={disabled || chosen === null || !splittable(chosen, 0)}
            title={t("options.splitTitle")}
            onClick={() =>
              chosen &&
              make({
                transform: "split_volume",
                volumeId: chosen.volumeId,
                along: "x",
                at: midCell(chosen, 0),
              })
            }
          >
            {t("options.split")}
          </button>
        </div>
      )}

      {table.status === "loading" || table.status === "idle" ? (
        <p className="options__note quiet">{t("options.loading")}</p>
      ) : table.status === "failed" ? (
        <ErrorPanel error={table.error} what="GET /api/options" />
      ) : (
        <ul className="options__cards">
          <Card
            title={t("options.baseline")}
            subtitle={t("options.baselineNote")}
            metrics={table.value.baseline}
            findings={[]}
            honesty={table.value.baseline.honesty}
            action={null}
            t={t}
          />
          {table.value.options.map((option) => (
            <Card
              key={option.optionId}
              title={option.label}
              subtitle={`${option.optionId} · ${option.transform}`}
              metrics={option.metrics}
              findings={option.envelopeFindings}
              honesty={option.honesty}
              action={
                <button
                  type="button"
                  className="btn btn--small options__select"
                  disabled={busy || option.stateDigest !== stateDigest}
                  title={
                    option.stateDigest === stateDigest
                      ? t("options.selectTitle")
                      : t("options.staleTitle")
                  }
                  onClick={() => onSelect(option.optionId)}
                >
                  {t("options.select")}
                </button>
              }
              t={t}
            />
          ))}
        </ul>
      )}
      {table.status === "ready" && table.value.options.length === 0 && (
        <p className="options__note quiet">{t("options.none")}</p>
      )}
    </div>
  );
}
