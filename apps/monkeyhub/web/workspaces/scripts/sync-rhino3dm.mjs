import { copyFile, cp, mkdir, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const workspaceRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const appRoot = dirname(workspaceRoot);
const source = join(
  appRoot,
  "node_modules",
  "rhino3dm",
);
// Rebuild only this application's generated public assets, including the local
// whiteboard fonts. Neither runtime needs a CDN in the packaged application.
const publicRoot = join(appRoot, ".generated", "public");
const destination = join(publicRoot, "rhino3dm");

await rm(publicRoot, { recursive: true, force: true });
await mkdir(destination, { recursive: true });
await Promise.all(
  ["rhino3dm.js", "rhino3dm.wasm"].map((name) =>
    copyFile(join(source, name), join(destination, name)),
  ),
);
// The exact-geometry fallback is a classic worker: it shares this public
// rhino3dm runtime without making Vite bundle rhino3dm's Node shims.
await copyFile(
  join(workspaceRoot, "src", "workspaces", "monkeyarch", "viewer", "nurbsFallback.worker.js"),
  join(publicRoot, "nurbsFallback.worker.js"),
);

await cp(
  join(appRoot, "node_modules", "@excalidraw", "excalidraw", "dist", "prod", "fonts"),
  join(publicRoot, "excalidraw", "fonts"),
  { recursive: true },
);
// Replace the bundled legacy Liberation font with the verified OFL 2.1.5 asset.
await copyFile(
  join(workspaceRoot, "assets", "board-fonts", "LiberationSans-Regular.woff2"),
  join(publicRoot, "excalidraw", "fonts", "Liberation", "LiberationSans-Regular.woff2"),
);

console.log(`Synced Rhino3dm runtime to ${destination}`);
