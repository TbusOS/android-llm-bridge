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
} from "lucide-react";

import { HitlConfirmModal } from "../../components/HitlConfirmModal";
import { useApp } from "../../stores/app";
import {
  type DeviceFileEntry,
  type WorkspaceFileEntry,
  workspaceDownloadUrl,
} from "../../lib/api";
import {
  useDeviceFiles,
  useFileTransfers,
  useWorkspaceFiles,
} from "./useFileBrowser";

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
  const { pullMutation, pushMutation } = useFileTransfers();

  if (!device) {
    return (
      <div className="mock-card">
        <h1 style={{ fontSize: 22 }}>{lang === "zh" ? "文件" : "Files"}</h1>
        <p className="section-sub">
          {lang === "zh"
            ? "顶栏选一台设备再回这里。"
            : "Pick a device from the top-bar picker, then come back."}
        </p>
      </div>
    );
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
    pullMutation.mutate({ serial: device, remote, local });
  };

  const onPush = (force = false) => {
    if (!selectedWorkspace || selectedWorkspace.is_dir) return;
    const local = workspacePath
      ? `${workspacePath}/${selectedWorkspace.name}`
      : selectedWorkspace.name;
    const remote = joinPath(devicePath, selectedWorkspace.name);
    pushMutation.mutate(
      { serial: device, local, remote, force },
      {
        onSuccess: (data) => {
          if (data.requires_confirm) {
            setPendingPush({
              serial: device,
              local,
              remote,
              error: data.error || "sensitive path",
            });
          } else {
            setPendingPush(null);
          }
        },
      },
    );
  };

  const confirmPush = () => {
    if (!pendingPush) return;
    pushMutation.mutate(
      { ...pendingPush, force: true },
      {
        onSuccess: () => setPendingPush(null),
      },
    );
  };

  return (
    <div className="files-tab">
      <div className="files-tab__panes">
        <FilePane
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
            meta: `${formatSize(e.size)} · ${e.mode}`,
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
              !selectedDevice || selectedDevice.is_dir || pullMutation.isPending
            }
            onClick={onPull}
          >
            <ArrowDownToLine size={12} style={{ verticalAlign: "-2px" }} />{" "}
            {pullMutation.isPending
              ? lang === "zh" ? "拉取中…" : "Pulling…"
              : lang === "zh" ? "拉到工作区" : "Pull"}
          </button>
        </FilePane>

        <FilePane
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
            meta: `${formatSize(e.size)}`,
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
              pushMutation.isPending
            }
            onClick={() => onPush(false)}
          >
            <ArrowUpFromLine size={12} style={{ verticalAlign: "-2px" }} />{" "}
            {pushMutation.isPending
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
              <Download size={12} style={{ verticalAlign: "-2px" }} />{" "}
              {lang === "zh" ? "下载" : "Download"}
            </a>
          ) : null}
        </FilePane>
      </div>

      <div className="files-tab__status">
        {pullMutation.data?.ok ? (
          <span className="files-tab__msg files-tab__msg--ok">
            {lang === "zh" ? "拉取成功 · " : "Pulled · "}
            {pullMutation.data.local}
          </span>
        ) : pullMutation.data?.error ? (
          <span className="files-tab__msg files-tab__msg--err">
            {lang === "zh" ? "拉取失败 · " : "Pull failed · "}
            {pullMutation.data.error}
          </span>
        ) : null}
        {pushMutation.data?.ok ? (
          <span className="files-tab__msg files-tab__msg--ok">
            {lang === "zh" ? "推送成功 · " : "Pushed · "}
            {pushMutation.data.remote} (
            {formatSize(pushMutation.data.bytes_transferred ?? 0)})
          </span>
        ) : pushMutation.data?.error && !pushMutation.data.requires_confirm ? (
          <span className="files-tab__msg files-tab__msg--err">
            {lang === "zh" ? "推送失败 · " : "Push failed · "}
            {pushMutation.data.error}
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
        pending={pushMutation.isPending}
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
            title="parent dir"
          >
            <FolderUp size={12} style={{ verticalAlign: "-2px" }} />
          </button>
          <button
            type="button"
            className="btn"
            onClick={p.onRefresh}
            disabled={p.isFetching}
            title="refresh"
          >
            <RefreshCw
              size={12}
              style={{ verticalAlign: "-2px" }}
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
          <div className="files-tab__loading">loading…</div>
        ) : p.error ? (
          <div className="files-tab__error">{p.error}</div>
        ) : p.entries.length === 0 ? (
          <div className="files-tab__empty">empty</div>
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

function formatSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
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
