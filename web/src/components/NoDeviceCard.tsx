/**
 * NoDeviceCard — placeholder shown when no device is selected in
 * top-bar picker. 4 inspect tabs (UI Dump / Screenshot / Charts /
 * Files) had near-identical 13-line blocks; 5/08 主对话 L-meta-001
 * sweep pulled them into this single component.
 *
 * SystemInfoTab keeps its own sys-grid variant — that one's a
 * legitimate visual deviation (sys-grid is the tab's own chrome,
 * not the generic mock-card) so we don't force-fit it here.
 */

import { useApp } from "../stores/app";

export interface NoDeviceCardProps {
  /** Section title in Chinese (e.g. "UI 树" / "屏幕截图"). */
  titleZh: string;
  /** Section title in English (e.g. "UI Dump" / "Screenshot"). */
  titleEn: string;
}

export function NoDeviceCard({ titleZh, titleEn }: NoDeviceCardProps) {
  const lang = useApp((s) => s.lang);
  return (
    <div className="mock-card">
      <h1 style={{ fontSize: 22 }}>{lang === "zh" ? titleZh : titleEn}</h1>
      <p className="section-sub">
        {lang === "zh"
          ? "顶栏选一台设备再回这里。"
          : "Pick a device from the top-bar picker, then come back."}
      </p>
    </div>
  );
}
