import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5175,
    strictPort: true,
    proxy: {
      "/api": {
        target: process.env.MONKEYHUB_API_URL ?? "http://127.0.0.1:8790",
        changeOrigin: true,
      },
    },
  },
  build: { target: "es2022" },
});
