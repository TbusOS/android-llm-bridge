/**
 * deviceFormat spec — AR9-6: the Transport union mirrors what the backend
 * actually emits (Adb / Ssh / Serial / Hybrid class names). Hybrid must
 * map to its own pill instead of being silently labeled adb-usb.
 */
import { describe, expect, it } from "vitest";

import { transportFromName, transportLabel } from "./deviceFormat";

describe("transportFromName (AR9-6)", () => {
  it("maps the backend transport class names", () => {
    expect(transportFromName("SshTransport")).toBe("ssh");
    expect(transportFromName("SerialTransport")).toBe("uart");
    expect(transportFromName("HybridTransport")).toBe("hybrid");
    expect(transportFromName("AdbTransport")).toBe("adb-usb");
  });

  it("falls back to adb-usb for null / unknown", () => {
    expect(transportFromName(null)).toBe("adb-usb");
    expect(transportFromName(undefined)).toBe("adb-usb");
    expect(transportFromName("WhoKnowsTransport")).toBe("adb-usb");
  });
});

describe("transportLabel (AR9-6)", () => {
  it("labels every union member", () => {
    expect(transportLabel("adb-usb")).toBe("adb");
    expect(transportLabel("uart")).toBe("uart");
    expect(transportLabel("ssh")).toBe("ssh");
    expect(transportLabel("hybrid")).toBe("hybrid");
  });
});
