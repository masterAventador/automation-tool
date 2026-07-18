import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { TaskCreationGatewayError } from "../../features/task-create/task-creation-gateway";
import { TauriTaskCreationGateway } from "./task-creation-gateway";

const definition = {
  template: "douyin.search_exposure.v1" as const,
  searchKeyword: "新能源汽车",
  action: "browse" as const,
  messageTemplate: null,
  targetLimit: 10,
  minimumIntervalSeconds: 30,
  maximumIntervalSeconds: 90,
  previewRequired: true as const,
  finalConfirmationRequired: true as const,
};

describe("Tauri Task creation gateway", () => {
  beforeEach(() => invoke.mockReset());

  it("invokes only the fixed Douyin creation Command with a validated definition", async () => {
    invoke.mockResolvedValue({
      taskId: "0f8fad5b-d9cb-469f-a165-70867728950e",
      status: "draft",
      revision: 1,
      lastEventSequence: 0,
      createdAt: "2026-07-18T15:30:00Z",
      updatedAt: "2026-07-18T15:30:00Z",
    });
    const gateway = new TauriTaskCreationGateway();

    await gateway.createDouyinSearchExposureTask(
      definition,
      "task:create:douyin-search:16fd2706-8baf-433b-82eb-8c7fada847da",
    );

    expect(invoke).toHaveBeenCalledWith("create_douyin_search_exposure_task", {
      definition,
      idempotencyKey: "task:create:douyin-search:16fd2706-8baf-433b-82eb-8c7fada847da",
    });
  });

  it("rejects invalid cross-runtime fields before invoke and never reflects secrets", async () => {
    const gateway = new TauriTaskCreationGateway();
    await expect(
      gateway.createDouyinSearchExposureTask(
        { ...definition, searchKeyword: " password=private-value" },
        "task:create:douyin-search:16fd2706-8baf-433b-82eb-8c7fada847da",
      ),
    ).rejects.toBeInstanceOf(TaskCreationGatewayError);
    expect(invoke).not.toHaveBeenCalled();

    invoke.mockRejectedValueOnce(new Error("Bearer private-native-value"));
    const error = await gateway
      .createDouyinSearchExposureTask(
        definition,
        "task:create:douyin-search:16fd2706-8baf-433b-82eb-8c7fada847da",
      )
      .catch((value: unknown) => value);
    expect(error).toMatchObject({
      code: "transport_unavailable",
      message: "Task creation service is unavailable",
    });
    expect(String(error)).not.toContain("private-native-value");
  });
});
