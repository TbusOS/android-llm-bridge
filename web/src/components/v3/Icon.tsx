/**
 * Stroked 24px icon from the shared set (./icons.ts). The markup strings
 * are parsed into React elements once, so no HTML is injected: only the
 * four shape tags the set uses are accepted, anything else is dropped.
 */
import { createElement, type ReactElement } from "react";

import { ICONS, type IconName } from "./icons";

type Shape = { tag: "path" | "rect" | "circle" | "ellipse"; attrs: Record<string, string> };

const ALLOWED = new Set(["path", "rect", "circle", "ellipse"]);
const ATTR_MAP: Record<string, string> = {
  d: "d", x: "x", y: "y", width: "width", height: "height", rx: "rx", ry: "ry", cx: "cx", cy: "cy", r: "r",
};
const cache = new Map<string, Shape[]>();

export function parseIcon(markup: string): Shape[] {
  const hit = cache.get(markup);
  if (hit) return hit;
  const shapes: Shape[] = [];
  for (const m of markup.matchAll(/<(\w+)\s+([^>]*?)\/>/g)) {
    const tag = m[1] ?? "";
    if (!ALLOWED.has(tag)) continue;
    const attrs: Record<string, string> = {};
    for (const a of (m[2] ?? "").matchAll(/([\w-]+)='([^']*)'/g)) {
      const key = ATTR_MAP[a[1] ?? ""];
      if (key) attrs[key] = a[2] ?? "";
    }
    shapes.push({ tag: tag as Shape["tag"], attrs });
  }
  cache.set(markup, shapes);
  return shapes;
}

export interface IconProps {
  name: IconName | string;
  size?: "sm" | "md" | "lg";
  className?: string;
  title?: string;
}

export function Icon({ name, size = "md", className, title }: IconProps): ReactElement {
  const shapes = parseIcon(ICONS[name] ?? "");
  const cls = ["ic", size === "sm" ? "ic--sm" : size === "lg" ? "ic--lg" : "", className ?? ""]
    .filter(Boolean)
    .join(" ");
  return (
    <svg className={cls} viewBox="0 0 24 24" aria-hidden={title ? undefined : true} role={title ? "img" : undefined}>
      {title ? <title>{title}</title> : null}
      {shapes.map((s, i) => createElement(s.tag, { key: i, ...s.attrs }))}
    </svg>
  );
}
