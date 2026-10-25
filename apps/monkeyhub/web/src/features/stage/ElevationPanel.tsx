import { useState } from "react";
import { useDeveloperMode, usePreferences } from "../settings/preferences";
import { elementWords } from "./elementWords";
import { elevationOf, type DraftObject, type ElevationCommand, type ElevationDatum, type ElevationReference } from "./modelDraft";
import "./ElevationPanel.css";

export interface ElevationControls {
  object: DraftObject;
  objects: readonly DraftObject[];
  levels: readonly ElevationDatum[];
  onApply(command: ElevationCommand): boolean;
}

/** Model units are metres; the panel shows and takes whole millimetres, which is how an architect reads a level. */
const toMm = (metres: number) => Math.round(metres * 1000);
const fromMm = (millimetres: number) => millimetres / 1000;


/** A selected mass's vertical controls; all writes enter the same local draft history. */
export function ElevationPanel({ controls, busy, error, onReference }: {
  controls: ElevationControls; busy: boolean; error: string | null;
  onReference(elevation: number | null): void;
}) {
  const { language } = usePreferences(), zh = language === "zh-CN", developer = useDeveloperMode();
  const { object, objects, levels, onApply } = controls, facts = elevationOf(object);
  const [datumId, setDatumId] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  if (!facts) return null;
  const locked = object.parameterBoundFields?.some(field => field === "height" || field === "work_plane");
  const blocked = busy || locked;
  const apply = (command: Omit<ElevationCommand, "kind" | "elementId">) => {
    setLocalError(null); return onApply({ ...command, kind: "elevation", elementId: object.elementId });
  };
  // A drawn mass by its number; another element by what it is, numbered among its kind. The id is for developers.
  const label = (row: DraftObject) => {
    if (row.elementId.startsWith("drawn-")) return `${zh ? "体块" : "Mass"} ${objects.indexOf(row) + 1}`;
    if (developer) return row.elementId;
    const words = elementWords(row.elementId, zh);
    const same = objects.filter(other => !other.elementId.startsWith("drawn-") && elementWords(other.elementId, zh) === words);
    return same.length > 1 ? `${words} ${same.indexOf(row) + 1}` : words;
  };
  const references = [
    ...levels.map(level => ({ key: `level:${level.levelId}`, label: `${level.name} · ${toMm(level.elevation)} mm`,
      reference: { kind: "level", id: level.levelId, offset: 0 } as ElevationReference, value: level.elevation })),
    ...objects.filter(row => row.elementId !== object.elementId && elevationOf(row)).map(row => ({
      key: `element-top:${row.elementId}`, label: `${label(row)} · ${zh ? "顶部" : "top"} ${toMm(elevationOf(row)!.top)} mm`,
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
    {reference && <small>{zh ? "偏移" : "Offset"} {reference.offset >= 0 ? "+" : ""}{toMm(reference.offset)} mm</small>}
  </label>;
  const selectedDatum = levels.find(level => level.levelId === datumId);
  return <aside className="elevation-panel" aria-label={zh ? "体块标高" : "Mass elevation"}
    onKeyDown={event => event.stopPropagation()} onPointerDown={event => event.stopPropagation()}>
    <strong>{zh ? "标高" : "Elevation"} <span className="quiet">mm</span></strong>
    <div className="elevation-panel__numbers">
      {(["base", "top", "height"] as const).map(field => <form key={`${object.elementId}:${field}:${facts[field]}`}
        onSubmit={event => {
          event.preventDefault();
          const value = new FormData(event.currentTarget).get("value"), number = Number(value);
          if (typeof value !== "string" || !value.trim() || !Number.isFinite(number)) { setLocalError(zh ? "请输入有效数值。" : "Enter a valid number."); return; }
          if (number === toMm(facts[field])) return;  // unchanged: the shown value is rounded, so applying it would move the mass
          if (!blocked) apply({ action: field === "base" ? "set-base" : field === "top" ? "set-top" : "set-height", value: fromMm(number) });
        }}>
        <label>{field === "base" ? (zh ? "底标高" : "Base Z") : field === "top" ? (zh ? "顶标高" : "Top Z") : (zh ? "高度" : "Height")}
          <input name="value" aria-label={field === "base" ? "Base Z" : field === "top" ? "Top Z" : "Height"}
            type="number" step="any" required defaultValue={toMm(facts[field])} disabled={blocked}
            onFocus={event => { event.currentTarget.select(); onReference(field === "height" ? facts.top : facts[field]); }}
            onBlur={() => onReference(null)} />
        </label>
        <button data-command="frame.set_level.apply" data-command-arg={field} type="submit" disabled={blocked} aria-label={`${zh ? "应用" : "Apply"} ${field}`}>{zh ? "应用" : "Apply"}</button>
      </form>)}
    </div>
    {referenceInput("base", facts.baseReference)}
    {referenceInput("top", facts.topReference)}
    <small>{zh ? "输入数值保留当前引用并调整偏移。" : "Numeric edits keep the reference and adjust its offset."}</small>
    <details>
      <summary data-command="view.disclosure.toggle" data-command-arg="reference_elevations">{zh ? "参考标高" : "Reference elevations"}</summary>
      <label>{zh ? "基准" : "Datum"}<select aria-label="Datum" value={datumId} disabled={busy}
        onChange={event => { setDatumId(event.target.value); onReference(levels.find(row => row.levelId === event.target.value)?.elevation ?? null); }}>
        <option value="">{zh ? "新建基准" : "New datum"}</option>
        {levels.map(level => <option key={level.levelId} value={level.levelId}>{level.name}</option>)}
      </select></label>
      <form key={`${datumId}:${selectedDatum?.name}:${selectedDatum?.elevation}`} onSubmit={event => {
        event.preventDefault(); const data = new FormData(event.currentTarget);
        const name = String(data.get("name") ?? "").trim(), raw = String(data.get("elevation") ?? ""), value = fromMm(Number(raw));
        if (!name || !raw.trim() || !Number.isFinite(value)) { setLocalError(zh ? "请输入名称和有效标高。" : "Enter a name and a valid elevation."); return; }
        const levelId = datumId || `datum-${crypto.randomUUID()}`;
        if (apply({ action: "set-datum", levelId, name, value })) { setDatumId(levelId); onReference(value); }
      }}>
        <label>{zh ? "名称" : "Name"}<input name="name" required maxLength={120} aria-label="Datum name" defaultValue={selectedDatum?.name ?? ""} disabled={busy} /></label>
        <label>{zh ? "标高 mm" : "Elevation mm"}<input name="elevation" required type="number" step="any" aria-label="Datum elevation"
          defaultValue={toMm(selectedDatum?.elevation ?? facts.base)} disabled={busy} onFocus={() => onReference(selectedDatum?.elevation ?? facts.base)} /></label>
        <button data-command="frame.set_level.datum" type="submit" disabled={busy}>{datumId ? (zh ? "更新基准" : "Update datum") : (zh ? "创建基准" : "Create datum")}</button>
      </form>
    </details>
    {locked && <small>{zh ? "此体块使用参数控制，请通过已有参数调整。" : "This mass uses parameter controls; adjust its existing parameters."}</small>}
    {(localError || error) && <p role="alert">{localError || error}</p>}
  </aside>;
}
