import { useState } from "react";
import { usePreferences } from "../settings/preferences";
import { elevationOf, type DraftObject, type ElevationCommand, type ElevationDatum, type ElevationReference } from "./modelDraft";
import "./ElevationPanel.css";

export interface ElevationControls {
  object: DraftObject;
  objects: readonly DraftObject[];
  levels: readonly ElevationDatum[];
  onApply(command: ElevationCommand): boolean;
}

/** A selected mass's vertical controls; all writes enter the same local draft history. */
export function ElevationPanel({ controls, busy, error, onReference }: {
  controls: ElevationControls; busy: boolean; error: string | null;
  onReference(elevation: number | null): void;
}) {
  const { language } = usePreferences(), zh = language === "zh-CN";
  const { object, objects, levels, onApply } = controls, facts = elevationOf(object);
  const [datumId, setDatumId] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  if (!facts) return null;
  const locked = object.parameterBoundFields?.some(field => field === "height" || field === "work_plane");
  const blocked = busy || locked;
  const apply = (command: Omit<ElevationCommand, "kind" | "elementId">) => {
    setLocalError(null); return onApply({ ...command, kind: "elevation", elementId: object.elementId });
  };
  const label = (row: DraftObject) => row.elementId.startsWith("drawn-")
    ? `${zh ? "体块" : "Mass"} ${objects.indexOf(row) + 1}` : row.elementId;
  const references = [
    ...levels.map(level => ({ key: `level:${level.levelId}`, label: `${level.name} · ${level.elevation.toFixed(3)} m`,
      reference: { kind: "level", id: level.levelId, offset: 0 } as ElevationReference, value: level.elevation })),
    ...objects.filter(row => row.elementId !== object.elementId && elevationOf(row)).map(row => ({
      key: `element-top:${row.elementId}`, label: `${label(row)} · ${zh ? "顶部" : "top"} ${elevationOf(row)!.top.toFixed(3)} m`,
      reference: { kind: "element-top", id: row.elementId, offset: 0 } as ElevationReference, value: elevationOf(row)!.top })),
  ];
  const referenceInput = (side: "base" | "top", reference: ElevationReference | null) => <label className="elevation-panel__reference">
    <span>{side === "base" ? (zh ? "底部跟随" : "Base follows") : (zh ? "顶部跟随" : "Top follows")}</span>
    <select aria-label={side === "base" ? "Base reference" : "Top reference"} disabled={blocked}
      value={reference ? `${reference.kind}:${reference.id}` : ""}
      onFocus={() => onReference(facts[side])} onBlur={() => onReference(null)}
      onChange={event => {
        const selected = references.find(row => row.key === event.target.value);
        if (selected) { onReference(selected.value); apply({ action: side === "base" ? "bind-base" : "bind-top", reference: selected.reference }); }
        else apply({ action: side === "base" ? "detach-base" : "detach-top" });
      }}>
      <option value="">{side === "base" ? (zh ? "自由 · 绝对标高" : "Free · absolute Z") : (zh ? "由高度确定" : "From height")}</option>
      {references.map(row => <option key={row.key} value={row.key}>{row.label}</option>)}
    </select>
    {reference && <small>{zh ? "偏移" : "Offset"} {reference.offset >= 0 ? "+" : ""}{reference.offset.toFixed(3)} m</small>}
  </label>;
  const selectedDatum = levels.find(level => level.levelId === datumId);
  return <aside className="elevation-panel" aria-label={zh ? "体块标高" : "Mass elevation"}
    onKeyDown={event => event.stopPropagation()} onPointerDown={event => event.stopPropagation()}>
    <strong>{zh ? "标高" : "Elevation"} <span className="quiet">m</span></strong>
    <div className="elevation-panel__numbers">
      {(["base", "top", "height"] as const).map(field => <form key={`${object.elementId}:${field}:${facts[field]}`}
        onSubmit={event => {
          event.preventDefault();
          const value = new FormData(event.currentTarget).get("value"), number = Number(value);
          if (typeof value !== "string" || !value.trim() || !Number.isFinite(number)) { setLocalError(zh ? "请输入有效数值。" : "Enter a valid number."); return; }
          if (!blocked) apply({ action: field === "base" ? "set-base" : field === "top" ? "set-top" : "set-height", value: number });
        }}>
        <label>{field === "base" ? (zh ? "底标高" : "Base Z") : field === "top" ? (zh ? "顶标高" : "Top Z") : (zh ? "高度" : "Height")}
          <input name="value" aria-label={field === "base" ? "Base Z" : field === "top" ? "Top Z" : "Height"}
            type="number" step="any" required defaultValue={Number(facts[field].toFixed(6))} disabled={blocked}
            onFocus={event => { event.currentTarget.select(); onReference(field === "height" ? facts.top : facts[field]); }}
            onBlur={() => onReference(null)} />
        </label>
        <button type="submit" disabled={blocked} aria-label={`${zh ? "应用" : "Apply"} ${field}`}>{zh ? "应用" : "Apply"}</button>
      </form>)}
    </div>
    {referenceInput("base", facts.baseReference)}
    {referenceInput("top", facts.topReference)}
    <small>{zh ? "输入数值保留当前引用并调整偏移。" : "Numeric edits keep the reference and adjust its offset."}</small>
    <details>
      <summary>{zh ? "参考标高" : "Reference elevations"}</summary>
      <label>{zh ? "基准" : "Datum"}<select aria-label="Datum" value={datumId} disabled={busy}
        onChange={event => { setDatumId(event.target.value); onReference(levels.find(row => row.levelId === event.target.value)?.elevation ?? null); }}>
        <option value="">{zh ? "新建基准" : "New datum"}</option>
        {levels.map(level => <option key={level.levelId} value={level.levelId}>{level.name}</option>)}
      </select></label>
      <form key={`${datumId}:${selectedDatum?.name}:${selectedDatum?.elevation}`} onSubmit={event => {
        event.preventDefault(); const data = new FormData(event.currentTarget);
        const name = String(data.get("name") ?? "").trim(), raw = String(data.get("elevation") ?? ""), value = Number(raw);
        if (!name || !raw.trim() || !Number.isFinite(value)) { setLocalError(zh ? "请输入名称和有效标高。" : "Enter a name and a valid elevation."); return; }
        const levelId = datumId || `datum-${crypto.randomUUID()}`;
        if (apply({ action: "set-datum", levelId, name, value })) { setDatumId(levelId); onReference(value); }
      }}>
        <label>{zh ? "名称" : "Name"}<input name="name" required maxLength={120} aria-label="Datum name" defaultValue={selectedDatum?.name ?? ""} disabled={busy} /></label>
        <label>{zh ? "标高 m" : "Elevation m"}<input name="elevation" required type="number" step="any" aria-label="Datum elevation"
          defaultValue={selectedDatum?.elevation ?? facts.base} disabled={busy} onFocus={() => onReference(selectedDatum?.elevation ?? facts.base)} /></label>
        <button type="submit" disabled={busy}>{datumId ? (zh ? "更新基准" : "Update datum") : (zh ? "创建基准" : "Create datum")}</button>
      </form>
    </details>
    {locked && <small>{zh ? "此体块使用参数控制，请通过已有参数调整。" : "This mass uses parameter controls; adjust its existing parameters."}</small>}
    {(localError || error) && <p role="alert">{localError || error}</p>}
  </aside>;
}
