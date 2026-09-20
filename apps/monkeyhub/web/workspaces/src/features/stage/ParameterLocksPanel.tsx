import { useState } from "react";
import type { ParameterDto } from "../../api/generated";
import { usePreferences } from "../settings/preferences";
import "./ModelEditPanel.css";
import "./ParameterLocksPanel.css";

export interface ParameterLockControls {
  parameters: readonly ParameterDto[];
  contextKey: string;
  busy: boolean;
  disabledReason: string | null;
  error: string | null;
  onApply(keys: string[], action: "lock" | "unlock"): void;
}

/** Shows the saved parameter projection; only the server changes lock state. */
export function ParameterLocksPanel({ controls, onClose }: {
  controls: ParameterLockControls;
  onClose(): void;
}) {
  const { language } = usePreferences();
  const zh = language === "zh-CN";
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const keysFor = (action: "lock" | "unlock") => controls.parameters
    .filter((parameter) => selected.has(parameter.key) && (action === "lock" ? !parameter.lockAuthority : !!parameter.lockAuthority))
    .map((parameter) => parameter.key);
  const locked = keysFor("unlock"), unlocked = keysFor("lock");
  const disabled = controls.busy || controls.disabledReason !== null;
  return <section className="model-edit-panel parameter-locks" role="region" aria-label={zh ? "参数锁" : "Parameter locks"}
    onKeyDown={(event) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); }
    }}>
    <div className="model-edit-panel__heading">
      <strong>{zh ? "参数锁" : "Parameter locks"}</strong>
      <button type="button" className="btn btn--small" autoFocus onClick={onClose}>{zh ? "关闭" : "Close"}</button>
    </div>
    <p>{zh ? "锁定所选参数的值及已有绑定。其他参数和几何仍可修改。" : "Lock selected values and existing bindings. Other parameters and geometry remain editable."}</p>
    {controls.parameters.length === 0 ? <p>{zh ? "当前模型没有已声明的参数。" : "This model has no declared parameters."}</p>
      : <div className="parameter-locks__list">
        {controls.parameters.map((parameter) => <label className="parameter-locks__row" key={parameter.key}>
          <input type="checkbox" checked={selected.has(parameter.key)} disabled={disabled}
            onChange={(event) => {
              const checked = event.currentTarget.checked;
              setSelected((current) => { const next = new Set(current); checked ? next.add(parameter.key) : next.delete(parameter.key); return next; });
            }} />
          <span className="parameter-locks__name">{parameter.key}<span className="quiet">{parameter.value} {parameter.unit}</span></span>
          <span>{parameter.lockAuthority ? (zh ? "已锁定" : "Locked") : (zh ? "可修改" : "Editable")}</span>
        </label>)}
      </div>}
    {controls.disabledReason && <p role="status">{controls.disabledReason}</p>}
    {controls.busy && <p role="status">{zh ? "正在保存…" : "Saving…"}</p>}
    {controls.error && <p className="model-edit-panel__error" role="alert">{controls.error}</p>}
    <div className="parameter-locks__actions">
      <button type="button" className="btn" disabled={disabled || unlocked.length === 0}
        onClick={() => controls.onApply(unlocked, "lock")}>{zh ? "锁定所选" : "Lock selected"}</button>
      <button type="button" className="btn" disabled={disabled || locked.length === 0}
        onClick={() => controls.onApply(locked, "unlock")}>{zh ? "解锁所选" : "Unlock selected"}</button>
    </div>
    <small>{zh ? "保存为新的工作候选；不会接受或发布设计阶段。" : "Saves a new working candidate; does not accept or issue a design stage."}</small>
  </section>;
}
