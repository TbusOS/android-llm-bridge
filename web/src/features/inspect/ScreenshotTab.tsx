/**
 * Inspect → Screenshot — capture device framebuffer + browse history
 * (functional LOW-1).
 *
 * v2 layout (mirrors UartCaptureView):
 *   - Top bar: Capture button + Download + last-shot meta.
 *   - Left sidebar: history list (newest first), driven by
 *     GET /devices/{serial}/screenshots.
 *   - Right viewer: <img> for the selected shot.
 *
 * Sidebar reuses `.uart-tab__list` / `.uart-tab__body` BEM block
 * (`.uart-tab__cap-name`, `.is-active`, etc.) — N=2 use of the capture-
 * sidebar visual is below the L-020 N=3 abstract-base threshold, so we
 * keep the existing class names instead of inventing a `.capture-list`
 * BEM block. Any third sidebar tab (logcat history, etc.) triggers the
 * extraction.
 *
 * Image fetching:
 *   - Right after a fresh capture, render the inline base64 from the
 *     POST response (avoids the second-round GET roundtrip).
 *   - On selection of a historical entry, switch to `<img src=...>`
 *     pointing at GET /devices/{serial}/screenshots/{name} so the
 *     browser handles caching and lazy-load naturally.
 */

import { useEffect, useState } from "react";
import { Camera, Download, RefreshCw, Trash2 } from "lucide-react";

import { useApp } from "../../stores/app";
import {
  screenshotImageUrl,
  type ScreenshotResponse,
} from "../../lib/api";
import { formatBytes } from "../../lib/format";
import { NoDeviceCard } from "../../components/NoDeviceCard";
import { SectionPlaceholder } from "../../components/SectionPlaceholder";
import { useArmedAction } from "../../lib/useArmedAction";
import {
  useDeleteScreenshotMutation,
  useScreenshots,
  useTriggerScreenshot,
} from "./useScreenshots";

interface ScreenshotRowProps {
  name: string;
  width: number | null;
  height: number | null;
  size: number;
  mtime: number;
  lang: "zh" | "en";
  isActive: boolean;
  deleting: boolean;
  onSelect: () => void;
  onConfirmDelete: () => void;
}

function ScreenshotRow({
  name,
  width,
  height,
  size,
  mtime,
  lang,
  isActive,
  deleting,
  onSelect,
  onConfirmDelete,
}: ScreenshotRowProps) {
  // Each row owns its own armed state; the 8s auto-disarm timer caps
  // the "two armed at once" window so we don't need a parent-level
  // single-armed coordinator.
  const armed = useArmedAction(onConfirmDelete);

  return (
    <li className={isActive ? "is-active" : undefined}>
      <div className="screenshot-row">
        <button
          type="button"
          className="screenshot-row__select"
          onClick={onSelect}
          aria-current={isActive ? "true" : undefined}
        >
          <div className="uart-tab__cap-name">{name}</div>
          <div className="uart-tab__cap-meta">
            {width && height ? `${width}×${height} · ` : ""}
            {formatBytes(size)} · {relativeTime(mtime, lang)}
          </div>
        </button>
        <button
          type="button"
          className={`screenshot-row__del${
            armed.armed ? " is-armed" : ""
          }`}
          aria-label={
            armed.armed
              ? lang === "zh"
                ? "再点一次确认删除"
                : "click again to confirm delete"
              : lang === "zh"
                ? "删除"
                : "delete"
          }
          title={armed.armed ? "click again to confirm" : "delete"}
          disabled={deleting}
          onClick={armed.trigger}
        >
          <Trash2 size={12} aria-hidden={true} />
        </button>
      </div>
    </li>
  );
}

function relativeTime(mtimeSec: number, lang: "zh" | "en"): string {
  const diffSec = Math.max(0, Date.now() / 1000 - mtimeSec);
  if (diffSec < 60) {
    return lang === "zh"
      ? `${Math.floor(diffSec)} 秒前`
      : `${Math.floor(diffSec)}s ago`;
  }
  if (diffSec < 3600) {
    return lang === "zh"
      ? `${Math.floor(diffSec / 60)} 分钟前`
      : `${Math.floor(diffSec / 60)}m ago`;
  }
  if (diffSec < 86400) {
    return lang === "zh"
      ? `${Math.floor(diffSec / 3600)} 小时前`
      : `${Math.floor(diffSec / 3600)}h ago`;
  }
  return lang === "zh"
    ? `${Math.floor(diffSec / 86400)} 天前`
    : `${Math.floor(diffSec / 86400)}d ago`;
}

