import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { SmartEditGatewayError } from "../../features/video-editing/smart-edit-gateway";
import { TauriSmartEditGateway } from "./smart-edit-gateway";

const PROJECT_ID = "0a48954d-2df1-4168-8f33-b62c5772845a";
const GENERATION_ID = "3d594650-b5f4-4498-8e38-0cf85d6dfa72";
const TIMELINE_ID = "1b70168c-90d0-4ac7-938a-51eb4754f32a";
const MATERIAL_ID = "9f48954d-2df1-4168-8f33-b62c5772845b";

const request = {
  projectId: PROJECT_ID,
  prompt: "把发布会开场剪成一条节奏明快的短片",
  enableThinking: false,
  mode: "draft" as const,
};

function running(overrides: Record<string, unknown> = {}) {
  return {
    generationId: GENERATION_ID,
    projectId: PROJECT_ID,
    mode: "draft",
    status: "running",
    stage: "preparing",
    progressPermille: 0,
    timeline: null,
    renderJob: null,
    failureCode: null,
    ...overrides,
  };
}

function succeeded() {
  return {
    ...running(),
    status: "succeeded",
    stage: "completed",
    progressPermille: 1_000,
    timeline: {
      timelineId: TIMELINE_ID,
      projectId: PROJECT_ID,
      revision: 1,
      durationMs: 1_000,
      tracks: [
        {
          trackId: "visual",
          kind: "visual",
          clips: [
            {
              clipId: "visual-0001",
              startMs: 0,
              durationMs: 1_000,
              sourceMaterialId: MATERIAL_ID,
              sourceInMs: 0,
              sourceOutMs: 1_000,
              text: null,
              gainDb: null,
              transitionIn: null,
              originalAudioMode: null,
            },
          ],
        },
      ],
      createdAt: "2026-08-01T00:00:00Z",
    },
  };
}

describe("Tauri smart-edit gateway", () => {
  beforeEach(() => {
    invoke.mockReset();
    vi.useRealTimers();
  });

  it("uses the three fixed commands and binds every snapshot to the request", async () => {
    invoke
      .mockResolvedValueOnce(running())
      .mockResolvedValueOnce(running({ status: "cancelling" }))
      .mockResolvedValueOnce(succeeded());
    const gateway = new TauriSmartEditGateway();

    await expect(gateway.start(request)).resolves.toEqual(running());
    await expect(gateway.cancel(GENERATION_ID)).resolves.toEqual(
      running({ status: "cancelling" }),
    );
    await expect(gateway.get(GENERATION_ID)).resolves.toEqual(succeeded());
    expect(invoke.mock.calls).toEqual([
      ["start_smart_edit_generation", { request }],
      ["cancel_smart_edit_generation", { generationId: GENERATION_ID }],
      ["get_smart_edit_generation", { generationId: GENERATION_ID }],
    ]);
  });

  it("polls with a bound and stops at the first terminal snapshot", async () => {
    vi.useFakeTimers();
    invoke.mockResolvedValueOnce(running()).mockResolvedValueOnce(succeeded());
    const pending = new TauriSmartEditGateway().waitForTerminal(GENERATION_ID);
    await vi.advanceTimersByTimeAsync(250);
    await expect(pending).resolves.toEqual(succeeded());
    expect(invoke).toHaveBeenCalledTimes(2);
  });

  it("honors AbortSignal before polling and rejects invalid input before invoke", async () => {
    const controller = new AbortController();
    controller.abort();
    const gateway = new TauriSmartEditGateway();
    await expect(
      gateway.waitForTerminal(GENERATION_ID, { signal: controller.signal }),
    ).rejects.toMatchObject({ code: "polling_cancelled", retryable: false });
    await expect(gateway.get("private-invalid-id")).rejects.toMatchObject({
      code: "invalid_request",
      retryable: false,
    });
    expect(invoke).not.toHaveBeenCalled();
  });

  it("interrupts an in-flight polling delay without another native call", async () => {
    vi.useFakeTimers();
    invoke.mockResolvedValueOnce(running());
    const controller = new AbortController();
    const pending = new TauriSmartEditGateway().waitForTerminal(GENERATION_ID, {
      signal: controller.signal,
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(invoke).toHaveBeenCalledTimes(1);
    controller.abort();
    await expect(pending).rejects.toMatchObject({
      code: "polling_cancelled",
      retryable: false,
    });
    await vi.advanceTimersByTimeAsync(250);
    expect(invoke).toHaveBeenCalledTimes(1);
  });

  it("maps only the fixed native errors and never reflects private payloads", async () => {
    invoke.mockRejectedValueOnce({
      code: "generation_not_found",
      message: "fixed",
      retryable: false,
    });
    const known = await new TauriSmartEditGateway()
      .get(GENERATION_ID)
      .catch((error: unknown) => error);
    expect(known).toBeInstanceOf(SmartEditGatewayError);
    expect(known).toMatchObject({ code: "generation_not_found", retryable: false });

    invoke.mockRejectedValueOnce({
      code: "private_code",
      message: "prompt=private /Users/private/result.json",
      retryable: true,
    });
    const opaque = await new TauriSmartEditGateway()
      .get(GENERATION_ID)
      .catch((error: unknown) => error);
    expect(opaque).toMatchObject({ code: "operation_unavailable", retryable: false });
    expect(JSON.stringify(opaque)).not.toContain("private");

    invoke.mockRejectedValueOnce({
      code: "generation_not_found",
      message: "fixed",
      retryable: true,
    });
    await expect(new TauriSmartEditGateway().get(GENERATION_ID)).rejects.toMatchObject({
      code: "operation_unavailable",
      retryable: false,
    });
  });

  it("rejects expanded or cross-generation native snapshots", async () => {
    invoke.mockResolvedValueOnce({ ...running(), privatePath: "/private/result.json" });
    await expect(new TauriSmartEditGateway().get(GENERATION_ID)).rejects.toMatchObject({
      code: "operation_unavailable",
      retryable: false,
    });

    invoke.mockResolvedValueOnce(
      running({ generationId: "4d594650-b5f4-4498-8e38-0cf85d6dfa73" }),
    );
    await expect(new TauriSmartEditGateway().get(GENERATION_ID)).rejects.toMatchObject({
      code: "operation_unavailable",
      retryable: false,
    });
  });
});
