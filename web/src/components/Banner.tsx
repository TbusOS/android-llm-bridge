/**
 * Top banner — shown on every page. Displays offline state and a link
 * back to the project home. Kept dependency-free so it renders even when
 * the API is unreachable.
 */
import { useEffect, useState } from "react";

export function Banner() {
  const [online, setOnline] = useState(() =>
    typeof navigator === "undefined" ? true : navigator.onLine,
  );
  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-4)",
        marginBottom: "var(--space-6)",
        paddingBottom: "var(--space-4)",
        borderBottom: "1px solid var(--anth-light-gray)",
      }}
    >
      <strong style={{ fontFamily: "var(--font-heading)", fontSize: 18 }}>
        android-llm-bridge
      </strong>
      <span
        style={{
          fontFamily: "var(--font-heading)",
          fontSize: 11,
          textTransform: "uppercase",
          letterSpacing: "0.04em",
          padding: "2px 8px",
          borderRadius: "var(--radius-pill)",
          background: online ? "rgba(120,140,93,0.18)" : "rgba(161,66,56,0.18)",
          color: online ? "#5d7043" : "var(--anth-danger)",
        }}
      >
        {online ? "online" : "offline"}
      </span>
      <a
        href="../"
        style={{ marginLeft: "auto", fontSize: 13, textDecoration: "none" }}
      >
        ← docs home
      </a>
    </header>
  );
}
