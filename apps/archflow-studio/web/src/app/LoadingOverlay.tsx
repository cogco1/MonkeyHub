/**
 * The client's one loading surface. Callers own the status sentence because only they
 * know what is actually pending; this component supplies an indeterminate rail and the
 * quiet shell around it. The launcher has a determinate rail because it knows its eight
 * startup steps, while the browser must not invent a percentage for network or 3DM work.
 */

import type { ReactNode } from "react";

import { BilingualProse } from "./ErrorPanel";

export function LoadingOverlay({
  mode,
  status,
}: {
  /** `boot` covers the window; `stage` preserves the model while blocking stale gestures. */
  mode: "boot" | "stage";
  /** What is actually being waited for, in the words of whoever is waiting. */
  status: ReactNode;
}) {
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
        {mode === "boot" && <p className="boot__title">MonkeyArch</p>}
        <span className="activity-rail" aria-hidden="true" />
        <p className="boot__status mono" role="status" aria-live="polite">
          {typeof status === "string" ? <BilingualProse source={status} /> : status}
        </p>
      </div>
    </div>
  );
}
