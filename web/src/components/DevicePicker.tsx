/**
 * DevicePicker — the topbar's real device dropdown.
 *
 * Replaces the v2 mockup's decorative chevron with a live picker:
 *   - polls `GET /devices` via the existing `useDevices()` hook
 *     (5 s refetch, same cache as DashboardPage's device strip)
 *   - clicking the trigger opens a listbox of available devices
 *   - selecting writes to `useApp().setDevice(serial)`
 *   - keyboard: ArrowUp/Down to move, Enter to pick, Esc to close
 *   - click-outside closes the menu
 *
 * Why a separate component: the picker owns dropdown state + a ref
 * for click-outside detection; co-locating with the topbar header
 * would entangle two concerns. The trigger pill keeps the v2 visual
 * (BEM `.device-picker`) so mockup baseline stays.
 */
import { ChevronDown } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import {
  statusFrom,
  transportFromName,
  transportLabel,
  type DeviceStatus,
} from "../lib/deviceFormat";
import { useDevices } from "../lib/hooks/useDevices";
import { useApp } from "../stores/app";

interface Props {
  /** Visible language — drives placeholder + a11y label. */
  lang: "en" | "zh";
}

interface PickerDevice {
  id: string;
  status: DeviceStatus;
  transportLabelStr: string;
  modelLine: string;
}

