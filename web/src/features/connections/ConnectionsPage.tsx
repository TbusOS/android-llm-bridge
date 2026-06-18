/**
 * Connection Center (P2) — remote device agents dialed in to this hub +
 * adb/serial forwarder state. Read-only view over GET /agent/status.
 *
 * Class names mirror docs/webui-preview-v2-connections.html (L-001 baseline).
 * Loading / error / empty all reuse the .conn-empty block (no new classes).
 */
import { useApp } from "../../stores/app";
import { useConnections } from "./useConnections";

export function ConnectionsPage() {
  const lang = useApp((s) => s.lang);
  const zh = lang === "zh";
  const { data, isLoading, isError } = useConnections();

  return (
    <div className="conn-wrap">
      <div className="conn">
        <header className="conn-head">
          <h1 className="conn-head__title">
            {zh ? "连接中心" : "Connection Center"}
          </h1>
          <p className="conn-head__sub">
            {zh
              ? "拨入本中枢的远程设备 agent，以及它们暴露的 adb / 串口转发器。"
              : "Remote device agents dialed in to this hub, and the adb / serial forwarders they expose."}
          </p>
        </header>

        {isLoading ? (
          <div className="conn-empty">
            <p className="conn-empty__title">{zh ? "加载中…" : "Loading…"}</p>
          </div>
        ) : isError || !data ? (
          <div className="conn-empty">
            <p className="conn-empty__title">
              {zh ? "无法读取连接状态" : "Could not load connection status"}
            </p>
            <p className="conn-empty__hint">
              {zh
                ? "确认 alb-api 正在运行。"
                : "Check that alb-api is running."}
            </p>
          </div>
        ) : (
          <>
            <section className="conn-section">
              <h2 className="conn-section__title">
                {zh ? "已连 agent" : "Connected agents"}
              </h2>
              {data.agents.length > 0 ? (
                <div className="conn-agents">
                  {data.agents.map((a) => (
                    <article className="agent-card" key={a.agent_id}>
                      <div className="agent-card__top">
                        <span className="agent-card__dot" />
                        <h3 className="agent-card__name">{a.name}</h3>
                        {a.current && (
                          <span className="agent-card__badge">
                            {zh ? "当前" : "active"}
                          </span>
                        )}
                      </div>
                      <p className="agent-card__meta">
                        id {a.agent_id.slice(0, 8)} · v{a.version}
                      </p>
                      {a.caps.length > 0 && (
                        <div className="agent-card__caps">
                          {a.caps.map((c) => (
                            <span className="agent-cap" key={c}>
                              {c}
                            </span>
                          ))}
                        </div>
                      )}
                      <div className="agent-card__devices">
                        {a.adb_devices.length === 0 &&
                        a.com_ports.length === 0 ? (
                          <span className="agent-dev agent-dev--muted">
                            {zh ? "未上报设备" : "no devices reported"}
                          </span>
                        ) : (
                          <>
                            {a.adb_devices.map((d) => (
                              <span className="agent-dev" key={`adb-${d}`}>
                                <span className="agent-dev__kind">adb</span>
                                {d}
                              </span>
                            ))}
                            {a.com_ports.map((cp) => (
                              <span className="agent-dev" key={`com-${cp.port}`}>
                                <span className="agent-dev__kind">com</span>
                                {cp.port}
                                {cp.desc ? ` · ${cp.desc}` : ""}
                              </span>
                            ))}
                          </>
                        )}
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <div className="conn-empty">
                  <span className="agent-card__dot agent-card__dot--off" />
                  <p className="conn-empty__title">
                    {zh ? "还没有 agent 连接" : "No agent connected"}
                  </p>
                  <p className="conn-empty__hint">
                    {zh
                      ? "在连着板子的机器上启动设备 agent，它会主动拨入本中枢。下方转发器在 agent 连上前保持空闲。"
                      : "Start the device agent on the machine that holds the board — it dials out to this hub. The forwarders below stay idle until one connects."}
                  </p>
                </div>
              )}
            </section>

            <section className="conn-section">
              <h2 className="conn-section__title">
                {zh ? "转发器" : "Forwarders"}
              </h2>
              <div className="fwd-list">
                <div className="fwd-row">
                  <span className="fwd-row__label">ADB</span>
                  <span
                    className={
                      data.forwarders.adb.bound
                        ? "fwd-row__pill fwd-row__pill--on"
                        : "fwd-row__pill fwd-row__pill--off"
                    }
                  >
                    {data.forwarders.adb.bound
                      ? zh
                        ? "已绑定"
                        : "bound"
                      : zh
                        ? "未绑定"
                        : "unbound"}
                  </span>
                  <span className="fwd-row__detail">
                    127.0.0.1:{data.forwarders.adb.port}
                  </span>
                </div>

                <div className="fwd-row">
                  <span className="fwd-row__label">Serial</span>
                  {data.forwarders.serial.configured ? (
                    <>
                      <span
                        className={
                          data.forwarders.serial.bound
                            ? "fwd-row__pill fwd-row__pill--on"
                            : "fwd-row__pill fwd-row__pill--off"
                        }
                      >
                        {data.forwarders.serial.bound
                          ? zh
                            ? "已绑定"
                            : "bound"
                          : zh
                            ? "未绑定"
                            : "unbound"}
                      </span>
                      <span className="fwd-row__detail">
                        127.0.0.1:{data.forwarders.serial.port} ·{" "}
                        {data.forwarders.serial.com} @{" "}
                        {data.forwarders.serial.baud}
                      </span>
                    </>
                  ) : (
                    <>
                      <span className="fwd-row__pill fwd-row__pill--off">
                        {zh ? "未配置" : "not configured"}
                      </span>
                      <span className="fwd-row__detail">
                        {zh
                          ? "设置 ALB_AGENT_SERIAL_COM"
                          : "set ALB_AGENT_SERIAL_COM"}
                      </span>
                    </>
                  )}
                </div>
              </div>
            </section>

            <div className="conn-hint">
              <span>
                {zh
                  ? "在设备所在主机上运行 agent（只需 Python + websockets）："
                  : "Run the agent on the device's host (it needs only Python + websockets):"}
              </span>
              <code className="conn-hint__code">
                python alb_agent.py --hub-url wss://&lt;hub&gt;/agent/connect
                --token &lt;token&gt;
              </code>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
