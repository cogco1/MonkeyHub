import { useRef } from "react";
import type { PlanDressingDto, PlanStatusDto, PlanVectorDto } from "../../api/generated";

const copy = {
  en: { title: "Drawing entourage", person: "Person", tree: "Tree", addPerson: "Add person", addTree: "Add tree", select: "Selected object", none: "Choose an object", anchor: "Position follows", fixed: "Fixed drawing coordinates", x: "Horizontal position / offset", y: "Vertical position / offset", size: "Symbol size", flip: "Mirror horizontally", remove: "Delete object", hint: "Drag symbols or use arrow keys. Save appearance to retain edits. These symbols do not change the building.", missing: "Anchor missing", outside: "Outside drawing crop", source: "Generate a cut plan first.", empty: "No entourage yet." },
  "zh-CN": { title: "图面配景", person: "人物", tree: "树木", addPerson: "添加人物", addTree: "添加树木", select: "选中配景", none: "选择配景对象", anchor: "位置跟随", fixed: "固定图面坐标", x: "水平位置／偏移", y: "竖向位置／偏移", size: "配景尺寸", flip: "水平翻转", remove: "删除配景", hint: "拖动配景或用方向键移动，保存表达后保留。这些配景不会修改建筑模型。", missing: "锚点缺失", outside: "超出图框", source: "请先生成一张剖切平面。", empty: "暂无配景。" },
} as const;
type Language = keyof typeof copy;
type Shared = { objects: PlanDressingDto[]; vector: PlanVectorDto; selected: string; onSelect(id: string): void; onChange(objects: PlanDressingDto[]): void; language: Language; crop: number[] };
export function dressingPosition(item: PlanDressingDto, vector: PlanVectorDto): [number, number] | null {
  const anchor = item.anchorObjectId ? vector.anchors.find(row => row.objectId === item.anchorObjectId) : null;
  if (item.anchorObjectId && !anchor) return null;
  return [item.positionUv[0] + (anchor?.positionUv[0] ?? 0), item.positionUv[1] + (anchor?.positionUv[1] ?? 0)];
}

export function DressingOverlay({ objects, vector, selected, onSelect, onChange, language, crop, disabled }: Shared & { disabled: boolean }) {
  const text = copy[language];
  const drag = useRef<{ id: string; x: number; y: number; start: number[] } | null>(null);
  const [x0, y0, x1, y1] = crop;
  return <svg className="drawing-dressing-overlay" viewBox={`${x0} ${-y1} ${x1-x0} ${y1-y0}`} aria-label={text.title}>
    {objects.map((item, index) => {
      const position = dressingPosition(item, vector), asset = vector.assets.find(asset => asset.id === item.assetId);
      if (!position || position.some(value => !Number.isFinite(value)) || !asset || !Number.isFinite(item.size) || item.size <= 0) return null;
      const [u, v] = position, name = `${item.assetId === "person-plan" ? text.person : text.tree} ${index+1}`;
      return <g key={item.id} data-dressing-id={item.id} className="drawing-dressing-object" tabIndex={disabled ? -1 : 0} role="button" aria-label={name} aria-pressed={item.id === selected}
        onPointerDown={event => { if (disabled) return; event.preventDefault(); onSelect(item.id); event.currentTarget.focus(); event.currentTarget.setPointerCapture(event.pointerId); drag.current = { id: item.id, x: event.clientX, y: event.clientY, start: item.positionUv }; }}
        onPointerMove={event => { if (disabled || drag.current?.id !== item.id) return; const box = event.currentTarget.ownerSVGElement!.getBoundingClientRect(); const current = drag.current;
          onChange(objects.map(row => row.id === item.id ? { ...row, positionUv: [current.start[0] + (event.clientX-current.x)/box.width*(x1-x0), current.start[1] - (event.clientY-current.y)/box.height*(y1-y0)] } : row)); }}
        onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}
        onKeyDown={event => { if (disabled) return; const step = item.size / (event.shiftKey ? 2 : 10); const directions: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
          if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); onChange(objects.filter(row => row.id !== item.id)); onSelect(""); }
          else if (directions[event.key]) { event.preventDefault(); onChange(objects.map(row => row.id === item.id ? { ...row, positionUv: [item.positionUv[0]+directions[event.key][0], item.positionUv[1]+directions[event.key][1]] } : row)); }
          else if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(item.id); } }}>
        <rect x={u-item.size/2} y={-v-item.size/2} width={item.size} height={item.size} fill="transparent" stroke={selected === item.id ? "var(--accent)" : "none"} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
        {asset.polylines.map((points, i) => <polyline key={i} points={points.map(([x,y]) => `${u+x*item.size*(item.flipped ? -1 : 1)},${-v-y*item.size}`).join(" ")} fill="none" stroke="#111" strokeWidth={1} vectorEffect="non-scaling-stroke" />)}
      </g>;
    })}
  </svg>;
}

