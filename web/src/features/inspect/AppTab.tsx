/**
 * AppTab — Inspect sub-tab for installed-app management.
 *
 * Two columns:
 *   sidebar — install apk (upload widget) + start/stop/clear by name
 *   main    — installed packages list + selected-package detail panel
 *             (version / code / install timestamps / requested perms +
 *             start / stop / clear data / uninstall buttons)
 *
 * Mockup baseline: `docs/webui-preview-v2-app.html` — BEM class names
 * here must mirror it (L-028).
 */
import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

import { useApp } from "../../stores/app";
import { type AppEnvelope } from "../../lib/api";
import { useArmedAction } from "../../lib/useArmedAction";
import {
  useAppClearDataMutation,
  useAppInfo,
  useAppInstallMutation,
  useAppList,
  useAppStartMutation,
  useAppStopMutation,
  useAppUninstallMutation,
} from "./useAppActions";

function envText(env: AppEnvelope<unknown> | undefined, okText: string): {
  text: string;
  status: "ok" | "err" | "idle";
} {
  if (!env) return { text: "", status: "idle" };
  if (!env.ok) {
    return {
      text: `${env.error?.code ?? "ERROR"}: ${env.error?.message ?? ""}`,
      status: "err",
    };
  }
  return { text: okText, status: "ok" };
}

function InstallCard({ device }: { device: string | null }) {
  const lang = useApp((s) => s.lang);
  const fileRef = useRef<HTMLInputElement>(null);
  const [replace, setReplace] = useState(true);
  const [grantRuntime, setGrantRuntime] = useState(false);
  const [downgrade, setDowngrade] = useState(false);

  const mut = useAppInstallMutation(device);

  const onInstall = () => {
    const f = fileRef.current?.files?.[0];
    if (!f) return;
    mut.mutate({ file: f, replace, grantRuntime, downgrade });
  };
  const env = envText(mut.data, "");

  return (
    <section className="app-card">
      <header className="app-card__head">
        <h2 className="app-card__title">install apk</h2>
      </header>
      <p className="app-card__hint">
        {lang === "zh"
          ? "上传 apk 推到设备 · pm install。"
          : "Push an APK file and run `pm install` on the device."}
      </p>
      <div className="app-card__row">
        <input ref={fileRef} type="file" accept=".apk" disabled={mut.isPending} />
      </div>
      <div className="app-card__row">
        <label>
          <input
            type="checkbox"
            checked={replace}
            onChange={(e) => setReplace(e.target.checked)}
            disabled={mut.isPending}
          />{" "}
          replace
        </label>
        <label>
          <input
            type="checkbox"
            checked={grantRuntime}
            onChange={(e) => setGrantRuntime(e.target.checked)}
            disabled={mut.isPending}
          />{" "}
          grant runtime
        </label>
        <label>
          <input
            type="checkbox"
            checked={downgrade}
            onChange={(e) => setDowngrade(e.target.checked)}
            disabled={mut.isPending}
          />{" "}
          downgrade
        </label>
      </div>
      <div className="app-card__row">
        <button
          type="button"
          className="app-btn"
          disabled={!device || mut.isPending}
          onClick={onInstall}
        >
          {mut.isPending
            ? lang === "zh"
              ? "安装中…"
              : "installing…"
            : lang === "zh"
              ? "安装"
              : "install"}
        </button>
      </div>
      {mut.data?.ok && (
        <div className="app-result" data-status="ok">
          {lang === "zh" ? "ok · 安装完成" : "ok · install succeeded"}
        </div>
      )}
      {env.status === "err" && (
        <div className="app-result" data-status="err">
          {env.text}
        </div>
      )}
    </section>
  );
}

