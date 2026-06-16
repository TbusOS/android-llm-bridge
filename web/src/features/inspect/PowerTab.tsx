/**
 * PowerTab — Inspect sub-tab for power operations.
 *
 * Three sections:
 *   - Battery card (left): polls `/api/power/battery`, refresh button.
 *   - Reboot action (right top): mode select + timeout + danger HITL.
 *   - Sleep/Wake action (right bottom): cycles + hold inputs.
 *
 * Mockup baseline: `docs/webui-preview-v2-power.html` — BEM class names
 * here must mirror that file (L-028 mockup-baseline rule).
 *
 * Non-normal reboot modes (recovery / bootloader / fastboot / sideload)
 * require a second click after the user has explicitly armed
 * "allow dangerous" — the capability layer ALSO enforces this, so the
 * UI flag is a usability nicety, not the security boundary.
 */
import { useEffect, useState } from "react";

import { useApp } from "../../stores/app";
import type { RebootRequest } from "../../lib/api";
import { useArmedAction } from "../../lib/hooks/useArmedAction";
import { useElapsedSeconds } from "../../lib/hooks/useElapsedSeconds";
import { useBattery, useRebootMutation, useSleepWakeMutation } from "./usePower";

type RebootMode = RebootRequest["mode"];
const REBOOT_MODES: RebootMode[] = [
  "normal",
  "recovery",
  "bootloader",
  "fastboot",
  "sideload",
];
const DANGEROUS_MODES = new Set<RebootMode>([
  "recovery",
  "bootloader",
  "fastboot",
  "sideload",
]);

function BatteryCard({ device }: { device: string | null }) {
  const lang = useApp((s) => s.lang);
  const { data, isLoading, isError, error, isFetching, refetch } =
    useBattery(device);

  if (!device) {
    return (
      <section className="power-battery-card">
        <header className="power-battery-card__head">
          <h2 className="power-battery-card__title">battery</h2>
        </header>
        <p className="power-action-card__hint">
          {lang === "zh"
            ? "未选择设备 —— 先在顶栏选一个设备。"
            : "No device selected — pick one from the top-bar."}
        </p>
      </section>
    );
  }

  return (
    <section className="power-battery-card">
      <header className="power-battery-card__head">
        <h2 className="power-battery-card__title">battery</h2>
        <button
          type="button"
          className="power-battery-card__refresh"
          disabled={isFetching}
          onClick={() => {
            void refetch();
          }}
        >
          {isFetching
            ? lang === "zh"
              ? "刷新中…"
              : "refreshing…"
            : "refresh"}
        </button>
      </header>

      {isLoading && (
        <p className="power-action-card__hint">
          {lang === "zh" ? "正在读取…" : "Loading…"}
        </p>
      )}
      {isError && (
        <p className="power-action-card__hint" role="alert">
          {lang === "zh" ? "读取失败：" : "Failed: "}
          {error instanceof Error ? error.message : String(error)}
        </p>
      )}
      {data && !data.ok && (
        <p className="power-action-card__hint" role="alert">
          {data.error?.code || "ERROR"}: {data.error?.message}
        </p>
      )}
      {data?.ok && data.data && (
        <>
          <div className="power-battery-card__level">
            <span className="power-battery-card__level-value">
              {data.data.level}
            </span>
            <span className="power-battery-card__level-unit">
              / {data.data.scale}
            </span>
          </div>
          <div className="power-battery-card__bar">
            <div
              className="power-battery-card__bar-fill"
              style={{
                width: `${Math.max(
                  0,
                  Math.min(100, (data.data.level / data.data.scale) * 100),
                )}%`,
              }}
            />
          </div>
          <div className="power-battery-card__grid">
            <Field
              label={lang === "zh" ? "状态" : "status"}
              value={data.data.status || "—"}
            />
            <Field
              label={lang === "zh" ? "健康" : "health"}
              value={data.data.health || "—"}
            />
            <Field
              label={lang === "zh" ? "供电" : "plugged"}
              value={data.data.plugged || "—"}
            />
            <Field
              label={lang === "zh" ? "温度" : "temp"}
              value={`${data.data.temperature_celsius} °C`}
            />
            <Field
              label={lang === "zh" ? "电压" : "voltage"}
              value={`${data.data.voltage_mv} mV`}
            />
          </div>
        </>
      )}
    </section>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="power-battery-card__field">
      <span className="power-battery-card__field-label">{label}</span>
      <span className="power-battery-card__field-value">{value}</span>
    </div>
  );
}

