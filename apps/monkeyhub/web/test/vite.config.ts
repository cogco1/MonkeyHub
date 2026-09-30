import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import hubConfig from "../../vite.config.ts";
import { workspaceFixture } from "./workspaceFixture.mjs";

/** Browser tests only. The Hub's production build has no workspace HTML entry. */
export default defineConfig({
  ...hubConfig,
  root: fileURLToPath(new URL("..", import.meta.url)),
  publicDir: fileURLToPath(new URL("../../.generated/public", import.meta.url)),
  plugins: [...(hubConfig.plugins ?? []), workspaceFixture()],
  server: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true,
    proxy: { "/api": process.env.ARCHFLOW_STUDIO_API_URL ?? "http://127.0.0.1:8000" },
  },
});
