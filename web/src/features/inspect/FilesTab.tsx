/**
 * Inspect → Files — two-pane file browser (PR-H).
 *
 * Left pane: device file system rooted at /sdcard/.
 * Right pane: workspace rooted at devices/<serial>/.
 *
 * Selecting a file enables Pull (device → workspace) or Push
 * (workspace → device). Pushes that target a sensitive prefix
 * (/system /vendor /data /dev /proc /sys /persist /oem /boot)
 * come back as `requires_confirm` — we surface a modal that
 * resubmits with `force: true` after the user OKs it.
 */

import { useEffect, useState } from "react";
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  Download,
  FolderUp,
  RefreshCw,
  X,
} from "lucide-react";

import { HitlConfirmModal } from "../../components/HitlConfirmModal";
import { formatBytes } from "../../lib/format";
import type { Lang } from "../../stores/app";
import { useApp } from "../../stores/app";
import { NoDeviceCard } from "../../components/NoDeviceCard";
import {
  type DeviceFileEntry,
  type WorkspaceFileEntry,
  workspaceDownloadUrl,
} from "../../lib/api";
import {
  useDeviceFiles,
  useWorkspaceFiles,
  useWorkspacePreview,
} from "./useFileBrowser";
import { useFileTransferStream } from "./useFileTransferStream";
import { useQueryClient } from "@tanstack/react-query";

const DEFAULT_DEVICE_PATH = "/sdcard/";
const DEFAULT_WS_PREFIX = "devices";