export function DevicePicker({ lang }: Props) {
  const device = useApp((s) => s.device);
  const setDevice = useApp((s) => s.setDevice);
  const {
    devices: rawDevices,
    transportName,
    isLoading,
    isError,
    refetch,
  } = useDevices();
  // Minimal projection — picker only needs serial / status / transport
  // label / model line. The dashboard's richer `DeviceCardData` (with
  // cpu/temp trends) lives in `features/dashboard/useDeviceCards`.
  const devices = useMemo<PickerDevice[]>(() => {
    const transportLabelStr = transportLabel(transportFromName(transportName));
    return rawDevices.map((d) => ({
      id: d.serial,
      status: statusFrom(d.state),
      transportLabelStr,
      modelLine: [d.product, d.model].filter(Boolean).join(" · "),
    }));
  }, [rawDevices, transportName]);

  const [open, setOpen] = useState(false);
  const [focusIdx, setFocusIdx] = useState(0);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const listId = useId();

  // Focus management — guarded by a ref so the initial-mount (open=
  // false) doesn't auto-focus the trigger on page load. After the
  // first true→false transition we treat close as "user dismissed the
  // menu" and return focus to the trigger (L-029 a11y baseline).
  // Opening: focus the listbox ONCE (mount-time). The previous
  // `ref={(el) => el?.focus()}` callback ran on every re-render and
  // re-stole focus from children, causing visible flicker.
  const wasOpenRef = useRef(false);
  useEffect(() => {
    if (open) {
      wasOpenRef.current = true;
      listRef.current?.focus();
    } else if (wasOpenRef.current) {
      triggerRef.current?.focus();
    }
  }, [open]);

  // Click-outside closes the menu.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  // Sync focusIdx to the currently-selected device when opening, AND
  // clamp it if `devices` shrinks while the menu is open (5/25 AO-4 /
  // ui-f MID-7 fix). react-query refetches every 5s · if device count
  // drops between renders, focusIdx might point past the end →
  // aria-activedescendant becomes undefined and visual focus ring
  // disappears. deps include devices.length so we re-evaluate on
  // those refetches; deps intentionally still exclude the `devices`
  // array reference (react-query returns stable refs prod-side, but
  // mock-side may not).
  useEffect(() => {
    if (!open) return;
    const i = devices.findIndex((d) => d.id === device);
    if (i >= 0) {
      setFocusIdx(i);
      return;
    }
    // selected device not in list (or no selection) — keep current
    // focus if still in range; clamp to last item if devices shrunk.
    setFocusIdx((prev) =>
      Math.min(prev, Math.max(0, devices.length - 1)),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, device, devices.length]);

  const onTriggerKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " " || e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
    }
  };

  const onListKey = (e: React.KeyboardEvent) => {
    if (devices.length === 0) {
      if (e.key === "Escape") setOpen(false);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setFocusIdx((i) => Math.min(devices.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setFocusIdx((i) => Math.max(0, i - 1));
    } else if (e.key === "Home") {
      e.preventDefault();
      setFocusIdx(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setFocusIdx(devices.length - 1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const d = devices[focusIdx];
      if (d) {
        setDevice(d.id);
        setOpen(false);
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  };

  const selected = devices.find((d) => d.id === device);
  const meta = selected
    ? `${selected.transportLabelStr} · ${selected.status}`
    : "adb · usb";

  return (
    <div className="device-picker-wrap" ref={wrapRef}>
      <button
        ref={triggerRef}
        type="button"
        className={device ? "device-picker" : "device-picker is-empty"}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-label={lang === "zh" ? "选择设备" : "Select device"}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onTriggerKey}
      >
        <span
          className="dot"
          data-status={selected?.status ?? "empty"}
        />
        <span className="label">
          {device ?? (lang === "zh" ? "未选择设备" : "no device")}
        </span>
        {device ? <span className="meta">{meta}</span> : null}
        <ChevronDown size={12} aria-hidden={true} />
      </button>

      {open && (
        <ul
          id={listId}
          ref={listRef}
          className="device-picker-menu"
          role="listbox"
          aria-label={lang === "zh" ? "可用设备" : "Available devices"}
          tabIndex={-1}
          // 5/25 ui-f HIGH-1 真修：AI-7 第一次把 aria-activedescendant
          // 挂在 trigger 上但 DOM focus 在 <ul> · SR 从 focused element
          // 读 activedescendant · 挂错宿主 → 朗读没生效。WAI-ARIA 1.2
          // listbox-with-active pattern：activedescendant 必须挂在
          // **持有 DOM focus** 的元素（这里是 listbox 自己）。
          aria-activedescendant={
            open && devices[focusIdx]
              ? `${listId}-opt-${focusIdx}`
              : undefined
          }
          onKeyDown={onListKey}
        >
          {isLoading && (
            <li className="device-picker-menu__hint">
              {lang === "zh" ? "读取设备列表…" : "Loading devices…"}
            </li>
          )}
          {isError && (
            <li className="device-picker-menu__hint" role="alert">
              {lang === "zh"
                ? "读取失败 · 点击重试"
                : "Failed to load · click to retry"}
              <button
                type="button"
                className="device-picker-menu__retry"
                onClick={() => {
                  refetch();
                }}
              >
                {lang === "zh" ? "重试" : "retry"}
              </button>
            </li>
          )}
          {!isLoading && !isError && devices.length === 0 && (
            <li className="device-picker-menu__hint">
              {lang === "zh"
                ? "无可用设备 — 插一台 / 起 adb / 检查隧道"
                : "No devices — plug one in / start adb / check tunnel"}
            </li>
          )}
          {devices.map((d, i) => {
            const isActive = device === d.id;
            const isFocus = i === focusIdx;
            return (
              <li
                key={d.id}
                id={`${listId}-opt-${i}`}
                role="option"
                aria-selected={isActive}
                className={[
                  "device-picker-menu__item",
                  isActive ? "is-active" : "",
                  isFocus ? "is-focus" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                onClick={() => {
                  setDevice(d.id);
                  setOpen(false);
                }}
                onMouseEnter={() => setFocusIdx(i)}
              >
                <span
                  className="device-picker-menu__dot"
                  data-status={d.status}
                />
                <span className="device-picker-menu__serial">{d.id}</span>
                <span className="device-picker-menu__meta">
                  {d.transportLabelStr}
                  {d.modelLine ? ` · ${d.modelLine}` : ""}
                  {d.status !== "online" ? ` · ${d.status}` : ""}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
