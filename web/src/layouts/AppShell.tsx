/**
 * v3 root layout (Material 3): top app bar, navigation drawer / rail,
 * routed content, assistant side sheet. Replaces the v2 activity bar.
 * Window classes follow M3: >=1200 drawer, 600-1199 rail, <600 modal.
 */
import { Outlet, useNavigate } from "@tanstack/react-router";
import { Suspense, useEffect, useState } from "react";

import { AssistantProvider, AssistantSheet } from "../components/v3/Assistant";
import { NavDrawer } from "../components/v3/NavDrawer";
import { PageSearch } from "../components/v3/PageSearch";
import { TopBar } from "../components/v3/TopBar";
import { SnackbarHost } from "../components/v3/ui";
import { useApp } from "../stores/app";
import { useShell } from "../stores/shell";

function navMode(width: number): "drawer" | "rail" {
  return width >= 1200 ? "drawer" : "rail";
}

export function AppShell() {
  const theme = useApp((s) => s.theme);
  const lang = useApp((s) => s.lang);
  const sheetOpen = useShell((s) => s.sheetOpen);
  const closeSheet = useShell((s) => s.closeSheet);
  const navOpen = useShell((s) => s.navOpen);
  const setNavOpen = useShell((s) => s.setNavOpen);
  const setSearchOpen = useShell((s) => s.setSearchOpen);
  const navigate = useNavigate();
  const [width, setWidth] = useState(() => (typeof window === "undefined" ? 1440 : window.innerWidth));

  useEffect(() => {
    document.documentElement.setAttribute("lang", lang === "zh" ? "zh-CN" : "en");
  }, [lang]);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setSearchOpen]);

  const overlaySheet = sheetOpen && width < 1200;
  const compact = width < 600;

  return (
    <AssistantProvider>
      <div className="app" data-nav={navMode(width)} data-sheet={sheetOpen ? "open" : "closed"} data-nav-open={compact && navOpen ? "true" : "false"}>
        <TopBar />
        <NavDrawer />
        <main className="content" id="main">
          <Suspense fallback={<div className="skeleton" style={{ height: 320 }} />}>
            <Outlet />
          </Suspense>
        </main>
        <AssistantSheet
          onOpenFull={() => {
            closeSheet();
            void navigate({ to: "/chat" });
          }}
        />
      </div>
      {overlaySheet || (compact && navOpen) ? (
        <div
          className="scrim"
          aria-hidden
          onClick={() => {
            closeSheet();
            setNavOpen(false);
          }}
        />
      ) : null}
      <PageSearch />
      <SnackbarHost />
    </AssistantProvider>
  );
}
