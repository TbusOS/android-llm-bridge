/**
 * vitest global setup.
 *
 * Loads @testing-library/jest-dom matchers so specs can use
 * `.toBeInTheDocument()` etc. Also stubs window.HTMLElement layout
 * APIs that jsdom doesn't implement (used by @tanstack/react-virtual
 * + xterm.js when those modules are imported transitively).
 */
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// React-testing-library doesn't auto-cleanup in vitest like it does in
// jest unless we explicitly call it.
afterEach(() => {
  cleanup();
});
