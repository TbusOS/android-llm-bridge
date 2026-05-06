/**
 * SectionPlaceholder — DEBT-018 close.
 *
 * Replaces 4 hand-rolled loading/error/empty <div> blocks across
 * DashboardPage (devices · backends · sessions · timeline). Each
 * section had its own ~30-line ternary chain; this component is the
 * single mount point for those tri-states.
 *
 * Class names are passed through verbatim — this is **not** a visual
 * refactor. Three BEM families coexist on Dashboard today (mockup
 * `.be-card--empty` solid border + 2 React-side additions
 * `.dev-strip-state` dashed and `.sess-card--state` inherited). Mockup
 * baseline is `docs/webui-preview-v2.html` and only defines the first;
 * unifying visual style would mean editing the baseline + re-running
 * the design-review three-gate, which is out of scope here. Tracked as
 * follow-up debt (see debts.md).
 *
 * Why parametrize on `styleKey` instead of `className`:
 *   - Caller doesn't reason about modifier strings (kind=error → which
 *     `--err` modifier the family supports).
 *   - The component is the single source of truth for the
 *     class-mapping table, so a future BEM consolidation lands in one
 *     place rather than touched in 4 callsites.
 */
import type { ReactNode } from "react";

export type PlaceholderKind = "loading" | "error" | "empty";

export type PlaceholderStyleKey = "dev-strip" | "be-card" | "sess-card";

const CLASS_MAP: Record<
  PlaceholderStyleKey,
  Record<PlaceholderKind, string>
> = {
  // dashed border + center align (devices + activity timeline)
  "dev-strip": {
    loading: "dev-strip-state",
    empty: "dev-strip-state",
    error: "dev-strip-state dev-strip-state--err",
  },
  // mockup baseline: solid border, no err modifier in CSS today.
  // backends section uses a single visual for all 3 kinds — text
  // content carries the semantic distinction.
  "be-card": {
    loading: "be-card--empty",
    empty: "be-card--empty",
    error: "be-card--empty",
  },
  // sess-card is the parent container; --state adds padding/center,
  // --err overrides text color.
  "sess-card": {
    loading: "sess-card sess-card--state",
    empty: "sess-card sess-card--state",
    error: "sess-card sess-card--state sess-card--err",
  },
};

export interface SectionPlaceholderProps {
  styleKey: PlaceholderStyleKey;
  kind: PlaceholderKind;
  children: ReactNode;
}

export function SectionPlaceholder({
  styleKey,
  kind,
  children,
}: SectionPlaceholderProps) {
  return <div className={CLASS_MAP[styleKey][kind]}>{children}</div>;
}
