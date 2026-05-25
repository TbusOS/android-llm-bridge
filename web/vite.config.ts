import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// alb Web build → `../docs/app/` (committed, served by FastAPI StaticFiles
// and by GitHub Pages). During dev, Vite proxies /chat, /playground,
// /metrics, /terminal, /api, /health to a running `alb-api` on localhost.
//
// Vitest config lives in `vitest.config.ts` (deliberately split). The
// reason — verified 2026-05-25 (AH-5):
//
//   - vite's `defineConfig` (from "vite") has no `test` field in its
//     `UserConfigExport` type. A `/// <reference types="vitest" />`
//     triple-slash does NOT augment the UserConfigExport union, only
//     the surrounding ambient declarations, so `test: { ... }` still
//     errors with TS2769.
//   - vitest's `defineConfig` (from "vitest/config") DOES expose
//     `test`, but its `UserConfig` type for `build.rollupOptions.output
//     .manualChunks` rejects our `{ xterm: [...] }` record form — it
//     only accepts the array-of-output form.
//
// So either the prod build's manualChunks loses its readable form or
// the test config can't carry the `test` field. Two configs is the
// non-ugly resolution. The earlier comment here ("strict rollup typings
// in vitest/config rejecting manualChunks") was directionally right
// but wasn't a misdiagnosis — single-config attempt confirmed both
// errors are real (see AH-5 commit message + DEBT-053).
//
// Maintainer responsibility: when adding plugins / alias / server
// proxy / resolve config that test files also need, mirror the change
// into vitest.config.ts. The two configs share an alias on `@` today.
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
      // /devices/{s}/files/push/stream and /pull/stream are WS endpoints
      // (MID-6, 2026-05-05). ws:true tells vite to proxy the upgrade
      // handshake too. Plain GET/POST under /devices keep working.
      "/devices": {
        target: "http://localhost:8765",
        ws: true,
      },
      "/workspace": "http://localhost:8765",
      "/sessions": "http://localhost:8765",
      "/tools": "http://localhost:8765",
      "/uart": {
        target: "http://localhost:8765",
        ws: true,
      },
      "/logcat": {
        target: "http://localhost:8765",
        ws: true,
      },
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
        // Split xterm into its own chunk so the dashboard / chat /
        // playground entries don't pay the ~80 KB gzip cost just to
        // exist. Loaded on demand when InspectPage opens UART /
        // Logcat / Shell tabs (PR-H follow-up perf-audit 2026-05-02).
        manualChunks: {
          xterm: ["@xterm/xterm", "@xterm/addon-fit"],
        },
      },
    },
  },
  // Pin a deterministic base path — alb-api mounts the bundle at /app/,
  // and the GitHub Pages preview lives at /android-llm-bridge/app/. The
  // served path is set per-deployment via `VITE_BASE`.
  base: process.env.VITE_BASE ?? "/app/",
});
