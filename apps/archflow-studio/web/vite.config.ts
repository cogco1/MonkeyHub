import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

function excalidrawLocalFonts(): Plugin {
  // Excalidraw 0.18.1 always appends its CDN even with a local asset path.
  // Keep the bundled fonts as the only source, including SVG export.
  const replacements = [
    {
      suffix: "/@excalidraw/excalidraw/dist/dev/chunk-4FTI6OG3.js",
      search: "urls.push(new URL(assetUrl, _ExcalidrawFontFace.ASSETS_FALLBACK_URL));",
      replacement: "",
    },
    {
      suffix: "/@excalidraw/excalidraw/dist/prod/chunk-K2UTITRG.js",
      search: "return r.push(new URL(n,jn.ASSETS_FALLBACK_URL)),r",
      replacement: "return r",
    },
  ];
  let isBuild = false;
  let transformed = false;
  return {
    name: "excalidraw-local-fonts",
    enforce: "pre",
    configResolved(config) {
      isBuild = config.command === "build";
    },
    buildStart() {
      transformed = false;
    },
    transform(code, id) {
      const path = id.replaceAll("\\", "/").split("?")[0];
      const rule = replacements.find(({ suffix }) => path.endsWith(suffix));
      if (!rule) return;
      if (code.split(rule.search).length !== 2) {
        this.error("Excalidraw 0.18.1 font source changed; review the local-font transform.");
      }
      transformed = true;
      return { code: code.replace(rule.search, rule.replacement), map: null };
    },
    buildEnd(error) {
      if (isBuild && !error && !transformed) {
        this.error("Excalidraw 0.18.1 font chunk was not found; review the local-font transform.");
      }
    },
  };
}

// This file runs in Node, which the app itself never does, so its one Node
// global is declared here rather than by adding Node's types to the app's
// compilation.
declare const process: { env: Record<string, string | undefined> };

export default defineConfig({
  plugins: [react(), excalidrawLocalFonts()],
  // Apply the same source transform in development instead of prebundling it.
  optimizeDeps: { exclude: ["@excalidraw/excalidraw"] },
  publicDir: ".generated/public",
  server: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true,
    proxy: {
      // Everything under /api is the Studio API's, event stream included; the
      // dev server forwards it verbatim so the browser talks to one origin.
      // The API's own default port is 8000 and so is this; an API started on
      // another port is named by ARCHFLOW_STUDIO_API_URL rather than by
      // editing this line, so two of them can run side by side.
      "/api": process.env.ARCHFLOW_STUDIO_API_URL ?? "http://127.0.0.1:8000",
    },
  },
  build: {
    target: "es2022",
    sourcemap: true,
  },
});
