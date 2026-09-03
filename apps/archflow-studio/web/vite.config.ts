import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// This file runs in Node, which the app itself never does, so its one Node
// global is declared here rather than by adding Node's types to the app's
// compilation.
declare const process: { env: Record<string, string | undefined> };

export default defineConfig({
  plugins: [react()],
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
