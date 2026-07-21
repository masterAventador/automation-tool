import { describe, expect, it } from "vitest";

import {
  TaskDiscoveryGatewayError,
  parseTaskDiscoveryReceipt,
  validateTaskDiscoveryInput,
} from "./task-discovery";

const TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const RECEIPT = {
  taskId: TASK_ID,
  taskStatus: "discovering_targets",
  taskRevision: 2,
  lastEventSequence: 1,
  commandId: "16fd2706-8baf-433b-82eb-8c7fada847da",
  executionAttemptId: "adff54bd-3571-44da-8acd-5ea15695e5e9",
  commandStatus: "pending",
};

describe("Task discovery contract", () => {
  it("accepts one exact receipt bound to the requested Task", () => {
    expect(parseTaskDiscoveryReceipt(RECEIPT, TASK_ID)).toEqual(RECEIPT);
    expect(() => validateTaskDiscoveryInput(TASK_ID, "task:discover:start:safe-key")).not.toThrow();
  });

  it("rejects malformed, cross-Task, and private fields without disclosure", () => {
    for (const invoke of [
      () => validateTaskDiscoveryInput("private-task", "task:discover:start:safe"),
      () => validateTaskDiscoveryInput(TASK_ID, "contains space"),
      () => parseTaskDiscoveryReceipt({ ...RECEIPT, taskId: "private-task" }, TASK_ID),
      () => parseTaskDiscoveryReceipt({ ...RECEIPT, privateField: "secret=value" }, TASK_ID),
    ]) {
      expect(invoke).toThrow(TaskDiscoveryGatewayError);
      try {
        invoke();
      } catch (error) {
        expect(String(error)).not.toMatch(/private-task|secret=value/u);
      }
    }
  });
});
