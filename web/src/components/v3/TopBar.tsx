/**
 * v3 top app bar: brand, which board / device this UI talks to, one chip
 * per channel (adb, serial, fastboot, bench agent, model backend), page
 * search, assistant, language and theme.
 */
import { useNavigate } from "@tanstack/react-router";
import { type KeyboardEvent, type ReactNode, useCallback, useEffect, useId, useRef, useState } from "react";

import { backendWord, deviceListCopy, deviceListState, overallBackend, transportLabel, useBackends } from "../../lib/overviewData";
import { type AgentStatus, fetchAgentStatus } from "../../lib/api";
import { type BenchBoardView, type FlashStatusView, fetchBenchBoard, fetchFlashStatus, selectBenchBoard } from "../../lib/api3";
import { useDashboardQuery } from "../../lib/dashboardQuery";
import { useDevices } from "../../lib/hooks/useDevices";
import { useT } from "../../lib/i18n";
import { type Theme, useApp } from "../../stores/app";
import { useShell } from "../../stores/shell";
import { Icon } from "./Icon";
import { Dot, Pill, type Tone, toast } from "./ui";

export function useAgentStatus() {
  return useDashboardQuery<AgentStatus>({ queryKey: ["agent-status"], queryFn: ({ signal }) => fetchAgentStatus(signal), refetchMs: 5_000 });
}
export function useBenchBoard() {
  return useDashboardQuery<BenchBoardView>({ queryKey: ["bench-board"], queryFn: ({ signal }) => fetchBenchBoard(signal), refetchMs: 10_000 });
}
export function useFlashStatusQuery() {
  return useDashboardQuery<FlashStatusView>({ queryKey: ["flash-status"], queryFn: ({ signal }) => fetchFlashStatus(signal), refetchMs: 5_000 });
}

function Chan({ tone, name, detail, title }: { tone: Tone; name: string; detail: string; title?: string }) {
  return (
    <span className="chip chip--sm" title={title ?? `${name} · ${detail}`}>
      <Dot tone={tone} />
      <b>{name}</b>
      <span>{detail}</span>
    </span>
  );
}

/* ---------------- board / device menu ---------------- */

interface Row {
  key: string;
  label: ReactNode;
  icon?: string;
  desc?: ReactNode;
  trail?: ReactNode;
  /** Rows without an action are information (loading, why the list is empty). */
  onSelect?: () => void;
  /** Leave the menu open after the action (retry shows its result in place). */
  keepOpen?: boolean;
  selected?: boolean;
  tone?: "error";
}

/**
 * The board switcher's own menu. The shared Menu only closes on Escape;
 * this one also moves with ↑ ↓ Home End, opens on ↓ / ↑ from the button,
 * starts on the selected row, and can hold rows that only explain.
 * Information rows stay in the arrow order (aria-disabled, not disabled)
 * so a screen reader user hears why the list is empty.
 */