export function ScreenshotTab() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  // selected: name of the shot currently shown. null means "show the
  // freshly-captured one (last)" if it exists, else empty viewer.
  const [selected, setSelected] = useState<string | null>(null);
  // last: the most recent capture's POST response, kept around so the
  // viewer can render its base64 inline without waiting for a 2nd GET.
  const [last, setLast] = useState<ScreenshotResponse | null>(null);
  // ui-fluency 2026-05-07 LOW-3: avoid 100-300ms white-flash when
  // switching between historical entries. Track loaded state per src
  // so we can fade-in the new image; the same <img> element is reused
  // (no key) so the browser keeps the old pixels until onLoad fires.
  const [imgLoaded, setImgLoaded] = useState(false);

  const list = useScreenshots(device);
  const trigger = useTriggerScreenshot(device, (data) => {
    setLast(data);
    if (data?.screenshot?.filename) {
      setSelected(data.screenshot.filename);
    }
  });
  const remove = useDeleteScreenshotMutation(device);

  // First-mount auto-select: if user opens the tab and there's already
  // history, show the newest. Skip if user already picked something.
  useEffect(() => {
    const newest = list.data?.screenshots?.[0];
    if (!selected && newest) {
      setSelected(newest.name);
    }
  }, [list.data, selected]);

  // Reset imgLoaded each time the displayed image changes — fade in
  // when onLoad fires.
  useEffect(() => {
    setImgLoaded(false);
  }, [selected]);

  if (!device) {
    return <NoDeviceCard titleZh="屏幕截图" titleEn="Screenshot" />;
  }

  // Pick the right image source. Prefer the inline base64 from the
  // mutation when its filename matches `selected` — saves one HTTP
  // round-trip right after a fresh capture. Fall back to the URL form
  // for historical entries.
  let imgSrc: string | null = null;
  let imgAlt = "device screenshot";
  if (selected) {
    if (last?.screenshot?.filename === selected && last.screenshot.png_base64) {
      imgSrc = `data:image/png;base64,${last.screenshot.png_base64}`;
    } else if (device) {
      imgSrc = screenshotImageUrl(device, selected);
    }
    imgAlt = `screenshot ${selected}`;
  }

  const downloadHref = imgSrc;
  const downloadName = selected ?? "screenshot.png";
  const captureFailed = trigger.data?.ok === false ? trigger.data.error : null;

  return (
    <div className="screenshot-tab">
      <div className="uart-tab__bar">
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => trigger.mutate()}
          disabled={trigger.isPending}
        >
          <Camera size={12} className="icon-inline" />{" "}
          {trigger.isPending
            ? lang === "zh" ? "抓取中…" : "Capturing…"
            : lang === "zh" ? "抓屏" : "Capture"}
        </button>
        {imgSrc && downloadHref && (
          <a className="btn" href={downloadHref} download={downloadName}>
            <Download size={12} className="icon-inline" />{" "}
            {lang === "zh" ? "下载 PNG" : "Download"}
          </a>
        )}
        <button
          type="button"
          className="btn"
          onClick={() => list.refetch()}
          aria-label={lang === "zh" ? "刷新历史列表" : "Refresh history"}
        >
          <RefreshCw size={12} className="icon-inline" />
        </button>
        {captureFailed && (
          <span className="uart-tab__last uart-tab__last--err">
            {captureFailed}
          </span>
        )}
      </div>

      <div className="uart-tab__body">
        <aside className="uart-tab__list">
          <div className="uart-tab__list-head">
            {lang === "zh" ? "历史 截图" : "Screenshots"}
            {list.data?.screenshots && (
              <span className="uart-tab__count">
                {list.data.screenshots.length}
              </span>
            )}
          </div>
          {list.isLoading && (
            <div className="uart-tab__empty">
              {lang === "zh" ? "加载中…" : "Loading…"}
            </div>
          )}
          {list.isError && (
            <div className="uart-tab__empty uart-tab__empty--err">
              {lang === "zh" ? "无法获取列表" : "Couldn't load list"}
            </div>
          )}
          {list.data?.screenshots && list.data.screenshots.length === 0 && (
            <div className="uart-tab__empty">
              {lang === "zh"
                ? "还没有截图 · 点击上方「抓屏」"
                : "No screenshots yet — press Capture above"}
            </div>
          )}
          <ul>
            {list.data?.screenshots?.map((s) => (
              <ScreenshotRow
                key={s.name}
                name={s.name}
                width={s.width ?? null}
                height={s.height ?? null}
                size={s.size_bytes}
                mtime={s.mtime}
                lang={lang}
                isActive={selected === s.name}
                deleting={
                  remove.isPending && remove.variables === s.name
                }
                onSelect={() => setSelected(s.name)}
                onConfirmDelete={() => {
                  remove.mutate(s.name, {
                    onSuccess: () => {
                      if (selected === s.name) setSelected(null);
                      if (last?.screenshot?.filename === s.name) setLast(null);
                    },
                  });
                }}
              />
            ))}
          </ul>
        </aside>

        <main className="screenshot-tab__viewer">
          {imgSrc ? (
            <img
              src={imgSrc}
              alt={imgAlt}
              className="screenshot-tab__img"
              style={{
                opacity: imgLoaded ? 1 : 0.4,
                transition: "opacity 180ms ease",
              }}
              onLoad={() => setImgLoaded(true)}
            />
          ) : (
            <SectionPlaceholder styleKey="be-card" kind="empty">
              {lang === "zh"
                ? "点上方「抓屏」，或从左侧选历史"
                : "Press Capture above, or pick one from the left"}
            </SectionPlaceholder>
          )}
        </main>
      </div>
    </div>
  );
}
