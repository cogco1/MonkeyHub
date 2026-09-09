import { copyFile, mkdir, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const source = join(
  appRoot,
  "node_modules",
  "rhino3dm",
);
// Rhino is the web client's only generated public asset. Clear the public root so a
// checkout that once generated the retired raster loading reel cannot keep shipping it.
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
  join(appRoot, "src", "workspaces", "monkeyarch", "viewer", "nurbsFallback.worker.js"),
  join(publicRoot, "nurbsFallback.worker.js"),
);

console.log(`Synced Rhino3dm runtime to ${destination}`);
