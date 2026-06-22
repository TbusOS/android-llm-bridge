/**
 * FilesTab spec — drag-drop / picker upload into the workspace pane.
 *
 * The file-browser queries, the push/pull stream, react-query, and the
 * zustand store are all mocked so the spec exercises only the upload
 * interaction: drag highlight, drop → uploadWorkspaceFile(dir, file),
 * and the success / failure status messages.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { mockUseApp, mockUpload, mockXfer } = vi.hoisted(() => ({
  mockUseApp: vi.fn((selector?: (s: { lang: string; device: string }) => unknown) => {
    const s = { lang: "en", device: "serial-1" };
    return selector ? selector(s) : s;
  }),
  mockUpload: vi.fn(),
  mockXfer: {
    state: "idle",
    progress: null,
    result: null,
    error: null,
    inflight: null,
    start: vi.fn(),
    cancel: vi.fn(),
  },
}));

vi.mock("../../stores/app", () => ({ useApp: mockUseApp }));
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));
vi.mock("./useFileTransferStream", () => ({
  useFileTransferStream: () => mockXfer,
}));
vi.mock("./useFileBrowser", () => ({
  useDeviceFiles: () => ({
    data: { ok: true, entries: [] },
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useWorkspaceFiles: () => ({
    data: { ok: true, entries: [] },
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useWorkspacePreview: () => ({ data: undefined, isLoading: false, isError: false }),
}));
vi.mock("../../lib/api", () => ({
  uploadWorkspaceFile: mockUpload,
  workspaceDownloadUrl: (p: string) => `/workspace/files/download/${p}`,
}));

import { FilesTab } from "./FilesTab";

function workspacePane(): HTMLElement {
  // the workspace pane is the drop target — it owns the Upload button.
  const btn = screen.getByRole("button", { name: /^upload$/i });
  const pane = btn.closest(".files-tab__pane");
  if (!pane) throw new Error("workspace pane not found");
  return pane as HTMLElement;
}

const FILE = new File(["payload"], "note.txt", { type: "text/plain" });

describe("FilesTab upload (drag-drop / picker)", () => {
  it("renders an Upload control in the workspace pane", () => {
    mockUpload.mockResolvedValue({ ok: true, name: "note.txt" });
    render(<FilesTab />);
    expect(screen.getByRole("button", { name: /^upload$/i })).toBeTruthy();
  });

  it("shows the drop hint while dragging files over the workspace", () => {
    mockUpload.mockResolvedValue({ ok: true, name: "note.txt" });
    const { container } = render(<FilesTab />);
    fireEvent.dragEnter(workspacePane());
    expect(container.querySelector(".files-tab__drop-hint")).not.toBeNull();
    expect(container.querySelector(".files-tab__pane--dragover")).not.toBeNull();
  });

  it("dropping a file uploads it into the workspace dir + shows success", async () => {
    mockUpload.mockResolvedValue({ ok: true, name: "note.txt", size: 7 });
    render(<FilesTab />);
    fireEvent.drop(workspacePane(), { dataTransfer: { files: [FILE] } });
    expect(mockUpload).toHaveBeenCalledWith("devices/serial-1", FILE);
    expect(await screen.findByText(/Uploaded/)).toBeTruthy();
  });

  it("a rejected upload surfaces the failure message", async () => {
    mockUpload.mockResolvedValue({ ok: false, error: "path escapes workspace" });
    render(<FilesTab />);
    fireEvent.drop(workspacePane(), { dataTransfer: { files: [FILE] } });
    expect(await screen.findByText(/Upload failed/)).toBeTruthy();
    expect(screen.getByText(/path escapes workspace/)).toBeTruthy();
  });

  it("clears the drag highlight after the drop", async () => {
    mockUpload.mockResolvedValue({ ok: true, name: "note.txt" });
    const { container } = render(<FilesTab />);
    const pane = workspacePane();
    fireEvent.dragEnter(pane);
    expect(container.querySelector(".files-tab__pane--dragover")).not.toBeNull();
    fireEvent.drop(pane, { dataTransfer: { files: [FILE] } });
    expect(container.querySelector(".files-tab__pane--dragover")).toBeNull();
    await screen.findByText(/Uploaded/);
  });
});
