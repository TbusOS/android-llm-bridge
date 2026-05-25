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
 * Split keeps both configs typed correctly. Shared config (plugins /
 * alias) is now imported from `vite.shared.ts` so vite and vitest
 * never drift out of sync (AM-3 · arch MID-15). Add new shared config
 * to `vite.shared.ts`; test-only options stay here.
 *
 * See AH-5 + AM-3 commit messages for the single-config attempt and
 * the concrete errors that ruled it out.
 */
import { defineConfig } from "vitest/config";

import { sharedAlias, sharedPlugins } from "./vite.shared";

export default defineConfig({
  plugins: sharedPlugins,
  resolve: {
    alias: sharedAlias,
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
