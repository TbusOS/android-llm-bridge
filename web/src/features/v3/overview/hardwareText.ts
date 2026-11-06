// What the overview says about a board's chip and CPU (2026-11-06).
//
// It used to say "qcom" and "8 cores · 1.80 GHz" for a board whose system
// names its chip and whose big cores run faster than cpu0: ro.hardware stood
// in for the chip, and only cpu0's frequency was read. Each line below takes
// the most specific name the hub sent and falls back to the old one, so an
// older hub without the new fields still renders as before.
import type { ApiDeviceDetails } from "../../../lib/api";

type T = (zh: string, en: string) => string;

export interface FactLine {
  main: string;
  /** Second, smaller line; "" when there is nothing more to say. */
  sub: string;
}

const ghz = (khz: number): string => `${(khz / 1e6).toFixed(2)} GHz`;

/** The chip by the vendor's name (ro.soc.*), then the other identifiers the
 *  same board goes by — each is what some other tool or log shows for it. */
export function chipLine(d: ApiDeviceDetails, t: T): FactLine {
  const x = d.extras;
  const named = [x.soc_manufacturer, x.soc_model].filter(Boolean).join(" ");
  const main = named || d.hardware || x.soc || "—";
  const sub = [
    x.board_platform && x.board_platform !== main ? `${t("平台", "platform")} ${x.board_platform}` : "",
    d.hardware && d.hardware !== main ? `ro.hardware ${d.hardware}` : "",
    x.soc_id ? `soc_id ${x.soc_id}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  return { main, sub };
}

/** Core count, then each cluster: "8 cores · 4 × Kryo 2xx Silver 1.80 GHz + …". */
export function cpuLine(d: ApiDeviceDetails, t: T): FactLine {
  const x = d.extras;
  const head = `${x.cpu_cores} ${t("核", "cores")}`;
  const clusters = x.cpu_clusters ?? [];
  if (clusters.length === 0) {
    return { main: x.cpu_max_khz > 0 ? `${head} · ${ghz(x.cpu_max_khz)}` : head, sub: "" };
  }
  const parts = clusters.map((c) =>
    [`${c.count} × ${c.core || c.part || t("未知核", "unknown core")}`, c.max_khz > 0 ? ghz(c.max_khz) : ""]
      .filter(Boolean)
      .join(" "),
  );
  return { main: `${head} · ${parts.join(" + ")}`, sub: "" };
}

/** The CPU architecture and which apps the system runs. The width comes from
 *  the ABI lists, not from the CPU: a board with ARMv8 cores can still run a
 *  32-bit-only system. */
export function archLine(d: ApiDeviceDetails, t: T): FactLine {
  const x = d.extras;
  const all = x.abi_list ?? "";
  const only64 = x.abi_list64 ?? "";
  let width = "";
  if (all && !only64) width = t("只跑 32 位应用", "32-bit apps only");
  else if (all && only64 === all) width = t("只跑 64 位应用", "64-bit apps only");
  else if (all) width = t("64 位和 32 位应用都能跑", "64- and 32-bit apps");
  const abis = (all || d.abi).split(",").filter(Boolean).join(", ");
  const main = [x.cpu_arch ?? "", width].filter(Boolean).join(" · ") || abis || "—";
  return { main, sub: main === abis || !abis ? "" : `ABI ${abis}` };
}
