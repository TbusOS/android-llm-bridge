/**
 * Top app nav — sticky 64px bar with brand, network status, language
 * toggle, theme cycler, and a link back to the docs home.
 *
 * Style is intentionally lifted from the GitHub Pages mockup
 * (docs/webui-preview.html) so the live app and the published preview
 * look like the same product.
 */
import { Link } from "@tanstack/react-router";
import { Languages, Moon, Sun, SunMoon, Wifi, WifiOff } from "lucide-react";
import { useEffect, useState } from "react";
import { useApp } from "../stores/app";

export function AppNav() {
  const theme = useApp((s) => s.theme);
  const lang = useApp((s) => s.lang);
  const setTheme = useApp((s) => s.setTheme);
  const setLang = useApp((s) => s.setLang);
  const online = useOnline();

  const nextTheme =
    theme === "light" ? "dark" : theme === "dark" ? "auto" : "light";

  return (
    <header className="app-nav" role="banner">
      <div className="app-nav-inner">
        <Link to="/devices" className="app-brand" aria-label="alb home">
          alb <small>android-llm-bridge</small>
        </Link>

        <span className="app-nav-spacer" />

        <span
          className="icon-btn"
          title={
            online
              ? lang === "zh"
                ? "已连接互联网"
                : "Connected to the internet"
              : lang === "zh"
                ? "离线 — 云端后端隐藏"
                : "Offline — cloud backends hidden"
          }
          style={{
            color: online ? "var(--anth-green)" : "var(--anth-danger)",
            borderColor: online ? "var(--anth-green)" : "var(--anth-danger)",
            cursor: "default",
          }}
        >
          {online ? <Wifi size={14} /> : <WifiOff size={14} />}
          {online ? (lang === "zh" ? "在线" : "online") : lang === "zh" ? "离线" : "offline"}
        </span>

        <button
          type="button"
          className="icon-btn"
          aria-label={`theme: ${theme}`}
          onClick={() => setTheme(nextTheme)}
          title={`theme: ${theme}`}
        >
          {theme === "dark" ? (
            <Moon size={14} />
          ) : theme === "auto" ? (
            <SunMoon size={14} />
          ) : (
            <Sun size={14} />
          )}
        </button>

        <button
          type="button"
          className="icon-btn"
          aria-label={lang === "zh" ? "Switch to English" : "切换到中文"}
          onClick={() => setLang(lang === "zh" ? "en" : "zh")}
        >
          <Languages size={14} />
          {lang === "zh" ? "EN" : "中"}
        </button>
      </div>
    </header>
  );
}

function useOnline() {
  const [online, setOnline] = useState(() =>
    typeof navigator === "undefined" ? true : navigator.onLine,
  );
  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);
  return online;
}