function NameOpsCard({ device }: { device: string | null }) {
  const lang = useApp((s) => s.lang);
  const [name, setName] = useState("");
  const startM = useAppStartMutation(device);
  const stopM = useAppStopMutation(device);
  const clearM = useAppClearDataMutation(device);
  const last = [startM.data, stopM.data, clearM.data].find((d) => d);
  const env = envText(last, "");
  const pending = startM.isPending || stopM.isPending || clearM.isPending;

  return (
    <section className="app-card">
      <header className="app-card__head">
        <h2 className="app-card__title">
          {lang === "zh" ? "按名称启动 / 停止 / 清数据" : "start / stop / clear by name"}
        </h2>
      </header>
      <div className="app-card__row">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="com.example.app or pkg/.Activity"
          disabled={pending}
        />
      </div>
      <div className="app-card__row">
        <button
          type="button"
          className="app-btn"
          disabled={!device || !name.trim() || pending}
          onClick={() => startM.mutate(name.trim())}
        >
          start
        </button>
        <button
          type="button"
          className="app-btn"
          disabled={!device || !name.trim() || pending}
          onClick={() => stopM.mutate(name.trim())}
        >
          stop
        </button>
        <button
          type="button"
          className="app-btn app-btn--danger"
          disabled={!device || !name.trim() || pending}
          onClick={() => clearM.mutate(name.trim())}
        >
          clear data
        </button>
      </div>
      {last?.ok && (
        <div className="app-result" data-status="ok">
          ok · {JSON.stringify(last.data ?? {})}
        </div>
      )}
      {env.status === "err" && (
        <div className="app-result" data-status="err">
          {env.text}
        </div>
      )}
    </section>
  );
}

function PackageDetail({
  device,
  pkg,
}: {
  device: string | null;
  pkg: string | null;
}) {
  const lang = useApp((s) => s.lang);
  const info = useAppInfo(device, pkg);
  const startM = useAppStartMutation(device);
  const stopM = useAppStopMutation(device);
  const clearM = useAppClearDataMutation(device);
  const uninstallM = useAppUninstallMutation(device);

  const uninstall = useArmedAction(() => uninstallM.mutate(pkg!));

  useEffect(() => {
    uninstall.disarm();
    [startM, stopM, clearM, uninstallM].forEach((m) => m.reset());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pkg]);

  if (!pkg) {
    return (
      <div className="app-detail">
        <p className="app-card__hint">
          {lang === "zh"
            ? "选择一个包查看详情。"
            : "Select a package to see details."}
        </p>
      </div>
    );
  }

  const d = info.data?.data;
  const pending =
    startM.isPending ||
    stopM.isPending ||
    clearM.isPending ||
    uninstallM.isPending;
  const lastOp = [startM.data, stopM.data, clearM.data, uninstallM.data].find(
    (x) => x,
  );

  return (
    <div className="app-detail">
      <h3 className="app-detail__name">{pkg}</h3>
      {info.isLoading && (
        <p className="app-card__hint">
          {lang === "zh" ? "读取详情…" : "loading…"}
        </p>
      )}
      {info.data && !info.data.ok && (
        <div className="app-result" data-status="err">
          {info.data.error?.code}: {info.data.error?.message}
        </div>
      )}
      {d && (
        <>
          <div className="app-detail__meta">
            <span className="app-detail__meta-label">
              {lang === "zh" ? "版本" : "version"}
            </span>
            <span className="app-detail__meta-value">{d.version_name || "—"}</span>
            <span className="app-detail__meta-label">code</span>
            <span className="app-detail__meta-value">{d.version_code || "—"}</span>
            <span className="app-detail__meta-label">
              {lang === "zh" ? "首装" : "installed"}
            </span>
            <span className="app-detail__meta-value">{d.first_install_time || "—"}</span>
            <span className="app-detail__meta-label">
              {lang === "zh" ? "更新" : "updated"}
            </span>
            <span className="app-detail__meta-value">{d.last_update_time || "—"}</span>
          </div>
          {d.requested_permissions.length > 0 && (
            <div>
              <span className="app-detail__meta-label">
                {lang === "zh" ? "申请权限" : "requested permissions"}
              </span>
              <div className="app-detail__perm" style={{ marginTop: 4 }}>
                {d.requested_permissions.map((p) => (
                  <span key={p} className="app-detail__perm-chip">
                    {p.replace(/^android\.permission\./, "")}
                  </span>
                ))}
              </div>
            </div>
          )}
        </>
      )}
      <div className="app-detail__actions">
        <button
          type="button"
          className="app-btn"
          disabled={pending}
          onClick={() => startM.mutate(pkg)}
        >
          start
        </button>
        <button
          type="button"
          className="app-btn"
          disabled={pending}
          onClick={() => stopM.mutate(pkg)}
        >
          stop
        </button>
        <button
          type="button"
          className="app-btn app-btn--danger"
          disabled={pending}
          onClick={() => clearM.mutate(pkg)}
        >
          clear data
        </button>
        <button
          type="button"
          className={`app-btn app-btn--danger${uninstall.armed ? " is-armed" : ""}`}
          disabled={pending}
          onClick={uninstall.trigger}
        >
          {uninstallM.isPending
            ? lang === "zh"
              ? "卸载中…"
              : "uninstalling…"
            : uninstall.armed
              ? lang === "zh"
                ? "⚠ 再点确认（8 秒）"
                : "⚠ click again to confirm (8 s)"
              : "uninstall"}
        </button>
        {uninstall.armed && (
          <button
            type="button"
            className="app-btn app-btn--ghost"
            onClick={uninstall.disarm}
          >
            {lang === "zh" ? "取消" : "cancel"}
          </button>
        )}
      </div>
      {lastOp?.ok && (
        <div className="app-result" data-status="ok">
          ok · {JSON.stringify(lastOp.data ?? {})}
        </div>
      )}
      {lastOp && !lastOp.ok && (
        <div className="app-result" data-status="err">
          {lastOp.error?.code}: {lastOp.error?.message}
        </div>
      )}
    </div>
  );
}

