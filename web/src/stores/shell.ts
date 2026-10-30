/**
 * Shell UI state for the v3 layout: assistant sheet, the context it was
 * opened with, the compact navigation drawer, the page search, and the
 * model source the assistant uses. Not persisted except the source.
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ModelSource = "host" | "api" | "board";

export interface AssistantContext {
  /** Short label shown as a chip, both languages. */
  zh: string;
  en: string;
  /** Text handed to the model with the next message. */
  detail?: string;
}

interface ShellState {
  sheetOpen: boolean;
  context: AssistantContext | null;
  navOpen: boolean;
  searchOpen: boolean;
  source: ModelSource;
  agentMode: boolean;
  /** Ask before each tool call the model makes. Off by default (2026-10-30). */
  confirmTools: boolean;
  openSheet: (ctx?: AssistantContext | null) => void;
  closeSheet: () => void;
  toggleSheet: () => void;
  clearContext: () => void;
  setNavOpen: (open: boolean) => void;
  setSearchOpen: (open: boolean) => void;
  setSource: (s: ModelSource) => void;
  setAgentMode: (on: boolean) => void;
  setConfirmTools: (on: boolean) => void;
}

export const useShell = create<ShellState>()(
  persist(
    (set) => ({
      sheetOpen: false,
      context: null,
      navOpen: false,
      searchOpen: false,
      source: "host",
      agentMode: true,
      confirmTools: false,
      openSheet: (ctx) => set((s) => ({ sheetOpen: true, context: ctx === undefined ? s.context : ctx })),
      closeSheet: () => set({ sheetOpen: false }),
      toggleSheet: () => set((s) => ({ sheetOpen: !s.sheetOpen })),
      clearContext: () => set({ context: null }),
      setNavOpen: (navOpen) => set({ navOpen }),
      setSearchOpen: (searchOpen) => set({ searchOpen }),
      setSource: (source) => set({ source }),
      setAgentMode: (agentMode) => set({ agentMode }),
      setConfirmTools: (confirmTools) => set({ confirmTools }),
    }),
    { name: "alb.shell", partialize: (s) => ({ source: s.source, agentMode: s.agentMode, confirmTools: s.confirmTools }) },
  ),
);
