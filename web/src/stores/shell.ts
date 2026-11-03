/**
 * Shell UI state for the v3 layout: assistant sheet, the context it was
 * opened with, the compact navigation drawer, the page search, and the
 * model source the assistant uses. Persisted: the source, agent mode, ask
 * before tools, and the reading settings (sheet width, chat text size).
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ModelSource = "host" | "api" | "board";

/* The assistant sheet can be dragged wider (2026-11-03: "sometimes it
   feels too narrow"); the chat text size can be changed. */
export const SHEET_W_DEFAULT = 400;
export const SHEET_W_MIN = 320;
export const SHEET_W_MAX = 960;
export const CHAT_FONT_DEFAULT = 14;
export const CHAT_FONT_MIN = 12;
export const CHAT_FONT_MAX = 22;

const clamp = (v: number, lo: number, hi: number, fallback: number): number =>
  Number.isFinite(v) ? Math.min(hi, Math.max(lo, Math.round(v))) : fallback;

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
  /** Assistant sheet width, px. */
  sheetWidth: number;
  /** Conversation text size, px. */
  chatFontPx: number;
  openSheet: (ctx?: AssistantContext | null) => void;
  closeSheet: () => void;
  toggleSheet: () => void;
  clearContext: () => void;
  setNavOpen: (open: boolean) => void;
  setSearchOpen: (open: boolean) => void;
  setSource: (s: ModelSource) => void;
  setAgentMode: (on: boolean) => void;
  setConfirmTools: (on: boolean) => void;
  setSheetWidth: (px: number) => void;
  setChatFontPx: (px: number) => void;
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
      sheetWidth: SHEET_W_DEFAULT,
      chatFontPx: CHAT_FONT_DEFAULT,
      openSheet: (ctx) => set((s) => ({ sheetOpen: true, context: ctx === undefined ? s.context : ctx })),
      closeSheet: () => set({ sheetOpen: false }),
      toggleSheet: () => set((s) => ({ sheetOpen: !s.sheetOpen })),
      clearContext: () => set({ context: null }),
      setNavOpen: (navOpen) => set({ navOpen }),
      setSearchOpen: (searchOpen) => set({ searchOpen }),
      setSource: (source) => set({ source }),
      setAgentMode: (agentMode) => set({ agentMode }),
      setConfirmTools: (confirmTools) => set({ confirmTools }),
      setSheetWidth: (px) => set({ sheetWidth: clamp(px, SHEET_W_MIN, SHEET_W_MAX, SHEET_W_DEFAULT) }),
      setChatFontPx: (px) => set({ chatFontPx: clamp(px, CHAT_FONT_MIN, CHAT_FONT_MAX, CHAT_FONT_DEFAULT) }),
    }),
    {
      name: "alb.shell",
      partialize: (s) => ({
        source: s.source,
        agentMode: s.agentMode,
        confirmTools: s.confirmTools,
        sheetWidth: s.sheetWidth,
        chatFontPx: s.chatFontPx,
      }),
    },
  ),
);
