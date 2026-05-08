/**
 * SectionPlaceholder — DEBT-018 close + DEBT-031 close.
 *
 * Shared UI primitive for any section's loading/error/empty placeholder
 * area. First landed on Dashboard's 4 sections (devices / backends /
 * sessions / timeline); now also used by inspect/ScreenshotTab's empty
 * viewer state. Lives in `web/src/components/` because both
 * `features/dashboard/` and `features/inspect/` consume it — keeping
 * it inside one feature would create a horizontal feature→feature
 * import (architecture-reviewer 2026-05-08 finding).
 *
 * DEBT-031 (2026-05-07): the 3 separate BEM families this component
 * used to map to (`dev-strip-state` / `be-card--empty` /
 * `sess-card--state`) are gone — all four sections now emit a single
 * `.section-placeholder` block with one of three visual variants
 * (dashed / solid / inset) and an optional `--err` color modifier.
 * `styleKey` API stays for callsite stability; under the hood it
 * picks a variant. Mockup baseline carries the matching CSS — see
 * docs/webui-preview-v2.html DEBT-031 comment.
 */
import type { ReactNode } from "react";

export type PlaceholderKind = "loading" | "error" | "empty";

/** Visual variant. Names map to mockup baseline modifiers:
 *    dev-strip  → --dashed (border-dashed + center)
 *    be-card    → --solid  (border-solid)
 *    sess-card  → --inset  (no border; nested in parent .sess-card)
 *  These three were the visual rhythms picked for the 4 dashboard
 *  sections; the API uses the legacy keys so callsites don't churn. */
export type PlaceholderStyleKey = "dev-strip" | "be-card" | "sess-card";

const VARIANT_CLASS: Record<PlaceholderStyleKey, string> = {
  "dev-strip": "section-placeholder--dashed",
  "be-card": "section-placeholder--solid",
  "sess-card": "section-placeholder--inset",
};

const ERR_CLASS = "section-placeholder--err";

/** sess-card variant is rendered inside an outer .sess-card chrome
 *  to keep the rounded border shared with the row list above it. */
const NEEDS_SESS_CARD_WRAP: PlaceholderStyleKey = "sess-card";

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
  const cls = [
    "section-placeholder",
    VARIANT_CLASS[styleKey],
    kind === "error" ? ERR_CLASS : null,
  ]
    .filter(Boolean)
    .join(" ");

  if (styleKey === NEEDS_SESS_CARD_WRAP) {
    return (
      <div className="sess-card">
        <div className={cls}>{children}</div>
      </div>
    );
  }
  return <div className={cls}>{children}</div>;
}
