/**
 * Placeholder data for Inspect.  Numbers come from the v2 mockup
 * (docs/webui-preview-v2.html).  Real version will fetch
 * /devices/{id}/info/{panel} for System Info and subscribe to
 * WS /metrics/stream for Charts.
 */
import type { ChartCardData, StorageCardData, SysCardData } from "./types";

export const MOCK_SYS_CARDS: SysCardData[] = [
  {
    title: "System",
    titleZh: "系统",
    entries: [
      { key: "OS", value: "Android 14 · UP1A.231005" },
      { key: "Kernel", value: "6.1.75-android14 #1 SMP PREEMPT" },
      { key: "Bootloader", value: "locked · verity on" },
      { key: "Uptime", value: "3 d 14 h 22 m" },
      { key: "Build", value: "aosp_arm64-userdebug" },
    ],
  },
  {
    title: "CPU",
    titleZh: "CPU",
    entries: [
      { key: "Model", keyZh: "型号", value: "arm64 8-core" },
      { key: "Governor", keyZh: "调速器", value: "schedutil" },
      { key: "Freq min/max", keyZh: "频率", value: "500 MHz / 2.85 GHz" },
      { key: "Load 1m", keyZh: "负载", value: "1.42" },
      { key: "SoC temp", keyZh: "温度", value: "46 °C" },
    ],
  },
  {
    title: "Memory",
    titleZh: "内存",
    entries: [
      { key: "Total", keyZh: "总量", value: "7.8 GiB" },
      { key: "Used", keyZh: "已用", value: "4.2 GiB · 53%" },
      { key: "Cached", keyZh: "缓存", value: "1.6 GiB" },
      { key: "swap", value: "2.0 GiB · 12%" },
      { key: "zram", value: "on · 2.0 GiB" },
    ],
  },
  {
    title: "Network",
    titleZh: "网络",
    entries: [
      { key: "iface", value: "wlan0" },
      { key: "IPv4", value: "192.168.50.108/24" },
      { key: "SSID", value: "lab-2.4G" },
      { key: "RSSI", value: "-58 dBm" },
      { key: "RX/TX", value: "4.2 MB/s · 0.7 MB/s" },
    ],
  },
  {
    title: "Battery",
    titleZh: "电池",
    entries: [
      { key: "Level", keyZh: "电量", value: "72%" },
      { key: "State", keyZh: "状态", value: "discharging" },
      { key: "Health", keyZh: "健康度", value: "good" },
      { key: "Voltage", keyZh: "电压", value: "3.94 V" },
      { key: "Temp", keyZh: "温度", value: "34 °C" },
    ],
  },
];

export const MOCK_STORAGE: StorageCardData = {
  title: "Storage",
  titleZh: "存储",
  partitions: [
    { name: "/data", pct: 46, color: "orange" },
    { name: "/system", pct: 78, color: "green" },
    { name: "/cache", pct: 12, color: "blue" },
    { name: "/vendor", pct: 91, color: "red" },
  ],
};

export const MOCK_CHARTS: ChartCardData[] = [
  {
    key: "cpu",
    title: "cpu",
    nowValue: "42",
    nowUnit: "%",
    caption: "avg 38 · max 71",
    spark: [
      72, 68, 55, 62, 48, 58, 40, 52, 38, 46, 30, 42, 28, 40, 32, 46, 38, 52,
      30, 42, 36,
    ],
    color: "blue",
    fillTint: true,
  },
  {
    key: "memory",
    title: "memory",
    nowValue: "53",
    nowUnit: "%",
    caption: "4.2 / 7.8 GiB",
    spark: [42, 42, 40, 40, 38, 38, 38, 36, 36],
    color: "green",
  },
  {
    key: "soc-temp",
    title: "soc temp",
    nowValue: "46",
    nowUnit: "°C",
    caption: "limit 85 °C",
    spark: [52, 50, 48, 46, 46, 44, 42, 42, 40],
    color: "orange",
  },
  {
    key: "disk-io",
    title: "disk i/o",
    nowValue: "12",
    nowUnit: "MB/s",
    caption: "r 8.2 · w 3.8",
    spark: [72, 68, 40, 52, 30, 48, 35, 55, 40, 28, 45, 38, 52, 42],
    color: "blue",
  },
  {
    key: "network",
    title: "network",
    nowValue: "4.9",
    nowUnit: "MB/s",
    caption: "rx 4.2 · tx 0.7",
    spark: [68, 52, 42, 30, 46, 38, 28, 42, 36, 30, 42],
    color: "green",
    secondSpark: [76, 72, 68, 72, 70, 74, 70, 72, 68, 72, 70],
    secondColor: "blue",
  },
  {
    key: "gpu",
    title: "gpu",
    nowValue: "31",
    nowUnit: "%",
    caption: "mali · 800 MHz",
    spark: [68, 62, 55, 52, 46, 42, 48, 52, 42, 36, 40, 44, 46, 52],
    color: "blue",
  },
];
