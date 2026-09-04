// The loading animation has one source: apps/archflow-studio/assets/loading/. The splash
// window reads those files straight off disk; the web client cannot, so they are copied into
// the served public directory here, the same way the rhino3dm runtime is.
//
// The four names are a contract with LoadingOverlay.tsx, which lists the same four. A
// storyboard that arrives with a different number of frames changes both files, and until it
// does this script refuses rather than serving a set the client will not ask for.

import { copyFile, mkdir, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const source = join(appRoot, "..", "assets", "loading");
const destination = join(appRoot, ".generated", "public", "loading");

const FRAMES = [
  "frame-01.png",
  "frame-02.png",
  "frame-03.png",
  "frame-04.png",
];

await rm(destination, { recursive: true, force: true });
await mkdir(destination, { recursive: true });
for (const name of FRAMES) {
  try {
    await copyFile(join(source, name), join(destination, name));
  } catch (cause) {
    throw new Error(
      `${name} is missing from ${source}. Draw the frames with ` +
        `py -3.12 apps/archflow-studio/assets/loading/make_frames.py, or drop the ` +
        `storyboard's four frames in under these names.`,
      { cause },
    );
  }
}

console.log(`Synced ${FRAMES.length} loading frames to ${destination}`);
