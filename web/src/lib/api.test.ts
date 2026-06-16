/**
 * api.ts spec — envelope normalisation + app write-op request bodies.
 *
 * `parseEnvelope` exists because endpoints whitelist 4xx/503 as
 * envelope-bearing, but FastAPI request-validation rejects return a
 * bare `{ detail }` body instead. Covers the three detail shapes the
 * backend can emit: plain string, validation-error array, missing.
 *
 * The clear-data / uninstall body specs pin the `allow_dangerous`
 * wire contract — the backend refuses both ops without it.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  parseEnvelope,
  postAppClearData,
  postAppUninstall,
} from "./api";

function fakeResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("parseEnvelope", () => {
  it("passes a real envelope through untouched (ok: true)", async () => {
    const env = { ok: true, data: { packages: ["a"], count: 1 } };
    await expect(parseEnvelope(fakeResponse(200, env))).resolves.toBe(env);
  });

  it("passes a real error envelope through untouched (ok: false)", async () => {
    const env = {
      ok: false,
      error: { code: "NO_DEVICE", message: "no device selected" },
    };
    await expect(parseEnvelope(fakeResponse(503, env))).resolves.toBe(env);
  });

  it("synthesises an envelope from a string detail", async () => {
    const r = fakeResponse(400, { detail: "device busy" });
    await expect(parseEnvelope(r)).resolves.toEqual({
      ok: false,
      error: { code: "HTTP_400", message: "device busy" },
    });
  });

  it("synthesises an envelope from a FastAPI validation-error array", async () => {
    const detail = [
      { loc: ["body", "package"], msg: "Field required", type: "missing" },
    ];
    const r = fakeResponse(422, { detail });
    const env = await parseEnvelope(r);
    expect(env.ok).toBe(false);
    expect((env as { error?: { code: string; message: string } }).error).toEqual({
      code: "HTTP_422",
      message: JSON.stringify(detail),
    });
  });

  it("synthesises an envelope when detail is missing", async () => {
    const env = await parseEnvelope(fakeResponse(503, {}));
    expect(env).toEqual({
      ok: false,
      error: { code: "HTTP_503", message: JSON.stringify("") },
    });
  });

  it("tolerates a null body", async () => {
    const env = await parseEnvelope(fakeResponse(500, null));
    expect(env).toEqual({
      ok: false,
      error: { code: "HTTP_500", message: JSON.stringify("") },
    });
  });
});

describe("app write-op request bodies", () => {
  function stubFetch() {
    const fetchMock = vi.fn(async () =>
      fakeResponse(200, { ok: true, data: {} }),
    );
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  function bodyOf(fetchMock: ReturnType<typeof vi.fn>): unknown {
    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    return JSON.parse(init.body as string);
  }

  it("clear-data sends allow_dangerous: true when opted in", async () => {
    const fetchMock = stubFetch();
    await postAppClearData("serial1", "com.example.app", {
      allow_dangerous: true,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/app/clear-data?device=serial1",
      expect.objectContaining({ method: "POST" }),
    );
    expect(bodyOf(fetchMock)).toEqual({
      package: "com.example.app",
      allow_dangerous: true,
    });
  });

  it("clear-data defaults allow_dangerous to false", async () => {
    const fetchMock = stubFetch();
    await postAppClearData("serial1", "com.example.app");
    expect(bodyOf(fetchMock)).toEqual({
      package: "com.example.app",
      allow_dangerous: false,
    });
  });

  it("uninstall sends allow_dangerous alongside keep_data (existing contract)", async () => {
    const fetchMock = stubFetch();
    await postAppUninstall("serial1", "com.example.app", {
      allow_dangerous: true,
    });
    expect(bodyOf(fetchMock)).toEqual({
      package: "com.example.app",
      keep_data: false,
      allow_dangerous: true,
    });
  });
});
