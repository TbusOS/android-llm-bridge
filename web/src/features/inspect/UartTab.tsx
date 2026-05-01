/**
 * UART tab wrapper (PR-C.a + PR-C.b).
 *
 * Two modes via segment toggle:
 *   - Capture (PR-C.a): event-less REST captures, history list +
 *     dark text viewer.
 *   - Live (PR-C.b):   xterm.js connected to /uart/stream WS, real-time
 *     bytes from the serial transport.
 */

import { useState } from "react";
import { useApp } from "../../stores/app";
import { UartCaptureView } from "./UartCaptureView";
import { UartLiveStream } from "./UartLiveStream";

type Mode = "capture" | "live";

export function UartTab() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const [mode, setMode] = useState<Mode>("capture");

  return (
    <div>
      <div className="uart-mode">
        <button
          type="button"
          className={mode === "capture" ? "uart-mode__btn is-active" : "uart-mode__btn"}
          onClick={() => setMode("capture")}
        >
          {lang === "zh" ? "捕获 (REST)" : "Capture (REST)"}
        </button>
        <button
          type="button"
          className={mode === "live" ? "uart-mode__btn is-active" : "uart-mode__btn"}
          onClick={() => setMode("live")}
        >
          {lang === "zh" ? "实时 (WS)" : "Live (WS)"}
        </button>
      </div>

      {mode === "capture" ? <UartCaptureView /> : <UartLiveStream device={device} />}
    </div>
  );
}
