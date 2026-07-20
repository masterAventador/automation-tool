import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskTargetPreview } from "../../api/control-plane/task-target-previews";
import { TauriTaskTargetPreviewSource } from "./task-target-preview-source";

const TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const TARGET_ID = "16fd2706-8baf-433b-82eb-8c7fada847da";
const PREVIEW: TaskTargetPreview = {
  taskId: TASK_ID,
  taskStatus: "awaiting_confirmation",
  taskRevision: 4,
  lastEventSequence: 3,
  pageRevision: 7,
  selectedTargetCount: 1,
  userExcludedTargetCount: 0,
  confirmed: false,
  confirmedAt: null,
  items: [
    {
      targetId: TARGET_ID,
      ordinal: 1,
      displayName: "预览目标",
      publicHandle: "public_1",
      source: "general_search_author",
      disposition: "eligible",
      userExcluded: false,
      selected: true,
    },
  ],
  nextCursor: null,
};

const invoke = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

describe("Tauri target preview source", () => {
  beforeEach(() => invoke.mockReset());

  it("uses only the three fixed native commands", async () => {
    invoke.mockResolvedValue(PREVIEW);
    const source = new TauriTaskTargetPreviewSource();

    await expect(source.getPreview({ taskId: TASK_ID, limit: 20 })).resolves.toEqual(PREVIEW);
    await expect(
      source.replaceExclusions({
        taskId: TASK_ID,
        pageRevision: 7,
        expectedTaskRevision: 4,
        excludedTargetIds: [TARGET_ID],
        idempotencyKey: "task:preview:exclude",
      }),
    ).resolves.toEqual(PREVIEW);
    await expect(
      source.confirm({
        taskId: TASK_ID,
        pageRevision: 7,
        expectedTaskRevision: 4,
        idempotencyKey: "task:preview:confirm",
      }),
    ).resolves.toEqual(PREVIEW);

    expect(invoke).toHaveBeenNthCalledWith(1, "get_task_target_preview", {
      taskId: TASK_ID,
      cursor: null,
      limit: 20,
    });
    expect(invoke).toHaveBeenNthCalledWith(2, "replace_task_target_exclusions", {
      taskId: TASK_ID,
      pageRevision: 7,
      expectedTaskRevision: 4,
      excludedTargetIds: [TARGET_ID],
      idempotencyKey: "task:preview:exclude",
    });
    expect(invoke).toHaveBeenNthCalledWith(3, "confirm_task_target_preview", {
      taskId: TASK_ID,
      pageRevision: 7,
      expectedTaskRevision: 4,
      idempotencyKey: "task:preview:confirm",
    });
  });

  it("rejects malformed native state and redacts native failures", async () => {
    const source = new TauriTaskTargetPreviewSource();
    invoke.mockResolvedValueOnce({ ...PREVIEW, platformTargetId: "private" });
    await expect(source.getPreview({ taskId: TASK_ID, limit: 20 })).rejects.toMatchObject({
      code: "protocol_mismatch",
      retryable: false,
    });

    invoke.mockRejectedValueOnce({ message: "credential=private", code: "private" });
    const failure = source.getPreview({ taskId: TASK_ID, limit: 20 });
    await expect(failure).rejects.toMatchObject({
      code: "transport_unavailable",
      message: "Target preview service is unavailable",
      retryable: true,
    });
    await expect(failure).rejects.not.toHaveProperty("cause");
  });

  it("does not invoke native code after cancellation", async () => {
    const controller = new AbortController();
    controller.abort();
    const source = new TauriTaskTargetPreviewSource();
    await expect(
      source.getPreview({ taskId: TASK_ID, limit: 20, signal: controller.signal }),
    ).rejects.toMatchObject({ code: "request_cancelled", retryable: false });
    expect(invoke).not.toHaveBeenCalled();
  });
});