function RebootCard({ device }: { device: string | null }) {
  const lang = useApp((s) => s.lang);
  const [mode, setMode] = useState<RebootMode>("normal");
  const [timeout, setTimeoutS] = useState(180);
  const mut = useRebootMutation(device);
  const isDanger = DANGEROUS_MODES.has(mode);

  const { armed, trigger, disarm } = useArmedAction(
    () =>
      mut.mutate({
        mode,
        wait_boot: mode === "normal",
        timeout,
        allow_dangerous: isDanger,
      }),
    { requiresArm: isDanger },
  );

  const reset = () => {
    disarm();
    mut.reset();
  };

  // Device-change reset — previous device's reboot result shouldn't
  // bleed into the new device's idle state.
  useEffect(() => {
    disarm();
    mut.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [device]);

  return (
    <section className="power-action-card">
      <header className="power-action-card__head">
        <h2 className="power-action-card__title">reboot</h2>
      </header>
      <p className="power-action-card__hint">
        {lang === "zh" ? (
          <>
            普通重启会等 sys.boot_completed=1。其它模式需要再点一次确认
            （8 秒内确认或自动取消）。
          </>
        ) : (
          <>
            Normal reboot waits for sys.boot_completed=1. Other modes need
            explicit confirmation (auto-cancel after 8 s).
          </>
        )}
      </p>
      <div className="power-action-card__row">
        <label>
          mode
          <select
            value={mode}
            disabled={mut.isPending}
            onChange={(e) => {
              setMode(e.target.value as RebootMode);
              disarm();
              mut.reset();
            }}
          >
            {REBOOT_MODES.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <label>
          {lang === "zh" ? "超时" : "timeout"}
          <input
            type="number"
            min={1}
            max={900}
            value={timeout}
            disabled={mut.isPending}
            onChange={(e) => setTimeoutS(Number(e.target.value) || 180)}
          />
        </label>
        <button
          type="button"
          className={[
            "power-btn",
            isDanger ? "power-btn--danger" : "",
            armed ? "is-armed" : "",
          ]
            .filter(Boolean)
            .join(" ")}
          disabled={!device || mut.isPending}
          onClick={trigger}
        >
          {mut.isPending
            ? lang === "zh"
              ? "执行中…"
              : "rebooting…"
            : armed
              ? lang === "zh"
                ? `⚠ 再点确认 ${mode}`
                : `⚠ click again to ${mode}`
              : isDanger
                ? lang === "zh"
                  ? `${mode}…`
                  : mode
                : lang === "zh"
                  ? "重启"
                  : "reboot"}
        </button>
        {armed && (
          <button
            type="button"
            className="power-battery-card__refresh"
            onClick={disarm}
          >
            {lang === "zh" ? "取消" : "cancel"}
          </button>
        )}
        {(mut.isSuccess || mut.isError) && (
          <button
            type="button"
            className="power-battery-card__refresh"
            onClick={reset}
          >
            {lang === "zh" ? "重置" : "reset"}
          </button>
        )}
      </div>

      {mut.data && (
        <div
          className="power-result"
          data-status={mut.data.ok ? "ok" : "err"}
        >
          {mut.data.ok && mut.data.data
            ? `ok · mode=${mut.data.data.mode} · wait_boot_ms=${
                mut.data.data.wait_boot_ms ?? "—"
              }`
            : `${mut.data.error?.code}: ${mut.data.error?.message}`}
        </div>
      )}
      {mut.isError && (
        <div className="power-result" data-status="err">
          {mut.error instanceof Error ? mut.error.message : String(mut.error)}
        </div>
      )}
    </section>
  );
}

function SleepWakeCard({ device }: { device: string | null }) {
  const lang = useApp((s) => s.lang);
  const [cycles, setCycles] = useState(1);
  const [holdSec, setHoldSec] = useState(5);
  const mut = useSleepWakeMutation(device);
  // Worst case runs cycles×(hold+wake) — up to tens of minutes. Without
  // an elapsed counter the user can't tell "stuck" from "still running".
  const elapsed = useElapsedSeconds(mut.isPending);
  // Device-change reset — see RebootCard.
  useEffect(() => {
    mut.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [device]);

  return (
    <section className="power-action-card">
      <header className="power-action-card__head">
        <h2 className="power-action-card__title">sleep · wake</h2>
      </header>
      <p className="power-action-card__hint">
        {lang === "zh"
          ? "N 轮 KEYCODE_POWER → KEYCODE_WAKEUP。常用于热 / wakelock 压测。"
          : "Send N rounds of KEYCODE_POWER → KEYCODE_WAKEUP. Useful for thermal / wakelock soak tests."}
      </p>
      <div className="power-action-card__row">
        <label>
          cycles
          <input
            type="number"
            min={1}
            max={1000}
            value={cycles}
            disabled={mut.isPending}
            onChange={(e) => setCycles(Number(e.target.value) || 1)}
          />
        </label>
        <label>
          {lang === "zh" ? "保持(秒)" : "hold (s)"}
          <input
            type="number"
            min={1}
            max={60}
            value={holdSec}
            disabled={mut.isPending}
            onChange={(e) => setHoldSec(Number(e.target.value) || 5)}
          />
        </label>
        <button
          type="button"
          className="power-btn"
          disabled={!device || mut.isPending}
          onClick={() => {
            mut.mutate({ cycles, hold_sec: holdSec });
          }}
        >
          {mut.isPending
            ? lang === "zh"
              ? `执行中… ${elapsed}s`
              : `running… ${elapsed}s`
            : lang === "zh"
              ? "运行"
              : "run"}
        </button>
      </div>
      {mut.data && (
        <div
          className="power-result"
          data-status={mut.data.ok ? "ok" : "err"}
        >
          {mut.data.ok && mut.data.data
            ? `ok · cycles=${mut.data.data.cycles} · total_ms=${mut.data.data.total_ms}`
            : `${mut.data.error?.code}: ${mut.data.error?.message}`}
        </div>
      )}
      {mut.isError && (
        <div className="power-result" data-status="err">
          {mut.error instanceof Error ? mut.error.message : String(mut.error)}
        </div>
      )}
    </section>
  );
}

export function PowerTab() {
  const device = useApp((s) => s.device);
  return (
    <div className="power-tab">
      <BatteryCard device={device} />
      <div className="power-actions">
        <RebootCard device={device} />
        <SleepWakeCard device={device} />
      </div>
    </div>
  );
}
