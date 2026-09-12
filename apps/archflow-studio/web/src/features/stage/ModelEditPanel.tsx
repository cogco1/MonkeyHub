import { useState } from "react";
import { usePreferences } from "../settings/preferences";
import "./ModelEditPanel.css";

export type DirectModelTool = "pushPull" | "move" | "rotate" | "scale" | "copy";
export type DirectModelAction =
  | { kind: "pushPull"; distance: number }
  | { kind: "move" | "copy"; translation: [number, number, number] }
  | { kind: "rotate"; angleDegrees: number; axis: [number, number, number] }
  | { kind: "scale"; scale: [number, number, number] };

/** Typed model actions; the server owns their geometry and exact-base checks. */
export function ModelEditPanel({ tool, subject, busy, error, onApply, onClose }: {
  tool: DirectModelTool;
  subject: string | null;
  busy: boolean;
  error: string | null;
  onApply(action: DirectModelAction): void;
  onClose(): void;
}) {
  const { language } = usePreferences();
  const zh = language === "zh-CN";
  const [values, setValues] = useState<[string, string, string]>(tool === "scale" ? ["1", "1", "1"] : ["1", "0", "0"]);
  const [axis, setAxis] = useState("z");
  const [uniform, setUniform] = useState(true);
  const titles = zh
    ? { pushPull: "推拉 P", move: "移动 M", rotate: "旋转 Q", scale: "缩放 S", copy: "复制" }
    : { pushPull: "Push/Pull P", move: "Move M", rotate: "Rotate Q", scale: "Scale S", copy: "Copy" };
  const vector = tool === "move" || tool === "copy" || (tool === "scale" && !uniform);
  const count = vector ? 3 : 1;
  const valid = values.slice(0, count).every((value) => value.trim() !== "" && Number.isFinite(Number(value))) &&
    (tool !== "scale" || values.slice(0, count).every((value) => Math.abs(Number(value)) > 1e-9)) &&
    (tool !== "pushPull" || Number(values[0]) !== 0);
  return <form className="model-edit-panel" aria-label={titles[tool]} onKeyDown={(event) => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); }
  }} onSubmit={(event) => {
    event.preventDefault();
    if (busy || !subject || !valid) return;
    const nums = values.map(Number) as [number, number, number];
    if (tool === "pushPull") onApply({ kind: tool, distance: nums[0] });
    else if (tool === "rotate") onApply({ kind: tool, angleDegrees: nums[0], axis: axis === "x" ? [1, 0, 0] : axis === "y" ? [0, 1, 0] : [0, 0, 1] });
    else if (tool === "scale") onApply({ kind: tool, scale: uniform ? [nums[0], nums[0], nums[0]] : nums });
    else onApply({ kind: tool, translation: nums });
  }}>
    <div className="model-edit-panel__heading"><strong>{titles[tool]}</strong><button type="button" aria-label={zh ? "关闭工具" : "Close tool"} onClick={onClose}>×</button></div>
    <p className="model-edit-panel__subject">{subject ?? (zh ? "先在模型中点击选择对象或面" : "Select an object or face in the model")}</p>
    <div className="model-edit-panel__fields">
      {Array.from({ length: count }, (_, index) => <label key={index}>
        {vector ? ["X", "Y", "Z"][index] : tool === "scale" ? (zh ? "倍率" : "Factor") : tool === "rotate" ? (zh ? "角度 °" : "Angle °") : (zh ? "距离 m" : "Distance m")}
        <input type="number" step="any" autoFocus={index === 0} value={values[index]} disabled={busy} onFocus={(event) => event.currentTarget.select()} onChange={(event) => setValues((current) => current.map((value, i) => i === index ? event.target.value : value) as [string, string, string])} />
      </label>)}
      {tool === "rotate" && <label>{zh ? "轴" : "Axis"}<select value={axis} onChange={(event) => setAxis(event.target.value)} disabled={busy}><option value="x">X</option><option value="y">Y</option><option value="z">Z</option></select></label>}
    </div>
    {tool === "scale" && <label className="model-edit-panel__uniform"><input type="checkbox" checked={uniform} disabled={busy} onChange={(event) => {
      if (!event.target.checked) setValues(([factor]) => [factor, factor, factor]);
      setUniform(event.target.checked);
    }} />{zh ? "等比缩放（取消后可沿轴缩放/镜像）" : "Uniform scale (uncheck for axis scale / mirror)"}</label>}
    <small>{tool === "pushPull"
      ? (zh ? "正值向选中面的外侧；支持绘制体各面与平面。" : "Positive extends outward from the selected face of a drawn solid or planar face.")
      : (zh ? "用于绘制体和平面，Z 向上。旋转、缩放以对象中心为基点。" : "For drawn solids and faces. Z is up; rotate/scale around the object centre.")}</small>
    {error && <p role="alert" className="model-edit-panel__error">{error}</p>}
    <button className="model-edit-panel__apply" type="submit" disabled={busy || !subject || !valid}>{busy ? (zh ? "正在生成模型…" : "Building model…") : (zh ? "应用" : "Apply")}</button>
  </form>;
}
