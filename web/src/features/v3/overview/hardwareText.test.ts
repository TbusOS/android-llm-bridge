import { describe, expect, it } from "vitest";
import type { ApiDeviceDetails, ApiDeviceDetailsExtras } from "../../../lib/api";
import { archLine, chipLine, cpuLine } from "./hardwareText";

const zh = (z: string) => z;

const board = (extras: Partial<ApiDeviceDetailsExtras>, over: Partial<ApiDeviceDetails> = {}): ApiDeviceDetails => ({
  model: "Board_X",
  brand: "Example",
  manufacturer: "Example",
  sdk: "36",
  release: "16",
  build_fingerprint: "",
  abi: "armeabi-v7a",
  hardware: "qcom",
  serialno: "",
  uptime_sec: 0,
  battery_level: 80,
  storage: {},
  extras: { soc: "platform-x", cpu_cores: 8, cpu_max_khz: 1_800_000, ram_total_kb: 0, ram_avail_kb: 0, display: {}, temp_c: 0, ...extras },
  ...over,
});

// The shape a big.LITTLE board with a 32-bit-only system sends (2026-11-06
// board; chip name and numbers replaced).
const NEW = {
  soc_model: "SM0000",
  soc_manufacturer: "QTI",
  board_platform: "platform-x",
  soc_id: "123",
  cpu_max_khz: 2_000_000,
  cpu_arch: "ARMv8",
  cpu_clusters: [
    { count: 4, cpus: [0, 1, 2, 3], core: "Kryo 2xx Silver", part: "0x51/0x801", max_khz: 1_800_000 },
    { count: 4, cpus: [4, 5, 6, 7], core: "Kryo 2xx Gold", part: "0x51/0x800", max_khz: 2_000_000 },
  ],
  abi_list: "armeabi-v7a,armeabi",
  abi_list64: "",
};

describe("hardware lines on the overview (2026-11-06)", () => {
  it("names the chip, not ro.hardware, and keeps the other names underneath", () => {
    expect(chipLine(board(NEW), zh)).toEqual({ main: "QTI SM0000", sub: "平台 platform-x · ro.hardware qcom · soc_id 123" });
  });

  it("lists each cluster with its own top frequency", () => {
    expect(cpuLine(board(NEW), zh).main).toBe("8 核 · 4 × Kryo 2xx Silver 1.80 GHz + 4 × Kryo 2xx Gold 2.00 GHz");
  });

  it("says a 32-bit-only system runs only 32-bit apps, whatever the CPU", () => {
    expect(archLine(board(NEW), zh)).toEqual({ main: "ARMv8 · 只跑 32 位应用", sub: "ABI armeabi-v7a, armeabi" });
  });

  it("tells 64-only and mixed systems apart", () => {
    const mixed = board({ ...NEW, abi_list: "arm64-v8a,armeabi-v7a,armeabi", abi_list64: "arm64-v8a" });
    const only64 = board({ ...NEW, abi_list: "arm64-v8a", abi_list64: "arm64-v8a" });
    expect(archLine(mixed, zh).main).toBe("ARMv8 · 64 位和 32 位应用都能跑");
    expect(archLine(only64, zh).main).toBe("ARMv8 · 只跑 64 位应用");
  });

  it("shows an unknown core by its numbers", () => {
    const d = board({ ...NEW, cpu_clusters: [{ count: 2, cpus: [0, 1], core: "", part: "0x99/0x123", max_khz: 1_000_000 }] });
    expect(cpuLine(d, zh).main).toBe("8 核 · 2 × 0x99/0x123 1.00 GHz");
  });

  it("an older hub without the new fields reads as before", () => {
    const old = board({}, { hardware: "qcom" });
    expect(chipLine(old, zh)).toEqual({ main: "qcom", sub: "" });
    expect(cpuLine(old, zh)).toEqual({ main: "8 核 · 1.80 GHz", sub: "" });
    expect(archLine(old, zh)).toEqual({ main: "armeabi-v7a", sub: "" });
  });
});
