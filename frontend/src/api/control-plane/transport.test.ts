import { beforeEach, describe, expect, it, vi } from "vitest";

import { createTransportStartupCheck } from "../../app/startup";
import { TauriControlPlaneTransport } from "../../platform/tauri/control-plane-transport";
import { ControlPlaneTransportError, type ControlPlaneHealth } from "./transport";
import { createTestHarnessControlPlaneTransport } from "./test-harness";

const healthy: ControlPlaneHealth = {
  status: "available",
  serviceVersion: "0.1.0",
};

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

describe("ControlPlaneTransport boundary", () => {
  beforeEach(() => {
    invoke.mockReset();
  });

  it("formal Tauri transport invokes only the fixed native health command", async () => {
    invoke.mockResolvedValueOnce(healthy);
    const transport = new TauriControlPlaneTransport();

    await expect(transport.checkHealth()).resolves.toEqual(healthy);
    expect(invoke).toHaveBeenCalledOnce();
    expect(invoke).toHaveBeenCalledWith("check_control_plane_health");
  });

  it("maps native rejection to a fixed public error without reflecting its cause", async () => {
    invoke.mockRejectedValueOnce({
      code: "private-native-code",
      message: "password=private-native-password",
    });
    const transport = new TauriControlPlaneTransport();

    const request = transport.checkHealth();
    await expect(request).rejects.toMatchObject({
      name: "ControlPlaneTransportError",
      code: "transport_unavailable",
      message: "Control Plane transport is unavailable",
      retryable: true,
    });
    await expect(request).rejects.not.toHaveProperty("cause");
  });

  it("preserves only the exact native Installation revocation category", async () => {
    invoke.mockRejectedValueOnce({
      code: "installation_access_denied",
      retryable: false,
    });
    const transport = new TauriControlPlaneTransport();

    await expect(transport.checkHealth()).rejects.toMatchObject({
      name: "ControlPlaneTransportError",
      code: "installation_access_denied",
      message: "Installation access is unavailable",
      retryable: false,
    });
  });

  it("preserves the native installation conflict so startup can explain it", async () => {
    invoke.mockRejectedValueOnce({
      code: "installation_conflict",
      retryable: false,
    });
    const transport = new TauriControlPlaneTransport();

    await expect(transport.checkHealth()).rejects.toMatchObject({
      name: "ControlPlaneTransportError",
      code: "installation_conflict",
      message: "Installation registration conflicts with the service",
      retryable: false,
    });
  });

  it("rejects malformed native responses without treating protocol failures as retryable", async () => {
    invoke.mockResolvedValueOnce({
      status: "available",
      serviceVersion: "0.1.0",
      privateCredential: "atdc1.private",
    });
    const transport = new TauriControlPlaneTransport();

    const request = transport.checkHealth();
    await expect(request).rejects.toMatchObject({
      code: "operation_unavailable",
      retryable: false,
      message: "Control Plane operation is unavailable",
    });
    await expect(request).rejects.not.toHaveProperty("cause");
  });

  it("honors cancellation without invoking native code when already aborted", async () => {
    const controller = new AbortController();
    controller.abort();
    const transport = new TauriControlPlaneTransport();

    await expect(
      transport.checkHealth({ signal: controller.signal }),
    ).rejects.toMatchObject({
      code: "request_cancelled",
      retryable: false,
    });
    expect(invoke).not.toHaveBeenCalled();
  });

  it("settles an in-flight health request as cancelled", async () => {
    const controller = new AbortController();
    invoke.mockImplementationOnce(() => new Promise(() => undefined));
    const transport = new TauriControlPlaneTransport();

    const request = transport.checkHealth({ signal: controller.signal });
    controller.abort();

    await expect(request).rejects.toMatchObject({
      code: "request_cancelled",
      retryable: false,
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
    const revoked = createTransportStartupCheck(
      createTestHarnessControlPlaneTransport({
        checkHealth: vi
          .fn()
          .mockRejectedValue(
            new ControlPlaneTransportError("installation_access_denied", false),
          ),
      }),
    );

    await expect(available.check()).resolves.toEqual({ status: "ready" });
    await expect(unavailable.check()).resolves.toEqual({ status: "unavailable" });
    await expect(revoked.check()).resolves.toEqual({ status: "revoked" });
  });
});