function BoardMenu({ rows, label, trigger }: { rows: (Row | { sep: string })[]; label: string; trigger: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const wrap = useRef<HTMLSpanElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const start = useRef<"selected" | "last">("selected");

  const items = useCallback(() => Array.from(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []), []);
  const close = useCallback((refocus: boolean) => {
    setOpen(false);
    if (refocus) btn.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    const els = items();
    const target = start.current === "last" ? els[els.length - 1] : els.find((e) => e.dataset.selected === "true") ?? els[0];
    target?.focus();
    start.current = "selected";
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open, items]);
  // Rows come and go while the menu is open (the list arrives, a device
  // drops out). If the focused row was removed, focus fell to <body>;
  // put it back on the selected row so the arrow keys keep working.
  useEffect(() => {
    if (!open || (document.activeElement && document.activeElement !== document.body)) return;
    const els = items();
    (els.find((e) => e.dataset.selected === "true") ?? els[0])?.focus();
  });

  const onTriggerKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      start.current = e.key === "ArrowUp" ? "last" : "selected";
      setOpen(true);
    }
  };
  const onMenuKey = (e: KeyboardEvent) => {
    const els = items();
    const at = els.indexOf(document.activeElement as HTMLElement);
    const go = (i: number) => {
      e.preventDefault();
      if (els.length) els[(i + els.length) % els.length]?.focus();
    };
    if (e.key === "ArrowDown") go(at + 1);
    else if (e.key === "ArrowUp") go(at < 0 ? els.length - 1 : at - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(els.length - 1);
    else if (e.key === "Escape") {
      // Stop here so the shell's Escape (close sheet / drawer) does not also fire.
      e.preventDefault();
      e.stopPropagation();
      close(true);
    } else if (e.key === "Tab") setOpen(false);
  };

  return (
    <span className="menu-anchor" ref={wrap}>
      <button
        ref={btn}
        type="button"
        className="board-switch"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onTriggerKey}
      >
        {trigger}
      </button>
      {open ? (
        <div ref={menu} className="menu board-menu" id={id} role="menu" aria-label={label} onKeyDown={onMenuKey}>
          {rows.map((r) =>
            "sep" in r ? (
              <div className="menu__sep" key={r.sep} />
            ) : (
              <button
                key={r.key}
                type="button"
                role="menuitem"
                tabIndex={-1}
                className={`menu__item${r.onSelect ? "" : " menu__item--info"}${r.tone === "error" ? " menu__item--error" : ""}`}
                aria-disabled={r.onSelect ? undefined : true}
                data-selected={r.selected ? "true" : undefined}
                onClick={() => {
                  if (!r.onSelect) return;
                  if (!r.keepOpen) close(true);
                  r.onSelect();
                }}
              >
                {r.icon ? <Icon name={r.icon} /> : <span />}
                <span>{r.label}</span>
                {r.trail ?? <span />}
                {r.desc ? <span className="menu__desc">{r.desc}</span> : null}
              </button>
            ),
          )}
        </div>
      ) : null}
    </span>
  );
}

const THEMES: Theme[] = ["auto", "light", "dark"];

