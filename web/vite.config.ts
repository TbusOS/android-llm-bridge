import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// alb Web build → `../docs/app/` (committed, served by FastAPI StaticFiles
// and by GitHub Pages). During dev, Vite proxies /chat, /playground,
// /metrics, /terminal, /api, /health to a running `alb-api` on localhost.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/health": "http://localhost:8765",
      "/api": "http://localhost:8765",
      "/devices": "http://localhost:8765",
      "/sessions": "http://localhost:8765",
      "/tools": "http://localhost:8765",
      "/uart": "http://localhost:8765",
      "/audit": {
        target: "http://localhost:8765",
        ws: true,
      },
      "/chat": {
        target: "http://localhost:8765",
        ws: true,
      },
      "/playground": {
        target: "http://localhost:8765",
        ws: true,
      },
      "/metrics": {
        target: "http://localhost:8765",
        ws: true,
      },
      "/terminal": {
        target: "http://localhost:8765",
        ws: true,
      },
    },
  },
  build: {
    outDir: "../docs/app",
    emptyOutDir: true,
    sourcemap: false,
    assetsDir: "assets",
    rollupOptions: {
      output: {
        // Keep chunk names stable across builds so git diff is readable.
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
  // Pin a deterministic base path — alb-api mounts the bundle at /app/,
  // and the GitHub Pages preview lives at /android-llm-bridge/app/. The
  // served path is set per-deployment via `VITE_BASE`.
  base: process.env.VITE_BASE ?? "/app/",
});
