/**
 * Global app state — theme, language, selected device / backend.
 *
 * Zustand was picked over React Context because (a) panels only care
 * about a few fields each, so Context-induced rerenders across the
 * whole tree are wasteful, and (b) Zustand's `subscribeWithSelector`
 * + shallow compare is built-in.
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Theme = "light" | "dark" | "auto";
export type Lang = "en" | "zh";

interface AppState {
  theme: Theme;
  lang: Lang;
  device: string | null;       // serial of the currently-selected device
  backend: string;             // LLM backend id (ollama / openai-compat / ...)
  model: string | null;        // selected model name within the backend
  setTheme: (t: Theme) => void;
  setLang: (l: Lang) => void;
  setDevice: (d: string | null) => void;
  setBackend: (b: string) => void;
  setModel: (m: string | null) => void;
}

export const useApp = create<AppState>()(
  persist(
    (set) => ({
      theme: "auto",
      lang: detectInitialLang(),
      device: null,
      backend: "ollama",
      model: null,
      setTheme: (theme) => set({ theme }),
      setLang: (lang) => set({ lang }),
      setDevice: (device) => set({ device }),
      setBackend: (backend) => set({ backend }),
      setModel: (model) => set({ model }),
    }),
    {
      name: "alb.app",
      partialize: (s) => ({
        theme: s.theme,
        lang: s.lang,
        device: s.device,
        backend: s.backend,
        model: s.model,
      }),
    },
  ),
);

function detectInitialLang(): Lang {
  if (typeof navigator === "undefined") return "en";
  return navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en";
}
