import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

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
      "/api": "http://127.0.0.1:8000",
    },
  },
  build: {
    target: "es2022",
    sourcemap: true,
  },
});
