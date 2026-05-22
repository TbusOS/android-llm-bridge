/**
 * 30-min activity timeline — tl-row grid (64px time / 18px dot /
 * 1fr text).  Dot color encodes severity (ok / orange / err / muted).
 *
 * Renders `TimelineEventData.parts` as React nodes — no
 * `dangerouslySetInnerHTML`, no manual escape. Producers (currently
 * `useAudit.mapAuditToTimeline`) supply a list of `{kind, value}`
 * segments and we map each kind to a `<code>` / `<span class=secondary>`
 * / raw text node. New producers can't accidentally inject markup
 * because the type system requires structured parts, not a string.
 */
import type { ReactNode } from "react";
import { useApp } from "../../stores/app";
import type { TimelineEventData, TimelineTextPart } from "./types";

interface Props {
  events: TimelineEventData[];
}

function renderPart(p: TimelineTextPart, i: number): ReactNode {
  if (p.kind === "code") return <code key={i}>{p.value}</code>;
  if (p.kind === "em")
    return (
      <span key={i} className="secondary">
        {p.value}
      </span>
    );
  return <span key={i}>{p.value}</span>;
}

export function ActivityTimeline({ events }: Props) {
  const lang = useApp((s) => s.lang);

  return (
    <div className="timeline">
      {events.map((evt, idx) => {
        const parts = lang === "zh" ? evt.partsZh : evt.parts;
        return (
          <div key={`${evt.time}-${idx}`} className="tl-row">
            <div className="tl-time">{evt.time}</div>
            <div className={`tl-dot tl-dot--${evt.dot}`} />
            <div className="tl-text">{parts.map(renderPart)}</div>
          </div>
        );
      })}
    </div>
  );
}