export function TopBar() {
  const t = useT();
  const navigate = useNavigate();
  const lang = useApp((s) => s.lang);
  const setLang = useApp((s) => s.setLang);
  const theme = useApp((s) => s.theme);
  const setTheme = useApp((s) => s.setTheme);
  const device = useApp((s) => s.device);
  const setDevice = useApp((s) => s.setDevice);
  const sheetOpen = useShell((s) => s.sheetOpen);
  const toggleSheet = useShell((s) => s.toggleSheet);
  const setNavOpen = useShell((s) => s.setNavOpen);
  const navOpen = useShell((s) => s.navOpen);
  const setSearchOpen = useShell((s) => s.setSearchOpen);

  const list = useDevices();
  const { devices } = list;
  const agent = useAgentStatus();
  const board = useBenchBoard();
  const flash = useFlashStatusQuery();
  const llm = useBackends();

  // Pick the first device only when nothing was chosen yet. A chosen
  // device that drops out of the list (reboot, cable, adb restart) stays
  // chosen and shows as offline: silently moving to another board would
  // point logcat, the console and every device page at the wrong one.
  useEffect(() => {
    const first = devices[0];
    if (first && !device) setDevice(first.serial);
  }, [devices, device, setDevice]);

  const listState = deviceListState(list);
  const tl = transportLabel(list.transportName);
  const current = devices.find((d) => d.serial === device);
  // Only claim "gone" once a list actually arrived without it.
  const missing = !!device && !current && listState.kind !== "loading" && listState.kind !== "fetch";
  const agentItem = agent.data?.agents?.[0];
  const serial = board.data?.serial;
  const activeBoard = board.data?.active ?? null;

  let adbTone: Tone = current ? (current.state === "device" ? "ok" : "warn") : agent.data?.forwarders?.adb?.bound ? "warn" : "idle";
  let adbDetail = current ? (current.state === "device" ? current.serial.slice(0, 10) : current.state) : t("没有设备", "no device");
  let adbTitle: string | undefined;
  if (listState.kind === "fetch" || listState.kind === "transport") {
    adbTone = "bad";
    adbDetail = listState.kind === "fetch" ? t("读不到列表", "no list") : t("不可用", "unavailable");
    adbTitle = listState.message;
  } else if (missing) {
    adbTone = "warn";
    adbDetail = t("离线", "offline");
    adbTitle = t(`${device} 不在设备列表里`, `${device} is not in the device list`);
  }
  const serialTone: Tone = serial?.com ? (serial.bound ? "ok" : "warn") : "idle";
  const serialDetail = serial?.com ? `${serial.com} ${serial.baud}` : t("未配置", "not set");
  const fbTone: Tone = flash.data ? (flash.data.busy ? "busy" : flash.data.available ? "ok" : "idle") : "idle";
  const fbDetail = flash.data ? (flash.data.busy ? t("作业中", "busy") : flash.data.available ? t("可用", "ready") : t("不可用", "unavailable")) : "—";
  const agentTone: Tone = agentItem ? "ok" : agent.isError ? "bad" : "idle";

  const overall = overallBackend(llm);
  const upBackend = llm.backends.find((b) => b.state.kind === "up");
  const llmTone: Tone = overall === "up" ? "ok" : overall === "down" ? "bad" : overall === "checking" ? "busy" : "idle";
  const llmDetail =
    overall === "up" && upBackend
      ? `${upBackend.name}${upBackend.state.kind === "up" && upBackend.state.latencyMs !== null ? ` ${upBackend.state.latencyMs} ms` : ""}`
      : overall === "down"
        ? t("连不上", "down")
        : overall === "checking"
          ? t("检测中", "checking")
          : overall === "unknown"
            ? t("未探测", "not probed")
            : t("未配置", "none");
  const llmTitle = llm.isError
    ? t("读不到模型后端列表", "Can't read the backend list")
    : llm.backends.map((b) => `${b.name} · ${backendWord(t, b.state)}`).join("\n") || t("没有注册的模型后端", "No backends registered");

  const rows: (Row | { sep: string })[] = [];
  for (const b of board.data?.boards ?? []) {
    rows.push({
      key: `board-${b.name}`,
      label: (
        <>
          {b.name} · <span className="mono">{b.serial_com}</span>
        </>
      ),
      icon: "usb",
      selected: b.name === activeBoard,
      trail: b.name === activeBoard ? <Pill tone="info">{t("当前", "current")}</Pill> : undefined,
      desc: `${b.serial_baud} baud${b.note ? ` · ${b.note}` : ""}`,
      onSelect: async () => {
        if (b.name === activeBoard) return;
        const r = await selectBenchBoard(b.name);
        if (r.ok) {
          toast(t(`串口已切到 ${b.name}。`, `Serial now points at ${b.name}.`));
          void board.refetch();
        } else {
          toast(`${r.code ?? "ERROR"}: ${r.error ?? ""}`);
        }
      },
    });
  }
  if (rows.length) rows.push({ sep: "sep-boards" });
  if (missing && device) {
    rows.push({
      key: "dev-gone",
      label: <span className="mono">{device}</span>,
      icon: "phone",
      selected: true,
      trail: <Pill tone="warn">{t("离线", "offline")}</Pill>,
      desc: devices.length
        ? t(`已选,但不在 ${tl} 设备列表里:可能在重启,或线被拔了。可以换下面另一台。`, `Selected, but not in the ${tl} device list: rebooting or unplugged? You can pick another one below.`)
        : t(`已选,但不在 ${tl} 设备列表里:可能在重启,或线被拔了。回来后会自动接上。`, `Selected, but not in the ${tl} device list: rebooting or unplugged? It reconnects when it comes back.`),
    });
  }
  for (const d of devices) {
    rows.push({
      key: `dev-${d.serial}`,
      label: <span className="mono">{d.serial}</span>,
      icon: "phone",
      selected: d.serial === device,
      trail: d.serial === device ? <Pill tone="info">{t("已选", "selected")}</Pill> : undefined,
      desc: [tl, d.state, d.product, d.model].filter(Boolean).join(" · "),
      onSelect: () => setDevice(d.serial),
    });
  }
  if (listState.kind !== "ok") {
    const copy = deviceListCopy(t, listState, tl);
    const bad = listState.kind === "fetch" || listState.kind === "transport";
    rows.push({
      key: "list-state",
      icon: bad ? "error" : listState.kind === "loading" ? "refresh" : "info",
      label: copy.title,
      desc: copy.body || undefined,
      tone: bad ? "error" : undefined,
      trail: listState.kind === "loading" ? <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> : undefined,
    });
    if (listState.kind !== "loading") {
      rows.push({ key: "retry", icon: "refresh", label: t("重新读取", "Read again"), keepOpen: true, onSelect: () => void list.refetch() });
      rows.push({ key: "bench", icon: "usb", label: t("去台架页看 USB 和串口", "Check USB and serial on the Bench page"), onSelect: () => void navigate({ to: "/bench" }) });
    }
  }

  const label = activeBoard ?? device ?? t("未选择", "none");
  const meta = missing ? t("离线", "offline") : activeBoard && current ? current.model ?? current.serial : current?.model ?? "";
  const switchTone: Tone = current?.state === "device" ? "live" : missing || listState.kind === "fetch" || listState.kind === "transport" ? "warn" : serial?.bound ? "live" : "idle";

  const themeIcon = theme === "auto" ? "monitor" : theme === "light" ? "sun" : "moon";
  const themeLabel = theme === "auto" ? t("主题:跟随系统", "Theme: system") : theme === "light" ? t("主题:浅色", "Theme: light") : t("主题:深色", "Theme: dark");

  return (
    <header className="topbar">
      <button type="button" className="icon-btn topbar__menu" aria-label={t("打开导航", "Open navigation")} aria-expanded={navOpen} onClick={() => setNavOpen(!navOpen)}>
        <Icon name="menu" />
      </button>
      <a className="brand" href={`${import.meta.env.BASE_URL}dashboard`}>
        <span className="brand__mark">
          <Icon name="bridge" />
        </span>
        <span className="brand__name">alb</span>
        <span className="brand__sub">android-llm-bridge</span>
      </a>
      <BoardMenu
        rows={rows}
        label={t("板子和设备", "Boards and devices")}
        trigger={
          <>
            <Dot tone={switchTone} />
            {label}
            {meta ? <span className={`board-switch__meta mono${missing ? " board-switch__meta--warn" : ""}`}>{meta}</span> : null}
            <Icon name="chevron-down" size="sm" />
          </>
        }
      />
      <div className="chans" aria-label={t("通道", "Channels")}>
        <Chan tone={adbTone} name="adb" detail={adbDetail} title={adbTitle} />
        <Chan tone={serialTone} name={t("串口", "serial")} detail={serialDetail} title={serial ? t(`${serial.readers} 个读者`, `${serial.readers} reader(s)`) : undefined} />
        <Chan tone={fbTone} name="fastboot" detail={fbDetail} title={flash.data?.job ?? undefined} />
        <Chan tone={agentTone} name={t("台架", "bench")} detail={agentItem?.name || (agent.isError ? t("连不上 hub", "hub unreachable") : t("未连接", "offline"))} />
        <Chan tone={llmTone} name={t("模型", "LLM")} detail={llmDetail} title={llmTitle} />
      </div>
      <span className="topbar__spacer" />
      <button type="button" className="search" onClick={() => setSearchOpen(true)} aria-label={t("搜索页面", "Search pages")}>
        <Icon name="search" />
        <span>{t("搜索页面和操作", "Search pages and actions")}</span>
        <kbd>{navigator.platform?.toLowerCase().includes("mac") ? "⌘K" : "Ctrl K"}</kbd>
      </button>
      <button type="button" className="icon-btn" aria-label={t("助手", "Assistant")} title={t("助手", "Assistant")} aria-expanded={sheetOpen} onClick={toggleSheet}>
        <Icon name="spark" />
      </button>
      <button
        type="button"
        className="icon-btn"
        aria-label={lang === "zh" ? "Switch to English" : "切换到中文"}
        title={lang === "zh" ? "English" : "中文"}
        onClick={() => setLang(lang === "zh" ? "en" : "zh")}
      >
        <Icon name="lang" />
      </button>
      <button type="button" className="icon-btn" aria-label={themeLabel} title={themeLabel} onClick={() => setTheme(THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length] ?? "auto")}>
        <Icon name={themeIcon} />
      </button>
    </header>
  );
}
