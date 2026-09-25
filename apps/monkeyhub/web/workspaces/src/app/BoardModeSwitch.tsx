import type { KeyboardEvent } from "react";
import { useT } from "../i18n/useT";
import "./boardModeSwitch.css";

export type BoardMode = "board" | "layout";
const modes: readonly BoardMode[] = ["board", "layout"];

/**
 * Board and its Layout mode share one rail entry (#300): this switch moves
 * between them. Both surfaces stay mounted, so neither loses its work. It is
 * one choice of two, so it reads and steers as a radio group.
 */
export function BoardModeSwitch({ mode, onChange }: { mode: BoardMode; onChange(mode: BoardMode): void }) {
  const t = useT();
  const step = (event: KeyboardEvent<HTMLButtonElement>) => {
    const offset = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (!offset) return;
    event.preventDefault();
    const next = modes[(modes.indexOf(mode) + offset + modes.length) % modes.length]!;
    onChange(next);
    (event.currentTarget.parentElement?.querySelector(`[data-mode="${next}"]`) as HTMLButtonElement | null)?.focus();
  };
  return <div className="board-mode-switch" role="radiogroup" aria-label={t("workspace.boardMode")}>
    {modes.map((value) => <button key={value} type="button" role="radio" data-mode={value} aria-checked={mode === value}
      tabIndex={mode === value ? 0 : -1} onKeyDown={step} onClick={() => { if (value !== mode) onChange(value); }}>
      {t(value === "board" ? "workspace.boardMode.board" : "workspace.boardMode.layout")}
    </button>)}
  </div>;
}
