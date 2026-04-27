import { Outlet } from "@tanstack/react-router";
import { useEffect } from "react";
import { AppNav } from "../components/AppNav";
import { TabChips } from "../components/TabChips";
import { useApp } from "../stores/app";

/**
 * Root layout — top nav, sticky tab strip, then the active route in a
 * 1280-px content container.  Matches docs/webui-preview.html.
 */
export function RootLayout() {
  const theme = useApp((s) => s.theme);
  const lang = useApp((s) => s.lang);

  useEffect(() => {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  }, [lang]);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);

  return (
    <>
      <AppNav />
      <TabChips />
      <main className="app-main">
        <Outlet />
      </main>
    </>
  );
}