// Single row height (px) — used for virtualizer estimateSize.  Lines
// are 12.5px monospace + 6px vertical padding × 2 ≈ 28 px total.
const _PKG_ROW_HEIGHT = 28;

function PackageList({
  device,
  selected,
  onSelect,
}: {
  device: string | null;
  selected: string | null;
  onSelect: (p: string) => void;
}) {
  const lang = useApp((s) => s.lang);
  const [filter, setFilter] = useState("");
  const deferredFilter = useDeferredValue(filter);
  const [includeSystem, setIncludeSystem] = useState(false);

  // perf HIGH#7: filter is now CLIENT-side — each keystroke only
  // re-derives via useMemo, no /api/app/list refetch.  The query
  // re-fires only when device / includeSystem actually changes.
  const list = useAppList(device, includeSystem);

  const allPackages = list.data?.ok ? list.data.data?.packages ?? [] : [];
  const visiblePackages = useMemo(() => {
    if (!deferredFilter) return allPackages;
    const q = deferredFilter.toLowerCase();
    return allPackages.filter((p) => p.toLowerCase().includes(q));
  }, [allPackages, deferredFilter]);

  // perf HIGH#7: virtualize the list.  Device with 500+ packages
  // previously rendered every <li> and paid layout cost on each
  // keystroke; only render visible rows now (mirrors UiDumpTab pattern).
  const listRef = useRef<HTMLDivElement | null>(null);
  const rowVirtualizer = useVirtualizer({
    count: visiblePackages.length,
    getScrollElement: () => listRef.current,
    estimateSize: () => _PKG_ROW_HEIGHT,
    overscan: 12,
  });

  return (
    <section className="app-list-card">
      <header className="app-list-card__head">
        <h2 className="app-list-card__title">
          {lang === "zh" ? "已安装的包" : "installed packages"}
        </h2>
        <div className="app-list-card__filter">
          <input
            type="text"
            placeholder={lang === "zh" ? "筛选" : "filter"}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <label>
            <input
              type="checkbox"
              checked={includeSystem}
              onChange={(e) => setIncludeSystem(e.target.checked)}
            />{" "}
            {lang === "zh" ? "含系统" : "system"}
          </label>
          {filter && allPackages.length > 0 && (
            <span
              className="app-card__hint"
              style={{ margin: 0 }}
              role="status"
              aria-live="polite"
            >
              {lang === "zh"
                ? `${visiblePackages.length} / ${allPackages.length} 匹配`
                : `${visiblePackages.length} of ${allPackages.length} matches`}
            </span>
          )}
          <button
            type="button"
            className="app-btn app-btn--ghost"
            disabled={list.isFetching}
            onClick={() => {
              void list.refetch();
            }}
          >
            {list.isFetching
              ? lang === "zh"
                ? "…"
                : "…"
              : lang === "zh"
                ? "刷新"
                : "refresh"}
          </button>
        </div>
      </header>

      <div className="app-list-card__body">
        <div
          className="app-pkg-list"
          ref={listRef}
          role="listbox"
          aria-label={lang === "zh" ? "已安装的包" : "Installed packages"}
          onKeyDown={(e) => {
            // ui-fluency HIGH#5: keyboard nav for the package list.
            //   ↓ / ↑  — move selection
            //   Home / End — jump to ends
            //   Enter — re-trigger select (no-op if same package)
            if (visiblePackages.length === 0) return;
            const curIdx = selected
              ? visiblePackages.indexOf(selected)
              : -1;
            let next = curIdx;
            if (e.key === "ArrowDown") next = Math.min(
              visiblePackages.length - 1, curIdx + 1,
            );
            else if (e.key === "ArrowUp") next = Math.max(0, curIdx - 1);
            else if (e.key === "Home") next = 0;
            else if (e.key === "End") next = visiblePackages.length - 1;
            else return;
            e.preventDefault();
            const pkg = visiblePackages[next];
            if (pkg) {
              onSelect(pkg);
              rowVirtualizer.scrollToIndex(next, { align: "auto" });
            }
          }}
          tabIndex={0}
        >
          {list.isLoading && (
            <div className="app-pkg" style={{ cursor: "default" }}>
              {lang === "zh" ? "读取中…" : "loading…"}
            </div>
          )}
          {list.data && !list.data.ok && (
            <div
              className="app-pkg"
              style={{ cursor: "default", color: "#b54b3d" }}
              role="alert"
            >
              {list.data.error?.code}: {list.data.error?.message}
            </div>
          )}
          {list.data?.ok &&
            allPackages.length === 0 && (
              <div className="app-pkg" style={{ cursor: "default" }}>
                {lang === "zh" ? "没有匹配的包" : "no matching packages"}
              </div>
            )}
          {list.data?.ok && allPackages.length > 0 && visiblePackages.length === 0 && (
            <div className="app-pkg" style={{ cursor: "default" }}>
              {lang === "zh"
                ? `没有包匹配 "${filter}"`
                : `No packages match "${filter}"`}
            </div>
          )}
          {visiblePackages.length > 0 && (
            <div
              className="app-pkg-list__virt"
              style={{
                height: rowVirtualizer.getTotalSize(),
                position: "relative",
              }}
            >
              {rowVirtualizer.getVirtualItems().map((vRow) => {
                const p = visiblePackages[vRow.index];
                if (!p) return null;
                const isActive = selected === p;
                return (
                  <button
                    type="button"
                    key={p}
                    className={`app-pkg${isActive ? " is-active" : ""}`}
                    role="option"
                    aria-selected={isActive}
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 0,
                      right: 0,
                      transform: `translateY(${vRow.start}px)`,
                    }}
                    onClick={() => onSelect(p)}
                  >
                    {p}
                  </button>
                );
              })}
            </div>
          )}
        </div>
        <PackageDetail device={device} pkg={selected} />
      </div>
    </section>
  );
}

export function AppTab() {
  const device = useApp((s) => s.device);
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <div className="app-tab">
      <aside className="app-sidebar">
        <InstallCard device={device} />
        <NameOpsCard device={device} />
      </aside>
      <PackageList device={device} selected={selected} onSelect={setSelected} />
    </div>
  );
}
