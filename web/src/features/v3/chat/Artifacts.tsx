/**
 * Files an answer produced (saved logs, screenshots, bug reports), shown on
 * the turn as chips. Tools report absolute paths; a file under the hub's
 * workspace opens in place (text in the log dialog, a screenshot in a new
 * tab) and can be downloaded. A file elsewhere only offers its path.
 */
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";

import { Icon } from "../../../components/v3/Icon";
import { LogDialog } from "../../../components/v3/LogDialog";
import { toast } from "../../../components/v3/ui";
import { listWorkspaceFiles, screenshotImageUrl, workspaceDownloadUrl } from "../../../lib/api";
import { copyText } from "../../../lib/clipboard";
import { useT } from "../../../lib/i18n";
import { baseName, fileKind, screenshotRef, toLines, workspaceRel } from "./toolText";
import "./assistant.css";

/** The hub's preview cap; larger files open truncated with a note. */
const PREVIEW_BYTES = 1 << 20;

interface Preview {
  text: string;
  truncated: boolean;
  binary: boolean;
  size: number;
}

async function readWorkspaceText(rel: string, signal?: AbortSignal): Promise<Preview> {
  const safe = rel
    .split("/")
    .filter((seg) => seg.length > 0)
    .map(encodeURIComponent)
    .join("/");
  const r = await fetch(`/workspace/files/preview/${safe}?max_bytes=${PREVIEW_BYTES}`, { signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const d = (await r.json()) as { ok?: boolean; text?: string; truncated?: boolean; is_binary?: boolean; size_bytes?: number; error?: string };
  if (d.ok === false) throw new Error(d.error ?? "preview failed");
  return { text: d.text ?? "", truncated: !!d.truncated, binary: !!d.is_binary, size: d.size_bytes ?? 0 };
}

export function ArtifactRow({ paths }: { paths: string[] }) {
  const t = useT();
  const root = useQuery({
    queryKey: ["chat3-workspace-root"],
    queryFn: ({ signal }) => listWorkspaceFiles("", signal),
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
  const rootPath = root.data?.ok ? (root.data.absolute_path ?? null) : null;
  const [viewing, setViewing] = useState<{ rel: string; name: string } | null>(null);

  return (
    <div className="chat__files">
      <span>{t("产出文件", "Files")}</span>
      {paths.map((p) => (
        <ArtifactChip key={p} path={p} rel={workspaceRel(p, rootPath)} rootKnown={!root.isLoading} onOpen={(rel, name) => setViewing({ rel, name })} />
      ))}
      {viewing ? <FileDialog rel={viewing.rel} name={viewing.name} onClose={() => setViewing(null)} /> : null}
    </div>
  );
}

function ArtifactChip({ path, rel, rootKnown, onOpen }: { path: string; rel: string | null; rootKnown: boolean; onOpen: (rel: string, name: string) => void }) {
  const t = useT();
  const name = baseName(path);
  const kind = fileKind(name);
  const shot = rel ? screenshotRef(rel) : null;
  const copyPath = async () => {
    toast((await copyText(path)) ? t("已复制路径。", "Path copied.") : t("复制失败。", "Copy failed."));
  };
  let label: ReactNode;
  if (rel && kind === "text") {
    label = (
      <button type="button" onClick={() => onOpen(rel, name)} aria-label={t(`打开 ${name}`, `Open ${name}`)}>
        {name}
      </button>
    );
  } else if (shot) {
    label = (
      <a href={screenshotImageUrl(shot.serial, shot.name)} target="_blank" rel="noopener noreferrer" aria-label={t(`打开 ${name}`, `Open ${name}`)}>
        {name}
      </a>
    );
  } else {
    label = <span>{name}</span>;
  }
  return (
    <span className="chip chip--sm chat__file" title={path}>
      <Icon name={kind === "image" ? "camera" : "attach"} size="sm" />
      {label}
      {rel ? (
        <a className="chat__file-act" href={workspaceDownloadUrl(rel)} download={name} aria-label={t(`下载 ${name}`, `Download ${name}`)} title={t("下载", "Download")}>
          <Icon name="download" size="sm" />
        </a>
      ) : rootKnown ? (
        <button type="button" className="chat__file-act" onClick={() => void copyPath()} aria-label={t(`复制 ${name} 的路径`, `Copy the path of ${name}`)} title={t("不在工作区里,只能复制路径", "Not in the workspace; copy its path")}>
          <Icon name="copy" size="sm" />
        </button>
      ) : null}
    </span>
  );
}

function FileDialog({ rel, name, onClose }: { rel: string; name: string; onClose: () => void }) {
  const t = useT();
  const q = useQuery({
    queryKey: ["chat3-artifact", rel],
    queryFn: ({ signal }) => readWorkspaceText(rel, signal),
    staleTime: 30_000,
    retry: false,
  });
  const d = q.data;
  const error = q.isError
    ? `${t("读不到这个文件:", "Cannot read this file: ")}${q.error instanceof Error ? q.error.message : String(q.error)}`
    : d?.binary
      ? t("这个文件不是文本,用旁边的下载按钮存下来看。", "This file is not text; use the download button next to it.")
      : null;
  return (
    <LogDialog
      open
      onClose={onClose}
      title={name}
      sub={rel}
      lines={d && !d.binary ? toLines(d.text) : []}
      loading={q.isFetching}
      error={error}
      note={d?.truncated ? t(`只读了前 1 MB(整个文件 ${Math.round(d.size / 1024)} KB),全文请下载。`, `Only the first 1 MB was read (the file is ${Math.round(d.size / 1024)} KB); download it for the rest.`) : undefined}
      fileName={name}
      onRefresh={() => void q.refetch()}
    />
  );
}