export function DressingControls({ objects, vector, selected, onSelect, onChange, language, crop, disabled, unit, status }: Shared & { disabled: boolean; unit: string; status: PlanStatusDto | null }) {
  const text = copy[language], item = objects.find(row => row.id === selected);
  const factor = { meter: 1, millimeter: 1000, foot: 1/.3048, inch: 1/.0254 }[unit] ?? 1;
  const patch = (fields: Partial<PlanDressingDto>) => item && onChange(objects.map(row => row.id === item.id ? { ...row, ...fields } : row));
  function add(assetId: PlanDressingDto["assetId"]) {
    const id = crypto.randomUUID();
    onChange([...objects, { id, assetId, positionUv: [(crop[0]+crop[2])/2, (crop[1]+crop[3])/2], size: (assetId === "person-plan" ? .65 : 2)*factor, flipped: false, anchorObjectId: null }]); onSelect(id);
  }
  const warnings = objects.filter(row => {
    const pos = dressingPosition(row, vector);
    return !pos || pos[0]-row.size/2 < crop[0] || pos[0]+row.size/2 > crop[2] || pos[1]-row.size/2 < crop[1] || pos[1]+row.size/2 > crop[3] || status?.dressing?.some(read => read.id === row.id && read.status === "missing");
  });
  return <fieldset disabled={disabled}><legend>{text.title}</legend>
    <div className="drawing-dressing-actions"><button type="button" disabled={objects.length >= 100} onClick={() => add("person-plan")}>{text.addPerson}</button><button type="button" disabled={objects.length >= 100} onClick={() => add("tree-plan")}>{text.addTree}</button></div>
    <p>{text.hint}</p>
    <label className="drawing-field">{text.select}<select aria-label={text.select} value={item?.id ?? ""} onChange={event => onSelect(event.target.value)}><option value="">{text.none}</option>{objects.map((row,index) => <option key={row.id} value={row.id}>{row.assetId === "person-plan" ? text.person : text.tree} {index+1}</option>)}</select></label>
    {warnings.map(row => <p key={row.id} className="drawing-anchor-warning">{row.assetId === "person-plan" ? text.person : text.tree}: {!dressingPosition(row,vector) || status?.dressing?.some(read => read.id === row.id && read.status === "missing") ? text.missing : text.outside}</p>)}
    {item && <>
      <label className="drawing-field">{text.anchor}<select aria-label={text.anchor} value={item.anchorObjectId ?? ""} onChange={event => { const position = dressingPosition(item, vector) ?? item.positionUv; const anchor = vector.anchors.find(row => row.objectId === event.target.value); patch({ anchorObjectId: anchor?.objectId ?? null, positionUv: [position[0]-(anchor?.positionUv[0] ?? 0), position[1]-(anchor?.positionUv[1] ?? 0)] }); }}><option value="">{text.fixed}</option>{item.anchorObjectId && !vector.anchors.some(row => row.objectId === item.anchorObjectId) && <option value={item.anchorObjectId}>{text.missing}: {item.anchorObjectId}</option>}{vector.anchors.map(row => <option key={row.objectId} value={row.objectId}>{row.objectId}</option>)}</select></label>
      {[text.x, text.y].map((label,index) => <label className="drawing-field" key={index}>{label} ({unit})<input type="number" step="any" required value={Number.isFinite(item.positionUv[index]) ? item.positionUv[index] : ""} onChange={event => { const next: [number,number] = [...item.positionUv]; next[index] = event.currentTarget.valueAsNumber; patch({ positionUv: next }); }} /></label>)}
      <label className="drawing-field">{text.size} ({unit})<input type="number" min="0.000001" max="100000" step="any" required value={Number.isFinite(item.size) ? item.size : ""} onChange={event => patch({ size: event.currentTarget.valueAsNumber })} /></label>
      <div className="drawing-dressing-actions"><button type="button" aria-pressed={item.flipped ?? false} onClick={() => patch({ flipped: !item.flipped })}>{text.flip}</button><button type="button" onClick={() => { onChange(objects.filter(row => row.id !== item.id)); onSelect(""); }}>{text.remove}</button></div>
    </>}
  </fieldset>;
}
