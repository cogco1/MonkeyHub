/**
 * The one loading surface the client has, in the launcher's words.
 *
 * The splash window that opens before the browser does shows this brand block, this
 * animation and a line of real status; when the browser takes over, the wait is not over --
 * the client still has to reach the API, and the viewport still has to parse the exports --
 * so the same three things carry on here rather than the tab starting again from a blank
 * page. Nothing on this surface is decided here: the status line is handed in, and it is
 * whatever the caller is actually waiting for.
 *
 * The frames are the launcher's frames, copied out of apps/archflow-studio/assets/loading/
 * by scripts/sync-loading.mjs at dev and at build. The four names below are the contract
 * that script enforces: it refuses rather than serving a set this file will not ask for.
 */

import { useEffect, useState } from "react";

export const LOADING_FRAMES = [
  "/loading/frame-01.png",
  "/loading/frame-02.png",
  "/loading/frame-03.png",
  "/loading/frame-04.png",
] as const;

/** ~8 fps, the rate the splash window's WinForms timer cycles the same four frames at. */
const FRAME_MS = 125;

export function LoadingOverlay({
  mode,
  status,
}: {
  /** `boot` covers the window before the shell has anything to show; `stage` covers the model. */
  mode: "boot" | "stage";
  /** What is actually being waited for, in the words of whoever is waiting. */
  status: string;
}) {
  const [frame, setFrame] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(
      () => setFrame((current) => (current + 1) % LOADING_FRAMES.length),
      FRAME_MS,
    );
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="boot" data-mode={mode} role="status" aria-live="polite">
      <div className="boot__card">
        <p className="boot__title">
          MonkeyArch <span className="boot__glyph">🐒</span>
        </p>
        <p className="boot__tagline">Professional modeling environment</p>
        <p className="boot__protocol">Powered by the open ArchFlow protocol.</p>
        {/* Every frame is in the document from the first paint, and only one of them is
            shown: swapping one `src` would leave the first cycle blank while each file is
            fetched, which on a loading surface reads as the loading having stalled. */}
        <div className="boot__reel" aria-hidden="true">
          {LOADING_FRAMES.map((source, index) => (
            <img
              key={source}
              className="boot__frame"
              data-on={String(index === frame)}
              src={source}
              alt=""
            />
          ))}
        </div>
        <p className="boot__working">猴子正在后台狠狠干 OCCT</p>
        <p className="boot__status mono">{status}</p>
      </div>
    </div>
  );
}