export function FilesTab() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const [devicePath, setDevicePath] = useState(DEFAULT_DEVICE_PATH);
  const [workspacePath, setWorkspacePath] = useState(
    device ? `${DEFAULT_WS_PREFIX}/${device}` : DEFAULT_WS_PREFIX,
  );
  const [selectedDevice, setSelectedDevice] = useState<DeviceFileEntry | null>(
    null,
  );
  const [selectedWorkspace, setSelectedWorkspace] =
    useState<WorkspaceFileEntry | null>(null);
  const [pendingPush, setPendingPush] = useState<{
    serial: string;
    local: string;
    remote: string;
    error: string;
  } | null>(null);

  // Debounce path-input → fetch trigger so 14-char path edits don't
  // fan out to 14 adb `ls -la` calls (code-review HIGH 2 / 2026-05-02).
  const debouncedDevicePath = useDebouncedValue(devicePath, 300);
  const debouncedWorkspacePath = useDebouncedValue(workspacePath, 300);
  const deviceQ = useDeviceFiles(device, debouncedDevicePath);
  const workspaceQ = useWorkspaceFiles(debouncedWorkspacePath);
  // MID-6: streaming push/pull with progress + cancel (replaces old
  // useFileTransfers HTTP mutations).
  const xfer = useFileTransferStream();
  const qc = useQueryClient();
  const xferRunning = xfer.state === "connecting" || xfer.state === "running";

  // Preview is shown only when a non-dir workspace file is selected.
  // null disables the query so unselecting (or selecting a dir) stops
  // the fetch immediately. Path is workspace-relative.
  const previewPath =
    selectedWorkspace && !selectedWorkspace.is_dir
      ? workspacePath
        ? `${workspacePath}/${selectedWorkspace.name}`
        : selectedWorkspace.name
      : null;
  const preview = useWorkspacePreview(previewPath);

  // After a successful transfer, refresh both panes so freshly pushed /
  // pulled files appear without a manual reload.
  useEffect(() => {
    if (xfer.state === "done" && xfer.result?.ok) {
      const serial = device || "";
      qc.invalidateQueries({ queryKey: ["device-files", serial] });
      qc.invalidateQueries({ queryKey: ["workspace-files"] });
    }
  }, [xfer.state, xfer.result, device, qc]);

  // Server returned `sensitive_path` → pop the HITL modal asking for
  // explicit force confirmation. Caller retries via xfer.start with
  // force=true on confirm.
  useEffect(() => {
    if (xfer.state === "needs_confirm" && xfer.result) {
      setPendingPush({
        serial: xfer.result.direction === "push" ? device || "" : "",
        local: xfer.result.local,
        remote: xfer.result.remote,
        error: xfer.error || xfer.result.error || "sensitive path",
      });
    }
  }, [xfer.state, xfer.result, xfer.error, device]);

  if (!device) {
    return <NoDeviceCard titleZh="文件" titleEn="Files" />;
  }

  const onDeviceEntryActivate = (entry: DeviceFileEntry) => {
    if (entry.is_dir) {
      setDevicePath(joinPath(devicePath, entry.name));
      setSelectedDevice(null);
    } else {
      setSelectedDevice(entry);
    }
  };

  const onWorkspaceEntryActivate = (entry: WorkspaceFileEntry) => {
    if (entry.is_dir) {
      setWorkspacePath(workspacePath ? `${workspacePath}/${entry.name}` : entry.name);
      setSelectedWorkspace(null);
    } else {
      setSelectedWorkspace(entry);
    }
  };

  const onPull = () => {
    if (!selectedDevice || selectedDevice.is_dir) return;
    const remote = joinPath(devicePath, selectedDevice.name);
    const local = `${workspacePath || `${DEFAULT_WS_PREFIX}/${device}/pulls`}/${selectedDevice.name}`;
    xfer.start({ serial: device, direction: "pull", remote, local });
  };

  const onPush = (force = false) => {
    if (!selectedWorkspace || selectedWorkspace.is_dir) return;
    const local = workspacePath
      ? `${workspacePath}/${selectedWorkspace.name}`
      : selectedWorkspace.name;
    const remote = joinPath(devicePath, selectedWorkspace.name);
    xfer.start({ serial: device, direction: "push", local, remote, force });
  };

  const confirmPush = () => {
    if (!pendingPush) return;
    xfer.start({
      serial: pendingPush.serial || device,
      direction: "push",
      local: pendingPush.local,
      remote: pendingPush.remote,
      force: true,
    });
    setPendingPush(null);
  };

  return (
    <div className="files-tab">
      <div className="files-tab__panes">
        <FilePane
          lang={lang}
          title={lang === "zh" ? "设备" : "Device"}
          subtitle={device}
          path={devicePath}
          onPathChange={(p) => {
            setDevicePath(p);
            setSelectedDevice(null);
          }}
          onUp={() => {
            setDevicePath(parentPath(devicePath, "/"));
            setSelectedDevice(null);
          }}
          onRefresh={() => deviceQ.refetch()}
          isFetching={deviceQ.isFetching}
          loading={deviceQ.isLoading}
          error={
            deviceQ.error
              ? String(deviceQ.error)
              : deviceQ.data && !deviceQ.data.ok
                ? deviceQ.data.error || "ls failed"
                : null
          }
          truncated={!!deviceQ.data?.truncated}
          entries={(deviceQ.data?.entries ?? []).map((e) => ({
            name: e.name,
            is_dir: e.is_dir,
            is_link: e.is_link,
            meta: `${formatBytes(e.size)} · ${e.mode}`,
            mtime: e.mtime,
          }))}
          activeName={selectedDevice?.name ?? null}
          onActivate={(name) => {
            const e = deviceQ.data?.entries.find((x) => x.name === name);
            if (e) onDeviceEntryActivate(e);
          }}
        >
          <button
            type="button"
            className="btn btn--primary"
            disabled={
              !selectedDevice || selectedDevice.is_dir || xferRunning
            }
            onClick={onPull}
          >
            <ArrowDownToLine size={12} className="icon-inline" />{" "}
            {xferRunning && xfer.result?.direction !== "push"
              ? lang === "zh" ? "拉取中…" : "Pulling…"
              : lang === "zh" ? "拉到工作区" : "Pull"}
          </button>
        </FilePane>

        <FilePane
          lang={lang}
          title={lang === "zh" ? "工作区" : "Workspace"}
          subtitle={workspacePath || "(root)"}
          path={workspacePath}
          onPathChange={(p) => {
            setWorkspacePath(p);
            setSelectedWorkspace(null);
          }}
          onUp={() => {
            setWorkspacePath(parentPath(workspacePath, "/"));
            setSelectedWorkspace(null);
          }}
          onRefresh={() => workspaceQ.refetch()}
          isFetching={workspaceQ.isFetching}
          loading={workspaceQ.isLoading}
          error={
            workspaceQ.error
              ? String(workspaceQ.error)
              : workspaceQ.data && !workspaceQ.data.ok
                ? workspaceQ.data.error || "ls failed"
                : null
          }
          truncated={!!workspaceQ.data?.truncated}
          entries={(workspaceQ.data?.entries ?? []).map((e) => ({
            name: e.name,
            is_dir: e.is_dir,
            is_link: e.is_link,
            meta: `${formatBytes(e.size)}`,
            mtime: e.mtime_epoch
              ? new Date(e.mtime_epoch * 1000).toISOString().slice(0, 19)
              : "",
          }))}
          activeName={selectedWorkspace?.name ?? null}
          onActivate={(name) => {
            const e = workspaceQ.data?.entries.find((x) => x.name === name);
            if (e) onWorkspaceEntryActivate(e);
          }}
        >
          <button
            type="button"
            className="btn btn--primary"
            disabled={
              !selectedWorkspace ||
              selectedWorkspace.is_dir ||
              xferRunning
            }
            onClick={() => onPush(false)}
          >
            <ArrowUpFromLine size={12} className="icon-inline" />{" "}
            {xferRunning && xfer.result?.direction !== "pull"
              ? lang === "zh" ? "推送中…" : "Pushing…"
              : lang === "zh" ? "推到设备" : "Push"}
          </button>
          {selectedWorkspace && !selectedWorkspace.is_dir ? (
            <a
              className="btn"
              href={workspaceDownloadUrl(
                workspacePath
                  ? `${workspacePath}/${selectedWorkspace.name}`
                  : selectedWorkspace.name,
              )}
              download={selectedWorkspace.name}
            >
              <Download size={12} className="icon-inline" />{" "}
              {lang === "zh" ? "下载" : "Download"}
            </a>
          ) : null}
        </FilePane>
      </div>

      {previewPath && preview.data ? (
        <div className="files-tab__preview">
          <div className="files-tab__preview-head">
            <span className="files-tab__preview-name">{previewPath}</span>
            <span className="files-tab__preview-meta">
              {formatBytes(preview.data.size_bytes)}
              {preview.data.truncated
                ? lang === "zh"
                  ? " · 已截断"
                  : " · truncated"
                : ""}
              {preview.data.is_binary
                ? lang === "zh"
                  ? " · 二进制"
                  : " · binary"
                : ` · ${preview.data.encoding}`}
            </span>
            <button
              type="button"
              className="files-tab__preview-close"
              onClick={() => setSelectedWorkspace(null)}
              aria-label={lang === "zh" ? "关闭预览" : "Close preview"}
              title={lang === "zh" ? "关闭" : "Close"}
            >
              <X size={12} />
            </button>
          </div>
          {preview.data.is_binary ? (
            <div className="files-tab__preview-binary">
              {lang === "zh"
                ? "二进制文件 · 用「下载」按钮拉取查看"
                : "Binary file — use Download to retrieve"}
            </div>
          ) : (
            <pre className="files-tab__preview-pre">
              {preview.data.text || (lang === "zh" ? "（空）" : "(empty)")}
            </pre>
          )}
        </div>
      ) : previewPath && preview.isLoading ? (
        <div className="files-tab__preview files-tab__preview--state">
          {lang === "zh" ? "加载预览…" : "Loading preview…"}
        </div>
      ) : previewPath && preview.isError ? (
        <div className="files-tab__preview files-tab__preview--state files-tab__preview--err">
          {lang === "zh" ? "预览失败" : "Preview failed"}
        </div>
      ) : null}

      <div className="files-tab__status">
        {xferRunning ? (
          <div className="files-tab__progress" role="status" aria-live="polite">
            <div className="files-tab__progress-bar">
              <div
                className="files-tab__progress-fill"
                style={{
                  width:
                    xfer.progress?.percent != null
                      ? `${Math.max(0, Math.min(100, xfer.progress.percent))}%`
                      : undefined,
                }}
                data-indeterminate={xfer.progress?.percent == null || undefined}
              />
            </div>
            <span className="files-tab__progress-meta">
              {xfer.progress?.percent != null
                ? `${Math.round(xfer.progress.percent)}%`
                : (lang === "zh" ? "传输中…" : "transferring…")}
              {xfer.progress?.bytes_transferred
                ? ` · ${formatBytes(xfer.progress.bytes_transferred)}`
                : ""}
            </span>
            <button
              type="button"
              className="btn btn--small"
              onClick={xfer.cancel}
              title={lang === "zh" ? "取消传输" : "Cancel transfer"}
            >
              <X size={11} className="icon-inline" />{" "}
              {lang === "zh" ? "取消" : "Cancel"}
            </button>
          </div>
        ) : null}

        {!xferRunning && xfer.result && xfer.state === "done" ? (
          <span className="files-tab__msg files-tab__msg--ok">
            {xfer.result.direction === "pull"
              ? lang === "zh" ? "拉取成功 · " : "Pulled · "
              : lang === "zh" ? "推送成功 · " : "Pushed · "}
            {xfer.result.direction === "pull"
              ? xfer.result.local
              : xfer.result.remote}
            {xfer.result.bytes_transferred
              ? ` (${formatBytes(xfer.result.bytes_transferred)})`
              : ""}
          </span>
        ) : null}

        {!xferRunning && xfer.state === "cancelled" ? (
          <span className="files-tab__msg">
            {lang === "zh" ? "已取消" : "Cancelled"}
          </span>
        ) : null}

        {!xferRunning && xfer.state === "error" && xfer.error ? (
          <span className="files-tab__msg files-tab__msg--err">
            {xfer.result?.direction === "pull"
              ? lang === "zh" ? "拉取失败 · " : "Pull failed · "
              : lang === "zh" ? "推送失败 · " : "Push failed · "}
            {xfer.error}
          </span>
        ) : null}
      </div>

      <HitlConfirmModal
        open={pendingPush !== null}
        title={
          lang === "zh"
            ? "敏感路径写入确认"
            : "Sensitive path push confirmation"
        }
        description={
          lang === "zh"
            ? "目标路径属于系统敏感前缀，覆写可能影响系统稳定性甚至导致设备无法启动。继续？"
            : "The target path is in a sensitive system prefix. Overwriting may destabilise the device or prevent boot. Continue?"
        }
        details={
          pendingPush
            ? {
                local: <code>{pendingPush.local}</code>,
                remote: <code>{pendingPush.remote}</code>,
                reason: pendingPush.error,
              }
            : undefined
        }
        cancelLabel={lang === "zh" ? "取消" : "Cancel"}
        approveLabel={
          lang === "zh" ? "确认覆写（force）" : "Confirm push (force)"
        }
        approveDanger
        pending={xferRunning && xfer.result?.direction !== "pull"}
        onCancel={() => setPendingPush(null)}
        onApprove={confirmPush}
      />
    </div>
  );
}

