import { useRef, type RefObject } from "react";
import { usePreferences } from "../settings/preferences";
import type { PushPullTarget, ScaleMode } from "../../workspaces/monkeyarch/interactionSession";
import type { SketchVector } from "./sketch";
import { ModelToolButton } from "./ModelToolButton";
import "./ModelEditPanel.css";
import { TRANSLATION_CONSTRAINTS, isTranslationConstraint, type TranslationConstraint } from "../../workspaces/monkeyarch/viewer/translationGizmo";

export type DirectModelTool = "pushPull" | "move" | "rotate" | "scale" | "copy";
export type DirectModelAction =
  | { kind: "pushPull"; distance: number; normal?: SketchVector; target?: PushPullTarget }
  | { kind: "move" | "copy"; translation: [number, number, number]; target?: PushPullTarget }
  | { kind: "rotate"; angleDegrees: number; axis: [number, number, number]; target?: PushPullTarget }
  | { kind: "scale"; scale: [number, number, number]; target?: PushPullTarget };

/** Typed model actions; the server owns their geometry and exact-base checks. */
export function ModelEditPanel({ tool, subject, busy, error, onApply, onClose, pushPull, move, rotate, scale }: {
  tool: DirectModelTool;
  subject: string | null;
  busy: boolean;
  error: string | null;
  onApply(action: DirectModelAction): void;
  onClose(): void;
  pushPull?: {
    inputRef: RefObject<HTMLInputElement | null>;
    active: boolean;
    hint: string;
    onChange(value: string): void;
    onCommit(distance: number): void;
  };
  move: {
    constraint: TranslationConstraint | null;
    onConstraint(constraint: TranslationConstraint): void;
    inputs: readonly RefObject<HTMLInputElement | null>[];
    hint: string;
    onChange(index: number, value: string): void;
    onCommit(): void;
  };
  rotate: {
    inputRef: RefObject<HTMLInputElement | null>;
    axis: "x" | "y" | "z";
    hint: string;
    onAxis(axis: "x" | "y" | "z"): void;
    onChange(value: string): void;
    onCommit(): void;
  };
  scale: {
    inputs: readonly RefObject<HTMLInputElement | null>[];
    values: readonly string[];
    mode: ScaleMode;
    hint: string;
    onMode(mode: ScaleMode): void;
    onChange(index: number, value: string): void;
    onCommit(): void;
  };
}) {
  const { language } = usePreferences();
  const zh = language === "zh-CN";
  const fallbackInput = useRef<HTMLInputElement>(null);
  const titles = zh
    ? { pushPull: "推拉 P", move: "移动 M", rotate: "旋转 Q", scale: "缩放 S", copy: "复制" }
    : { pushPull: "Push/Pull P", move: "Move M", rotate: "Rotate Q", scale: "Scale S", copy: "Copy" };
  if (tool === "scale") return <form className="model-edit-panel model-edit-panel--pushpull model-edit-panel--move" aria-label={titles[tool]}
    onKeyDown={(event) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); }
    }} onSubmit={(event) => { event.preventDefault(); if (!busy && subject) scale.onCommit(); }}>
    <div className="model-edit-panel__distance">
      <ModelToolButton icon="help" label={scale.hint} />
      <select aria-label="Scale axes" value={scale.mode} disabled={busy} onChange={(event) => scale.onMode(event.target.value as ScaleMode)}>
        <option value="uniform">XYZ</option><option value="x">X</option><option value="y">Y</option><option value="z">Z</option>
      </select>
      {scale.inputs.slice(0, scale.mode === "uniform" ? 1 : 3).map((input, index) => <label key={index}>
        {scale.mode !== "uniform" && <span className="quiet">{["X", "Y", "Z"][index]}</span>}
        <input ref={input} aria-label={scale.mode === "uniform" ? (zh ? "等比倍率" : "Uniform factor") : `${["X", "Y", "Z"][index]} factor`}
          type="number" step="any" defaultValue={scale.values[index] ?? "1"} disabled={busy}
          onFocus={(event) => event.currentTarget.select()} onChange={(event) => scale.onChange(index, event.target.value)} />
      </label>)}
      <span className="quiet" aria-hidden="true">×</span>
      <ModelToolButton icon="check" label={zh ? "应用" : "Apply"} shortcut="Enter" type="submit" disabled={busy || !subject} />
      <ModelToolButton icon="close" label={zh ? "关闭工具" : "Close tool"} shortcut="Esc" onClick={onClose} />
    </div>
    {error && <p role="alert" className="model-edit-panel__error">{error}</p>}
  </form>;
  if (tool === "rotate") return <form className="model-edit-panel model-edit-panel--pushpull" aria-label={titles[tool]}
    onKeyDown={(event) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); }
    }} onSubmit={(event) => { event.preventDefault(); if (!busy && subject) rotate.onCommit(); }}>
    <div className="model-edit-panel__distance">
      <ModelToolButton icon="help" label={rotate.hint} />
      <input ref={rotate.inputRef} aria-label={zh ? "角度 °" : "Angle °"} type="number" step="any" defaultValue="0" disabled={busy}
        onFocus={(event) => event.currentTarget.select()} onChange={(event) => rotate.onChange(event.target.value)} />
      <span className="quiet" aria-hidden="true">°</span>
      <select aria-label="Rotation axis" value={rotate.axis} disabled={busy} onChange={(event) => rotate.onAxis(event.target.value as "x" | "y" | "z")}>
        <option value="x">X</option><option value="y">Y</option><option value="z">Z</option>
      </select>
      <ModelToolButton icon="check" label={zh ? "应用" : "Apply"} shortcut="Enter" type="submit" disabled={busy || !subject} />
      <ModelToolButton icon="close" label={zh ? "关闭工具" : "Close tool"} shortcut="Esc" onClick={onClose} />
    </div>
    {error && <p role="alert" className="model-edit-panel__error">{error}</p>}
  </form>;
  if (tool === "move" || tool === "copy") return <form className="model-edit-panel model-edit-panel--pushpull model-edit-panel--move" aria-label={titles[tool]}
    onKeyDown={(event) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); }
    }} onSubmit={(event) => { event.preventDefault(); if (!busy && subject) move.onCommit(); }}>
    <div className="model-edit-panel__distance">
      <ModelToolButton icon="help" label={move.hint} />
      <select aria-label={zh ? "世界坐标约束" : "World constraint"} value={move.constraint ?? ""} disabled={busy}
        onChange={(event) => { if (isTranslationConstraint(event.target.value)) move.onConstraint(event.target.value); }}>
        <option value="" disabled>{zh ? "选择轴/平面" : "Axis / plane"}</option>
        {TRANSLATION_CONSTRAINTS.map(axis => <option key={axis} value={axis}>{axis}</option>)}
      </select>
      {move.inputs.map((input, index) => <label key={index}>
        <span className="quiet">{["X", "Y", "Z"][index]}</span>
        <input ref={input} aria-label={`${["X", "Y", "Z"][index]} m`} type="number" step="any" defaultValue="0" disabled={busy || !move.constraint?.includes("XYZ"[index]!)}
          onFocus={(event) => event.currentTarget.select()} onChange={(event) => move.onChange(index, event.target.value)} />
      </label>)}
      <span className="quiet" aria-hidden="true">m</span>
      <ModelToolButton icon="check" label={zh ? "应用" : "Apply"} shortcut="Enter" type="submit" disabled={busy || !subject} />
      <ModelToolButton icon="close" label={zh ? "关闭工具" : "Close tool"} shortcut="Esc" onClick={onClose} />
    </div>
    {error && <p role="alert" className="model-edit-panel__error">{error}</p>}
  </form>;
  if (tool === "pushPull") {
    const inputRef = pushPull?.inputRef ?? fallbackInput;
    const hint = pushPull?.hint || (zh ? "先选择一个面。正值向外推，负值向内拉。" : "Select a face. Positive extends outward; negative pulls inward.");
    return <form className="model-edit-panel model-edit-panel--pushpull" aria-label={titles[tool]}
      onKeyDown={(event) => {
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); }
      }} onSubmit={(event) => {
        event.preventDefault();
        const value = inputRef.current?.value ?? "";
        const distance = Number(value);
        if (busy || !subject || !value.trim() || !Number.isFinite(distance) || Math.abs(distance) < 1e-9) return;
        if (pushPull?.active) pushPull.onCommit(distance);
        else onApply({ kind: "pushPull", distance });
      }}>
      <div className="model-edit-panel__distance">
        <ModelToolButton icon="help" label={hint} />
        <label><span className="model-edit-panel__sr">{zh ? "距离 m" : "Distance m"}</span>
          <input ref={inputRef} type="number" step="any" defaultValue="1" disabled={busy}
            onFocus={(event) => event.currentTarget.select()}
            onChange={(event) => pushPull?.onChange(event.target.value)} />
        </label><span className="quiet" aria-hidden="true">m</span>
        <ModelToolButton icon="check" label={busy ? (zh ? "正在生成模型…" : "Building model…") : (zh ? "应用" : "Apply")}
          shortcut="Enter" type="submit" disabled={busy || !subject} />
        <ModelToolButton icon="close" label={zh ? "关闭工具" : "Close tool"} shortcut="Esc" onClick={onClose} />
      </div>
      {error && <p role="alert" className="model-edit-panel__error">{error}</p>}
    </form>;
  }
  return null;
}
