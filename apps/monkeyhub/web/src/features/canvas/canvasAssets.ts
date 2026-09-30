/**
 * Where Excalidraw finds its fonts.
 *
 * The Hub serves Excalidraw's fonts from its own origin (`excalidraw/`, synced
 * by `workspaces/scripts/sync-rhino3dm.mjs`; `vite.config.ts` removes the CDN
 * fallback). Excalidraw reads this path the first time a canvas loads its
 * fonts, so the canvas host imports this module before it mounts one: fonts
 * are local whether a project first opens Board or the Design Tree.
 */
export function prepareCanvasAssets(): void {
  (window as Window & { EXCALIDRAW_ASSET_PATH?: string }).EXCALIDRAW_ASSET_PATH =
    new URL("excalidraw/", document.baseURI).href;
}

prepareCanvasAssets();
