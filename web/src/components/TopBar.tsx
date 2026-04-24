import { Moon, Sun, Languages, Wifi, WifiOff } from "lucide-react";
import { useEffect, useState } from "react";
import { useApp } from "../stores/app";

export function TopBar() {
  const theme = useApp((s) => s.theme);
  const lang = useApp((s) => s.lang);
  const setTheme = useApp((s) => s.setTheme);
  const setLang = useApp((s) => s.setLang);
  const online = useOnline();

  const nextTheme = theme === "light" ? "dark" : theme === "dark" ? "auto" : "light";

  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-3)",
        padding: "var(--space-3) var(--space-5)",
        borderBottom: "1px solid var(--anth-light-gray)",
        background: "var(--anth-bg)",
        position: "sticky",
        top: 0,
        zIndex: 10,
      }}
    >
      <span
        title={online ? "Connected to the internet" : "Offline — cloud backends hidden"}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          fontFamily: "var(--font-heading)",
          fontSize: 12,
          color: online ? "var(--anth-green)" : "var(--anth-danger)",
        }}
      >
        {online ? <Wifi size={14} /> : <WifiOff size={14} />}
        {online ? (lang === "zh" ? "在线" : "online") : lang === "zh" ? "离线" : "offline"}
      </span>

      <div style={{ marginLeft: "auto", display: "flex", gap: "var(--space-2)" }}>
        <IconButton
          label={`theme: ${theme}`}
          onClick={() => setTheme(nextTheme)}
        >
          {theme === "dark" ? <Moon size={16} /> : <Sun size={16} />}
        </IconButton>
        <IconButton
          label={lang === "zh" ? "English" : "中文"}
          onClick={() => setLang(lang === "zh" ? "en" : "zh")}
        >
          <Languages size={16} />
          <span style={{ marginLeft: 4, fontFamily: "var(--font-heading)", fontSize: 12 }}>
            {lang === "zh" ? "EN" : "中"}
          </span>
        </IconButton>
      </div>
    </header>
  );
}

function IconButton(props: {
  children: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={props.label}
      onClick={props.onClick}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "6px 10px",
        border: "1px solid var(--anth-light-gray)",
        borderRadius: "var(--radius-pill)",
        background: "transparent",
        color: "var(--anth-text-secondary)",
        cursor: "pointer",
        fontFamily: "var(--font-heading)",
      }}
    >
      {props.children}
    </button>
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
