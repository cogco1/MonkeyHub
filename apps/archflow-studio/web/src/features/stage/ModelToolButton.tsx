import type { ComponentProps } from "react";

const paths = {
  select: "M5 3 20 12 13 14 10 21 5 3Z",
  rectangle: "M5 5h14v14H5Z",
  circle: "M20 12a8 8 0 1 1-16 0 8 8 0 1 1 16 0Z",
  polygon: "M5 5 17 4 21 13 13 20 4 15Z",
  pushPull: "M12 3v9m-3-6 3-3 3 3M4 14l8-4 8 4-8 4-8-4Zm0 0v4l8 4 8-4v-4M12 18v4",
  move: "M12 3v18M3 12h18M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3",
  rotate: "M20 9a8 8 0 1 0 0 6M20 3v6h-6",
  scale: "M4 14h6v6H4ZM13 4h7v7M20 4l-8 8",
  copy: "M9 9h11v11H9ZM15 5V3H3v12h2",
  undo: "M8 5 3 10l5 5M3 10h11a6 6 0 0 1 6 6v3",
  redo: "m16 5 5 5-5 5M21 10H10a6 6 0 0 0-6 6v3",
  measure: "m3 15 12-12 6 6L9 21 3 15Zm3-3 2 2m1-5 3 3m0-6 2 2m1-5 3 3",
  annotate: "m4 16 12-12 4 4L8 20H4v-4Zm9-9 4 4M4 16l4 4",
  erase: "m4 13 9-9 7 7-9 9H8l-4-4v-3Zm5-5 7 7M11 20h9",
  fit: "M9 4H4v5m11-5h5v5M4 15v5h5m11-5v5h-5M8 8h8v8H8Z",
  front: "M5 20V5h14v15M3 20h18M9 20v-7h6v7M9 9h1m4 0h1",
  more: "M5 12a1 1 0 1 1-2 0 1 1 0 1 1 2 0Zm8 0a1 1 0 1 1-2 0 1 1 0 1 1 2 0Zm8 0a1 1 0 1 1-2 0 1 1 0 1 1 2 0Z",
  grid: "M4 4h16v16H4ZM4 12h16M12 4v16",
  check: "m5 12 4 4L19 6",
  close: "m6 6 12 12M18 6 6 18",
  help: "M20 12a8 8 0 1 1-16 0 8 8 0 1 1 16 0ZM9.5 9a2.5 2.5 0 0 1 5 0c0 2-2.5 2-2.5 4M12 16h.01",
} as const;

export type ModelToolIcon = keyof typeof paths;

export type ModelToolButtonProps = Omit<ComponentProps<"button">, "children" | "aria-label"> & {
  icon: ModelToolIcon;
  label: string;
  shortcut?: string;
};

/** A local toolbar control; the caller continues to own its action and state. */
export function ModelToolButton({ icon, label, shortcut, className, type = "button", ...props }: ModelToolButtonProps) {
  return (
    <button {...props} type={type} aria-label={label} className={`model-tool-button${className ? ` ${className}` : ""}`}>
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
        strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
        <path d={paths[icon]} />
      </svg>
      <span className="model-tool-button__tooltip" aria-hidden="true">
        <span>{label}</span>
        {shortcut && <kbd>{shortcut}</kbd>}
      </span>
    </button>
  );
}
