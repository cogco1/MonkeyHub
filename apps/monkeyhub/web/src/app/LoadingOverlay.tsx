/**
 * The client's one loading surface. Callers own the status sentence because only they
 * know what is actually pending; this component supplies an indeterminate rail, the
 * quiet shell around it and, once a wait runs long, how long it has lasted. The
 * launcher has a determinate rail because it knows its eight startup steps, while the
 * browser must not invent a percentage for network or 3DM work.
 */

import { useEffect, useState, type ReactNode } from "react";

import { usePreferences, type Language } from "../features/settings/preferences";
import { BilingualProse } from "./ErrorPanel";

/** A wait this long starts showing how long it has lasted (R14). */
const ELAPSED_AFTER_SECONDS = 3;

/** The overlay's own words; they stay here until the catalogs can take them (review §4 step 1). */
const COPY: Record<Language, { opening: string; waited(seconds: number): string }> = {
  "zh-CN": {
    opening: "正在打开…",
    waited: (seconds) => `已等待 ${seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`}`,
  },
  en: {
    opening: "Opening…",
    waited: (seconds) => `Still waiting · ${seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s`}`,
  },
};

/** Whole seconds since the overlay appeared; one wait keeps counting across its steps. */
function useElapsedSeconds(): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return seconds;
}

function DraftMonkeyMark() {
  return (
    <svg
      className="draft-monkey"
      viewBox="0 0 56 56"
      aria-hidden="true"
      focusable="false"
    >
      <path className="draft-monkey__arch" d="M6 52V29a22 22 0 0 1 44 0v23" />
      <path className="draft-monkey__keystone" d="M23 3h10l-2 14h-6z" />
      <g className="draft-monkey__figure">
        <path d="M28 16l-2 9" />
        <circle className="draft-monkey__node" cx="16.5" cy="30" r="3.5" />
        <circle className="draft-monkey__node" cx="29.5" cy="29.5" r="3.5" />
        <circle className="draft-monkey__node" cx="23" cy="29" r="7" />
        <path d="M20.5 35.5c-2.2 3.5-1.6 8.6 2 11.7l6.5-.7c2.8-3.4 2.7-7.8-.2-11.2" />
        <path d="M21 38l-8 5M23 47l-5 5M28 46.5l4 4.2" />
        <path d="M29 38.5c10-5 20 1 20 9 0 6-5 9-10 8-5-1-7-5-5-9 2-3 7-3 9 0 1 2 0 4-2 4" />
      </g>
    </svg>
  );
}

export function LoadingOverlay({
  mode,
  status,
  label,
}: {
  /** `boot` covers the window; `stage` preserves the model while blocking stale gestures. */
  mode: "boot" | "stage";
  /** What is actually being waited for, in the words of whoever is waiting. */
  status: ReactNode;
  /**
   * What is being opened, named for the person, such as a surface or a model. It
   * heads the boot readout in place of a brand; without it the heading is a plain
   * "Opening…" and the status names the rest.
   */
  label?: string;
}) {
  const { language } = usePreferences();
  const copy = COPY[language];
  const seconds = useElapsedSeconds();
  return (
    <div
      className="boot"
      data-mode={mode}
    >
      {mode === "boot" && (
        <div className="boot__workspace" aria-hidden="true">
          <span className="boot__chrome" />
          <span className="boot__sidebar" />
          <span className="boot__viewport" />
        </div>
      )}
      <div className="boot__readout">
        {mode === "boot" && (
          <div className="boot__brand">
            <DraftMonkeyMark />
            <p className="boot__title">{label ?? copy.opening}</p>
          </div>
        )}
        <span className="activity-rail" aria-hidden="true" />
        <p className="boot__status mono" role="status" aria-live="polite">
          {typeof status === "string" ? <BilingualProse source={status} /> : status}
        </p>
        {/* Outside the live region: the count is there to read, not announced every second. */}
        {seconds >= ELAPSED_AFTER_SECONDS && <p className="boot__status boot__elapsed">{copy.waited(seconds)}</p>}
      </div>
    </div>
  );
}
