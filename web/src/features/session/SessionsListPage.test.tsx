/**
 * Sessions list: a session is named by its first user line (the hub reads
 * it from messages.jsonl) and says where it was started; sessions without a
 * title keep showing their id.
 */
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderPage, stubFetch } from "../../test/renderPage";
import { SessionsListPage } from "./SessionsListPage";

const SESSIONS = [
  { session_id: "20261030-aaaa1111", title: "Why did the last boot panic?", source: "cli", created: "2026-10-30T08:00:00+00:00", backend: "ollama", model: "qwen-x", device: null, turns: 4, last_event_ts: null },
  { session_id: "20260427-bbbb2222", title: "", source: "", created: "2026-04-27T08:00:00+00:00", backend: "ollama", model: "qwen-x", device: null, turns: 0, last_event_ts: null },
];

describe("Sessions list", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(stubFetch({ "/sessions": { ok: true, sessions: SESSIONS } })));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("names a session by its title, with the id and the source below it", async () => {
    await renderPage(<SessionsListPage />);
    expect(await screen.findByText("Why did the last boot panic?")).toBeInTheDocument();
    expect(screen.getByText(/20261030-aaaa1111 · (命令行|command line)/)).toBeInTheDocument();
    // no title: the id is the name, as before
    expect(screen.getByText("20260427-bbbb2222")).toBeInTheDocument();
  });

  it("the filter matches the title", async () => {
    await renderPage(<SessionsListPage />);
    await screen.findByText("Why did the last boot panic?");
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "panic" } });
    await waitFor(() => expect(screen.queryByText("20260427-bbbb2222")).toBeNull());
    expect(screen.getByText("Why did the last boot panic?")).toBeInTheDocument();
  });
});
