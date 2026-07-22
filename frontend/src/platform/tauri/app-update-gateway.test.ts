import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { AppUpdateGatewayError } from "../../features/app-updates/contracts";
import { TauriAppUpdateGateway } from "./app-update-gateway";

const release = {
  version: "1.2.3",
  channel: "stable",
  policy: "optional",
  notes: "安全更新说明",
  publishedAt: "2026-07-22T00:00:00Z",
  artifact: {
    target: "darwin",
    arch: "aarch64",
    sha256: "a".repeat(64),
    sizeBytes: 1024,
  },
} as const;

describe("Tauri app update gateway", () => {
  beforeEach(() => invoke.mockReset());

  it("uses only the three fixed update commands and exact public states", async () => {
    invoke
      .mockResolvedValueOnce({ state: "ready", release, action: "prompt" })
      .mockResolvedValueOnce({ state: "up_to_date", trigger: "manual" })
      .mockResolvedValueOnce({ state: "installation_launched", release });
    const gateway = new TauriAppUpdateGateway();

    await expect(gateway.getState()).resolves.toMatchObject({ state: "ready" });
    await expect(gateway.checkNow()).resolves.toEqual({
      state: "up_to_date",
      trigger: "manual",
    });
    await expect(gateway.decide("install_now")).resolves.toMatchObject({
      state: "installation_launched",
    });
    expect(invoke.mock.calls).toEqual([
      ["get_app_update_state"],
      ["check_app_update_now"],
      ["decide_app_update", { decision: "install_now" }],
    ]);
  });

  it("rejects private fields and never reflects native failures", async () => {
    const gateway = new TauriAppUpdateGateway();
    invoke.mockResolvedValueOnce({
      state: "ready",
      release: { ...release, url: "https://private.invalid/update" },
      action: "prompt",
    });
    await expect(gateway.getState()).rejects.toMatchObject({ code: "protocol_mismatch" });

    invoke.mockRejectedValueOnce({
      code: "decision_unavailable",
      privatePath: "/Users/private/update",
    });
    const error = await gateway.decide("defer").catch((value: unknown) => value);
    expect(error).toBeInstanceOf(AppUpdateGatewayError);
    expect(error).toMatchObject({ code: "decision_unavailable" });
    expect(String(error)).not.toContain("/Users/private/update");
  });
});
