/**
 * Shared vite config fragments — single source of truth for
 * everything that both `vite.config.ts` (build) and `vitest.config.ts`
 * (test) must agree on.
 *
 * Background: ADR-040 enumerated WHY we keep two config files (vite
 * vs vitest each narrow each other's types). The maintenance contract
 * was "any plugin / alias / resolve that specs need MUST be mirrored
 * to vitest.config.ts" — but a written contract is fragile (5/25
 * second-round arch MID-2). This file replaces the contract with an
 * import: both configs read `sharedPlugins` / `sharedAlias` here.
 *
 * Adding new shared config: extend the exports here, both configs
 * spread automatically. Vite-only options (server proxy / build /
 * base) stay in vite.config.ts; test-only options (environment /
 * setupFiles / globals) stay in vitest.config.ts.
 */
import react from "@vitejs/plugin-react";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export const sharedPlugins = [react()];

export const sharedAlias = {
  "@": path.resolve(__dirname, "./src"),
};
