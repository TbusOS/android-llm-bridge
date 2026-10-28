/**
 * Screen (v3): screenshots and the UI tree of what is on screen, as two tabs.
 * The tab is in the URL (`?tab=shot|tree`) so a link, a bookmark or the old
 * /inspect/ui-dump address can open the UI tree directly.
 */
import { useNavigate, useSearch } from "@tanstack/react-router";
import { lazy, Suspense } from "react";

import { Icon } from "../../../components/v3/Icon";
import { PageHead } from "../../../components/v3/ui";
import { useT } from "../../../lib/i18n";
import { useApp } from "../../../stores/app";

const ScreenshotTab = lazy(() => import("../../inspect/ScreenshotTab").then((m) => ({ default: m.ScreenshotTab })));
const UiDumpTab = lazy(() => import("../../inspect/UiDumpTab").then((m) => ({ default: m.UiDumpTab })));

type Tab = "shot" | "tree";

export function ScreenPage() {
  const t = useT();
  const device = useApp((s) => s.device);
  const search = useSearch({ strict: false }) as { tab?: string };
  const navigate = useNavigate();
  const tab: Tab = search.tab === "tree" ? "tree" : "shot";
  const pick = (x: Tab) => {
    if (x !== tab) void navigate({ to: "/screen", search: { tab: x } as never, replace: true });
  };
  return (
    <>
      <PageHead
        eyebrow={<span>{t("设备", "Device")}{device ? ` · ${device}` : ""}</span>}
        title={t("屏幕", "Screen")}
        desc={t("截屏,以及当前界面的控件树。", "Screenshots, and the view tree of what is on screen.")}
      />
      <article className="card card--flush">
        <div className="tabs" role="tablist" aria-label={t("屏幕", "Screen")}>
          <button type="button" className="tab" role="tab" aria-selected={tab === "shot"} onClick={() => pick("shot")}>
            <Icon name="camera" size="sm" />
            {t("截屏", "Screenshots")}
          </button>
          <button type="button" className="tab" role="tab" aria-selected={tab === "tree"} onClick={() => pick("tree")}>
            <Icon name="list" size="sm" />
            {t("UI 树", "UI tree")}
          </button>
        </div>
        <div className="legacy" role="tabpanel">
          <Suspense fallback={<div className="skeleton" style={{ height: 240 }} />}>{tab === "shot" ? <ScreenshotTab /> : <UiDumpTab />}</Suspense>
        </div>
      </article>
    </>
  );
}
