import { describe, expect, it, vi } from "vitest";

import { createTransportStartupCheck } from "../../app/startup";
import { TauriControlPlaneTransport } from "../../platform/tauri/control-plane-transport";
import { ControlPlaneTransportError, type ControlPlaneHealth } from "./transport";
import { createTestHarnessControlPlaneTransport } from "./test-harness";

const healthy: ControlPlaneHealth = {
  status: "available",
  serviceVersion: "0.1.0",
};

describe("ControlPlaneTransport boundary", () => {
  it("formal Tauri stub fails safely until the Rust operation is wired", async () => {
    const transport = new TauriControlPlaneTransport();

    await expect(transport.checkHealth()).rejects.toMatchObject({
      name: "ControlPlaneTransportError",
      code: "transport_unavailable",
      message: "Control Plane transport is unavailable",
      retryable: true,
    });
  });

  it("does not reflect private causes through the public transport error", () => {
    const error = new ControlPlaneTransportError("transport_unavailable", true);

    expect(String(error)).not.toContain("private-password");
    expect(error.cause).toBeUndefined();
  });

  it("test Harness delegates only the explicit health operation and signal", async () => {
    const checkHealth = vi.fn().mockResolvedValue(healthy);
    const transport = createTestHarnessControlPlaneTransport({ checkHealth });
    const controller = new AbortController();

    await expect(transport.checkHealth({ signal: controller.signal })).resolves.toEqual(healthy);
    expect(checkHealth).toHaveBeenCalledWith({ signal: controller.signal });
  });

  it("test Harness fails closed when the explicit health handler is absent", async () => {
    const transport = createTestHarnessControlPlaneTransport({});

    await expect(transport.checkHealth()).rejects.toMatchObject({
      code: "operation_unavailable",
      retryable: false,
    });
  });

  it("adapts transport health to the existing startup gate without leaking failures", async () => {
    const available = createTransportStartupCheck(
      createTestHarnessControlPlaneTransport({
        checkHealth: vi.fn().mockResolvedValue(healthy),
      }),
    );
    const unavailable = createTransportStartupCheck(
      createTestHarnessControlPlaneTransport({
        checkHealth: vi.fn().mockRejectedValue(new Error("private-password")),
      }),
    );

    await expect(available.check()).resolves.toEqual({ status: "ready" });
    await expect(unavailable.check()).resolves.toEqual({ status: "unavailable" });
  });
});
