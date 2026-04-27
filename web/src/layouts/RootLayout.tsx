import { Outlet } from "@tanstack/react-router";
import { useEffect } from "react";
import { ActivityBar } from "../components/ActivityBar";
import { TopBar } from "../components/TopBar";
import { useApp } from "../stores/app";

/**
 * Root layout — 3px brand stripe + 64-px activity bar on the left, then
 * a sticky 56-px topbar above the routed page.  Matches v2 mockup
 * (docs/webui-preview-v2.html).
 */
export function RootLayout() {
  const theme = useApp((s) => s.theme);
  const lang = useApp((s) => s.lang);

  useEffect(() => {
    document.documentElement.setAttribute("lang", lang === "zh" ? "zh-CN" : "en");
  }, [lang]);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);

  return (
    <>
      <div className="brand-stripe" aria-hidden={true} />
      <div className="app-shell">
        <ActivityBar />
        <div style={{ minWidth: 0 }}>
          <TopBar />
          <main className="app-content">
            <Outlet />
          </main>
        </div>
      </div>
    </>
  );
}
