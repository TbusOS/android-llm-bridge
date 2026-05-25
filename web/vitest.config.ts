/**
 * Vitest config — deliberately separate from `vite.config.ts`.
 *
 * Why two configs (verified 2026-05-25 via single-config attempt):
 *   - `defineConfig` from "vite" has no `test` field on its
 *     UserConfigExport; adding `test: { ... }` errors with TS2769.
 *   - `defineConfig` from "vitest/config" exposes `test` but narrows
 *     `build.rollupOptions.output.manualChunks` to the array-of-output
 *     form, rejecting our `{ xterm: [...] }` record form (prod build's
 *     chunk-split config).
 *
 * Split keeps both configs typed correctly. Maintenance contract: any
 * plugin / alias / resolve-config change that specs also need MUST be
 * mirrored here. Today we share `react()` plugin + `@` alias.
 *
 * See AH-5 commit message for the single-config attempt and the
 * concrete errors that ruled it out.
 */
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: false,
    // .css imports become empty modules in tests so React components
    // that pull global styles don't crash module resolution.
    css: true,
  },
});
