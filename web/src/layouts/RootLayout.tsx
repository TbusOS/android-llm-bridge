import { Outlet } from "@tanstack/react-router";
import { useEffect } from "react";
import { Sidebar } from "../components/Sidebar";
import { TopBar } from "../components/TopBar";
import { useApp } from "../stores/app";

/**
 * Root layout — sidebar + topbar + <Outlet/> for the active route.
 * Applies `html[lang]` + `html[data-theme]` based on Zustand state so
 * legacy anthropic.css selectors (html[lang^="zh"] ...) keep working.
 */
export function RootLayout() {
  const theme = useApp((s) => s.theme);
  const lang = useApp((s) => s.lang);

  useEffect(() => {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  }, [lang]);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "auto") {
      root.removeAttribute("data-theme");
    } else {
      root.setAttribute("data-theme", theme);
    }
  }, [theme]);

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "220px 1fr",
        minHeight: "100vh",
      }}
    >
      <Sidebar lang={lang} />
      <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
        <TopBar />
        <main
          style={{
            flex: 1,
            padding: "var(--space-6) var(--space-7)",
            minWidth: 0,
          }}
        >
          <Outlet />
        </main>
      </div>
    </div>
  );
}
