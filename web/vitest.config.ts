/**
 * Vitest config — kept separate from vite.config.ts so the strict
 * rollup output typings in `vitest/config`'s re-exported `defineConfig`
 * don't reject our `manualChunks: { xterm: [...] }` record form.
 *
 * Pulls in the same plugins as the production build (React) so jsx /
 * fast-refresh / `.tsx` handling works inside specs.
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
