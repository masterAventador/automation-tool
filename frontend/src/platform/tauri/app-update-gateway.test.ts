import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { AppUpdateGatewayError } from "../../features/app-updates/contracts";
import { TauriAppUpdateGateway } from "./app-update-gateway";

const release = {
  version: "1.2.3",
  channel: "stable",
  policy: "optional",
  notes: null,
  publishedAt: "2026-07-22T00:00:00Z",
  artifact: {
    target: "darwin",
    arch: "aarch64",
    sha256: "a".repeat(64),
    sizeBytes: 1024,
  },
};

describe("Tauri App update gateway", () => {
  beforeEach(() => invoke.mockReset());

  it("uses only the three fixed native update commands", async () => {
    invoke
      .mockResolvedValueOnce({ state: "idle" })
      .mockResolvedValueOnce({ state: "up_to_date", trigger: "manual" })
      .mockResolvedValueOnce({ state: "ready", release, action: "deferred" });
    const gateway = new TauriAppUpdateGateway();

    await gateway.getState();
    await gateway.checkNow();
    await gateway.decide("defer");

    expect(invoke.mock.calls).toEqual([
      ["get_app_update_state", {}],
      ["check_app_update_now", {}],
      ["decide_app_update", { decision: "defer" }],
    ]);
  });

  it("accepts real progress and installation-launched native states", async () => {
    invoke
      .mockResolvedValueOnce({
        state: "downloading",
        release,
        downloadedBytes: 512,
        totalBytes: 1024,
      })
      .mockResolvedValueOnce({ state: "installation_launched", release });
    const gateway = new TauriAppUpdateGateway();

    await expect(gateway.getState()).resolves.toMatchObject({
      state: "downloading",
      downloadedBytes: 512,
    });
    await expect(gateway.getState()).resolves.toMatchObject({
      state: "installation_launched",
    });
  });

  it("rejects malformed native values and never reflects native secrets", async () => {
    const gateway = new TauriAppUpdateGateway();
    invoke.mockResolvedValueOnce({ state: "ready", release, action: "private_action" });
    await expect(gateway.getState()).rejects.toMatchObject({
      code: "protocol_mismatch",
      retryable: false,
    });

    invoke.mockRejectedValueOnce(new Error("signature=private-native-secret"));
    const error = await gateway.getState().catch((value: unknown) => value);
    expect(error).toBeInstanceOf(AppUpdateGatewayError);
    expect(error).toMatchObject({ code: "transport_unavailable", retryable: true });
    expect(String(error)).not.toContain("private-native-secret");
  });
});
