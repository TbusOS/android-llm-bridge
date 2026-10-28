/**
 * A v2 feature component (the old Inspect tabs and top-level pages) shown
 * inside the v3 shell with a v3 page header. Their own markup keeps its
 * classes; the colour and type tokens they use are mapped onto Material 3
 * in styles/v3/legacy.css, so they pick up the theme and both languages.
 */
import { type ComponentType, type LazyExoticComponent, type ReactNode, Suspense } from "react";

import { Icon } from "../../components/v3/Icon";
import { PageHead } from "../../components/v3/ui";
import { useT } from "../../lib/i18n";
import { useApp } from "../../stores/app";

export interface LegacyTab {
  key: string;
  zh: string;
  en: string;
  Component: LazyExoticComponent<ComponentType> | ComponentType;
}

export function LegacyPage({
  zh,
  en,
  descZh,
  descEn,
  needsDevice,
  children,
}: {
  zh: string;
  en: string;
  descZh?: string;
  descEn?: string;
  needsDevice?: boolean;
  children: ReactNode;
}) {
  const t = useT();
  const device = useApp((s) => s.device);
  return (
    <>
      <PageHead
        eyebrow={needsDevice ? <span>{t("设备", "Device")}{device ? ` · ${device}` : ""}</span> : undefined}
        title={t(zh, en)}
        desc={descZh && descEn ? t(descZh, descEn) : undefined}
      />
      {needsDevice && !device ? (
        <div className="banner banner--warn" style={{ marginBottom: 16 }}>
          <Icon name="alert" />
          <p>{t("还没有选中设备。在顶栏的板子菜单里选一台 adb 设备。", "No device selected. Pick an adb device from the board menu in the top bar.")}</p>
          <span />
        </div>
      ) : null}
      <div className="legacy">
        <Suspense fallback={<div className="skeleton" style={{ height: 240 }} />}>{children}</Suspense>
      </div>
    </>
  );
}
