/**
 * Inspect → Screenshot — capture device framebuffer + display inline (PR-G).
 *
 * v1: single button + last shot. Multi-shot history left for v2 (could
 * mirror the UART captures pattern with a sidebar of past shots).
 */

import { useState } from "react";
import { Camera, Download } from "lucide-react";
import { useMutation } from "@tanstack/react-query";

import { useApp } from "../../stores/app";
import {
  captureScreenshot,
  type ScreenshotResponse,
} from "../../lib/api";

export function ScreenshotTab() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const [last, setLast] = useState<ScreenshotResponse | null>(null);

  const m = useMutation({
    mutationFn: () => {
      if (!device) throw new Error("no device");
      return captureScreenshot(device);
    },
    onSuccess: (data) => setLast(data),
  });

  if (!device) {
    return (
      <div className="mock-card">
        <h1 style={{ fontSize: 22 }}>{lang === "zh" ? "屏幕截图" : "Screenshot"}</h1>
        <p className="section-sub">
          {lang === "zh"
            ? "顶栏选一台设备再回这里。"
            : "Pick a device from the top-bar picker, then come back."}
        </p>
      </div>
    );
  }

  const shot = last?.screenshot;
  const dataUrl = shot ? `data:image/png;base64,${shot.png_base64}` : null;

  return (
    <div className="screenshot-tab">
      <div className="uart-tab__bar">
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => m.mutate()}
          disabled={m.isPending}
        >
          <Camera size={12} style={{ verticalAlign: "-2px" }} />{" "}
          {m.isPending
            ? lang === "zh" ? "抓取中…" : "Capturing…"
            : lang === "zh" ? "抓屏" : "Capture"}
        </button>
        {shot && dataUrl && (
          <a className="btn" href={dataUrl} download={shot.filename}>
            <Download size={12} style={{ verticalAlign: "-2px" }} />{" "}
            {lang === "zh" ? "下载 PNG" : "Download"}
          </a>
        )}
        {shot && (
          <span className="uart-tab__last">
            {shot.width}×{shot.height} · {(shot.size_bytes / 1024).toFixed(0)} KB · {shot.filename}
          </span>
        )}
        {last?.ok === false && (
          <span className="uart-tab__last uart-tab__last--err">{last.error}</span>
        )}
      </div>

      <div className="screenshot-tab__viewer">
        {dataUrl ? (
          <img
            src={dataUrl}
            alt="device screenshot"
            className="screenshot-tab__img"
          />
        ) : (
          <div className="uart-tab__empty">
            {lang === "zh"
              ? "点上方「抓屏」按钮"
              : "Press Capture above"}
          </div>
        )}
      </div>
    </div>
  );
}
