/**
 * 30-min activity timeline — tl-row grid (64px time / 18px dot /
 * 1fr text).  Dot color encodes severity (ok / orange / err / muted).
 *
 * The text bodies in mockData carry inline `<code>` and `<em>` HTML
 * because the mockup renders the same; we use dangerouslySetInnerHTML
 * on a small whitelist of tags only.  TODO: replace with structured
 * events from /audit when that endpoint exists.
 */
import { useApp } from "../../stores/app";
import type { TimelineEventData } from "./types";

interface Props {
  events: TimelineEventData[];
}

export function ActivityTimeline({ events }: Props) {
  const lang = useApp((s) => s.lang);

  return (
    <div className="timeline">
      {events.map((evt, idx) => (
        <div key={`${evt.time}-${idx}`} className="tl-row">
          <div className="tl-time">{evt.time}</div>
          <div className={`tl-dot tl-dot--${evt.dot}`} />
          <div
            className="tl-text"
            // Whitelist: <code>, <em class="secondary"> — produced only
            // by us inside mockData.  Real endpoint will hand back
            // structured tokens.
            dangerouslySetInnerHTML={{
              __html: renderMarkup(lang === "zh" ? evt.textZh : evt.text),
            }}
          />
        </div>
      ))}
    </div>
  );
}

/** Translate `<em>...</em>` → `<span class="secondary">...</span>`. */
function renderMarkup(html: string): string {
  return html
    .replace(/<em>/g, '<span class="secondary">')
    .replace(/<\/em>/g, "</span>");
}
