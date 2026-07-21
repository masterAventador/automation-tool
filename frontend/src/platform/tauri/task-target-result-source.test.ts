import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  parseTaskTargetResultSnapshot,
  type TaskTargetResultSnapshot,
} from "../../api/control-plane/task-target-results";
import { TauriTaskTargetResultSource } from "./task-target-result-source";

const TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const SNAPSHOT: TaskTargetResultSnapshot = {
  taskId: TASK_ID,
  taskStatus: "partially_succeeded",
  taskRevision: 8,
  lastEventSequence: 7,
  items: [
    {
      targetId: "16fd2706-8baf-433b-82eb-8c7fada847da",
      ordinal: 1,
      displayName: "评论成功目标",
      publicHandle: "success.target",
      resultStatus: "succeeded",
      evidence: "comment_confirmed",
      actionId: "adff54bd-3571-44da-8acd-5ea15695e5e9",
      updatedAt: "2026-07-21T08:00:00Z",
    },
    {
      targetId: "d9428888-122b-41aa-8fc2-0a5e810a4d18",
      ordinal: 2,
      displayName: "用户排除目标",
      publicHandle: null,
      resultStatus: "skipped",
      evidence: "user_excluded",
      actionId: null,
      updatedAt: "2026-07-21T08:00:01Z",
    },
  ],
};

const invoke = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

describe("Tauri target result source", () => {
  beforeEach(() => invoke.mockReset());

  it("uses one fixed native command and returns strict target facts", async () => {
    invoke.mockResolvedValue(SNAPSHOT);
    const source = new TauriTaskTargetResultSource();

    await expect(source.getResults(TASK_ID)).resolves.toEqual(SNAPSHOT);
    expect(invoke).toHaveBeenCalledWith("get_task_target_results", { taskId: TASK_ID });
  });

  it("rejects mismatched evidence, extra fields, and cross-Task responses", async () => {
    for (const invalid of [
      {
        ...SNAPSHOT,
        items: [{ ...SNAPSHOT.items[0]!, evidence: "user_excluded" }],
      },
      { ...SNAPSHOT, privatePath: "/Users/private" },
    ]) {
      expect(() => parseTaskTargetResultSnapshot(invalid)).toThrowError(
        expect.objectContaining({ code: "protocol_mismatch" }),
      );
    }

    invoke.mockResolvedValue({ ...SNAPSHOT, taskId: "fd4aa304-3d73-4fd2-af88-c2c9747c4168" });
    await expect(new TauriTaskTargetResultSource().getResults(TASK_ID)).rejects.toMatchObject({
      code: "protocol_mismatch",
      retryable: false,
    });
  });

  it("redacts native failures and never invokes after cancellation", async () => {
    invoke.mockRejectedValueOnce({ message: "credential=private", code: "private" });
    await expect(new TauriTaskTargetResultSource().getResults(TASK_ID)).rejects.toMatchObject({
      code: "transport_unavailable",
      message: "Task target results are unavailable",
      retryable: true,
    });

    invoke.mockReset();
    const controller = new AbortController();
    controller.abort();
    await expect(
      new TauriTaskTargetResultSource().getResults(TASK_ID, { signal: controller.signal }),
    ).rejects.toMatchObject({ code: "request_cancelled", retryable: false });
    expect(invoke).not.toHaveBeenCalled();
  });
});