interface PaneEntry {
  name: string;
  is_dir: boolean;
  is_link: boolean;
  meta: string;
  mtime: string;
}

interface FilePaneProps {
  title: string;
  subtitle: string;
  path: string;
  onPathChange: (p: string) => void;
  onUp: () => void;
  onRefresh: () => void;
  isFetching: boolean;
  loading: boolean;
  error: string | null;
  truncated: boolean;
  entries: PaneEntry[];
  activeName: string | null;
  onActivate: (name: string) => void;
  lang: Lang;
  children?: React.ReactNode;
}

function FilePane(p: FilePaneProps) {
  return (
    <div className="files-tab__pane">
      <div className="files-tab__pane-head">
        <div>
          <div className="files-tab__pane-title">{p.title}</div>
          <div className="files-tab__pane-sub">{p.subtitle}</div>
        </div>
        <div className="files-tab__pane-actions">
          <button
            type="button"
            className="btn"
            onClick={p.onUp}
            aria-label="Parent directory"
            title="parent dir"
          >
            <FolderUp size={12} className="icon-inline" />
          </button>
          <button
            type="button"
            className="btn"
            onClick={p.onRefresh}
            disabled={p.isFetching}
            aria-label="Refresh"
            title="refresh"
          >
            <RefreshCw
              size={12}
              className="icon-inline"
              className={p.isFetching ? "spin" : ""}
            />
          </button>
        </div>
      </div>
      <div className="files-tab__pane-path">
        <input
          type="text"
          value={p.path}
          onChange={(e) => p.onPathChange(e.target.value)}
          spellCheck={false}
        />
      </div>
      <div className="files-tab__pane-list">
        {p.loading ? (
          <div className="files-tab__loading">
            {p.lang === "zh" ? "加载中…" : "loading…"}
          </div>
        ) : p.error ? (
          <div className="files-tab__error">{p.error}</div>
        ) : p.entries.length === 0 ? (
          <div className="files-tab__empty">
            {p.lang === "zh"
              ? "目录为空 · 上一级或换路径"
              : "Empty directory — go up or change path"}
          </div>
        ) : (
          <ul>
            {p.entries.map((e) => (
              <li
                key={e.name}
                className={p.activeName === e.name ? "is-active" : ""}
              >
                <button
                  type="button"
                  onClick={() => p.onActivate(e.name)}
                  onDoubleClick={() => {
                    if (e.is_dir) p.onActivate(e.name);
                  }}
                  aria-current={p.activeName === e.name ? "true" : undefined}
                >
                  <span className="files-tab__entry-name">
                    {e.is_dir ? "📁 " : e.is_link ? "🔗 " : "· "}
                    {e.name}
                  </span>
                  <span className="files-tab__entry-meta">{e.meta}</span>
                  <span className="files-tab__entry-mtime">{e.mtime}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {p.truncated ? (
          <div className="files-tab__truncated">
            (list truncated — narrow the path to see more)
          </div>
        ) : null}
      </div>
      <div className="files-tab__pane-foot">{p.children}</div>
    </div>
  );
}

function joinPath(base: string, leaf: string): string {
  if (!base.endsWith("/")) base = `${base}/`;
  return `${base}${leaf}`;
}

function parentPath(p: string, sep: "/" = "/"): string {
  const norm = p.endsWith(sep) ? p.slice(0, -1) : p;
  const i = norm.lastIndexOf(sep);
  if (i <= 0) return sep;
  return norm.slice(0, i + 1);
}


/**
 * Trail-edge debounce — `value` echoes back after `delayMs` of no
 * changes. Used to keep input snappy while throttling expensive
 * downstream effects (TanStack queryKey churn → ls -la storm).
 */
function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}
