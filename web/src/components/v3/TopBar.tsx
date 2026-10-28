/**
 * v3 top app bar: brand, which board / device this UI talks to, one chip
 * per channel (adb, serial, fastboot, bench agent), page search, assistant,
 * language and theme.
 */
import { useEffect } from "react";

import { type AgentStatus, fetchAgentStatus } from "../../lib/api";
import { type BenchBoardView, type FlashStatusView, fetchBenchBoard, fetchFlashStatus, selectBenchBoard } from "../../lib/api3";
import { useDashboardQuery } from "../../lib/dashboardQuery";
import { useDevices } from "../../lib/hooks/useDevices";
import { useT } from "../../lib/i18n";
import { type Theme, useApp } from "../../stores/app";
import { useShell } from "../../stores/shell";
import { Icon } from "./Icon";
import { Dot, Menu, type MenuItem, type Tone, toast } from "./ui";

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

const THEMES: Theme[] = ["auto", "light", "dark"];

export function TopBar() {
  const t = useT();
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

  const { devices } = useDevices();
  const agent = useAgentStatus();
  const board = useBenchBoard();
  const flash = useFlashStatusQuery();

  // Keep a device selected while exactly the selected one disappears or
  // none was chosen yet: the first online device is the obvious target.
  useEffect(() => {
    const first = devices[0];
    if (first && (!device || !devices.some((d) => d.serial === device))) {
      setDevice(first.serial);
    }
  }, [devices, device, setDevice]);

  const current = devices.find((d) => d.serial === device);
  const agentItem = agent.data?.agents?.[0];
  const serial = board.data?.serial;
  const activeBoard = board.data?.active ?? null;

  const adbTone: Tone = current ? (current.state === "device" ? "ok" : "warn") : agent.data?.forwarders?.adb?.bound ? "warn" : "idle";
  const adbDetail = current ? (current.state === "device" ? current.serial.slice(0, 10) : current.state) : t("没有设备", "no device");
  const serialTone: Tone = serial?.com ? (serial.bound ? "ok" : "warn") : "idle";
  const serialDetail = serial?.com ? `${serial.com} ${serial.baud}` : t("未配置", "not set");
  const fbTone: Tone = flash.data ? (flash.data.busy ? "busy" : flash.data.available ? "ok" : "idle") : "idle";
  const fbDetail = flash.data ? (flash.data.busy ? t("作业中", "busy") : flash.data.available ? t("可用", "ready") : t("不可用", "unavailable")) : "—";
  const agentTone: Tone = agentItem ? "ok" : agent.isError ? "bad" : "idle";

  const boardItems: (MenuItem | "sep")[] = [];
  for (const b of board.data?.boards ?? []) {
    boardItems.push({
      key: `board-${b.name}`,
      label: (
        <>
          {b.name} · <span className="mono">{b.serial_com}</span>
        </>
      ),
      icon: "usb",
      trail: b.name === activeBoard ? <span className="pill pill--info">{t("当前", "current")}</span> : undefined,
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
  if (boardItems.length && devices.length) boardItems.push("sep");
  for (const d of devices) {
    boardItems.push({
      key: `dev-${d.serial}`,
      label: <span className="mono">{d.serial}</span>,
      icon: "phone",
      trail: d.serial === device ? <span className="pill pill--info">{t("已选", "selected")}</span> : undefined,
      desc: `adb · ${d.state}${d.model ? ` · ${d.model}` : ""}`,
      onSelect: () => setDevice(d.serial),
    });
  }
  if (!boardItems.length) {
    boardItems.push({ key: "none", label: t("没有板子和设备", "No boards or devices"), icon: "info", disabled: true, onSelect: () => {} });
  }

  const label = activeBoard ?? current?.serial ?? t("未选择", "none");
  const meta = activeBoard && current ? current.model ?? current.serial : current?.model ?? "";

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
      <Menu
        items={boardItems}
        trigger={(p) => (
          <button type="button" className="board-switch" {...p}>
            <Dot tone={current?.state === "device" || serial?.bound ? "live" : "idle"} />
            {label}
            {meta ? <span className="board-switch__meta mono">{meta}</span> : null}
            <Icon name="chevron-down" size="sm" />
          </button>
        )}
      />
      <div className="chans" aria-label={t("通道", "Channels")}>
        <Chan tone={adbTone} name="adb" detail={adbDetail} />
        <Chan tone={serialTone} name={t("串口", "serial")} detail={serialDetail} title={serial ? t(`${serial.readers} 个读者`, `${serial.readers} reader(s)`) : undefined} />
        <Chan tone={fbTone} name="fastboot" detail={fbDetail} title={flash.data?.job ?? undefined} />
        <Chan tone={agentTone} name={t("台架", "bench")} detail={agentItem?.name || (agent.isError ? t("连不上 hub", "hub unreachable") : t("未连接", "offline"))} />
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
