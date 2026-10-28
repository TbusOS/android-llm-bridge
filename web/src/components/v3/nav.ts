/**
 * The v3 navigation map: one place for the drawer, the page search and the
 * redirects from the old /inspect/<tab> addresses. Paths must not contain
 * "." (DEBT-014: the hub's SPA fallback reads a dot as a missing asset).
 */
export interface NavItem {
  key: string;
  to: string;
  icon: string;
  zh: string;
  en: string;
  /** Extra words the page search matches. */
  keywords?: string;
  badge?: "new";
}

export interface NavGroup {
  zh: string;
  en: string;
  items: NavItem[];
}

export const NAV: NavGroup[] = [
  {
    zh: "设备",
    en: "Device",
    items: [
      { key: "overview", to: "/dashboard", icon: "overview", zh: "总览", en: "Overview", keywords: "dashboard home board state" },
      { key: "console", to: "/console", icon: "terminal", zh: "控制台", en: "Console", keywords: "shell terminal adb serial uart 终端 串口" },
      { key: "logs", to: "/logs", icon: "logs", zh: "日志", en: "Logs", keywords: "logcat dmesg uart search 搜索" },
      { key: "timeline", to: "/timeline", icon: "timeline", zh: "时间轴", en: "Timeline", keywords: "boot 开机 record", badge: "new" },
      { key: "perf", to: "/perf", icon: "chart", zh: "性能", en: "Performance", keywords: "cpu memory temperature metrics charts 内存 温度" },
      { key: "files", to: "/files", icon: "folder", zh: "文件", en: "Files", keywords: "push pull workspace 工作区" },
      { key: "apps", to: "/apps", icon: "apps", zh: "应用", en: "Apps", keywords: "install package apk 安装" },
      { key: "screen", to: "/screen", icon: "phone", zh: "屏幕", en: "Screen", keywords: "screenshot ui dump 截屏" },
      { key: "problems", to: "/problems", icon: "bug", zh: "问题", en: "Problems", keywords: "anr tombstone pstore bugreport crash 崩溃" },
      { key: "info", to: "/info", icon: "info", zh: "系统信息", en: "System info", keywords: "properties partitions 属性 分区" },
      { key: "power", to: "/power", icon: "battery", zh: "电源", en: "Power", keywords: "battery sleep wake reboot 电池 休眠 重启" },
    ],
  },
  {
    zh: "烧录",
    en: "Flash",
    items: [
      { key: "flash", to: "/flash", icon: "bolt", zh: "烧录", en: "Flash", keywords: "fastboot package plan 整包" },
      { key: "board-config", to: "/board-config", icon: "sliders", zh: "配置分区", en: "Board config", keywords: "cfg KEY=VALUE" },
    ],
  },
  {
    zh: "台架",
    en: "Bench",
    items: [{ key: "bench", to: "/bench", icon: "usb", zh: "台架", en: "Bench", keywords: "agent usb board windows 板子 驱动" }],
  },
  {
    zh: "助手",
    en: "Assistant",
    items: [
      { key: "chat", to: "/chat", icon: "chat", zh: "对话", en: "Chat", keywords: "llm agent model 模型" },
      { key: "playground", to: "/playground", icon: "flask", zh: "模型试验", en: "Playground", keywords: "backend model compare" },
      { key: "sessions", to: "/sessions", icon: "history", zh: "会话记录", en: "Sessions" },
    ],
  },
  {
    zh: "系统",
    en: "System",
    items: [
      { key: "doctor", to: "/doctor", icon: "check-circle", zh: "环境检查", en: "Health check", keywords: "doctor" },
      { key: "audit", to: "/audit", icon: "shield", zh: "操作记录", en: "Audit log", keywords: "history commands" },
      { key: "connections", to: "/connections", icon: "link", zh: "连接", en: "Connections", keywords: "agent forwarder adb serial" },
    ],
  },
];

/** Where an old Inspect tab lives now: a page, and the tab on it when the page has several. */
export interface InspectTarget {
  to: string;
  /** Value for the page's `?tab=` (LogsPage: logcat|dmesg|serial|search; ScreenPage: shot|tree). */
  tab?: string;
}

/** Old Inspect tab → new address. */
export const INSPECT_REDIRECTS: Record<string, InspectTarget> = {
  system: { to: "/info" },
  charts: { to: "/perf" },
  uart: { to: "/logs", tab: "serial" },
  logcat: { to: "/logs", tab: "logcat" },
  shell: { to: "/console" },
  screenshot: { to: "/screen", tab: "shot" },
  "ui-dump": { to: "/screen", tab: "tree" },
  files: { to: "/files" },
  power: { to: "/power" },
  "log-search": { to: "/logs", tab: "search" },
  diag: { to: "/problems" },
  app: { to: "/apps" },
  flash: { to: "/flash" },
};

/** The redirect for an old tab key (`/inspect/<key>` or `/inspect?tab=<key>`); unknown keys go to the overview. */
export function inspectTarget(key: string | undefined): { to: string; search: { tab?: string } } {
  const hit = key ? INSPECT_REDIRECTS[key] : undefined;
  if (!hit) return { to: "/dashboard", search: {} };
  return { to: hit.to, search: hit.tab ? { tab: hit.tab } : {} };
}

export const ALL_ITEMS: NavItem[] = NAV.flatMap((g) => g.items);
