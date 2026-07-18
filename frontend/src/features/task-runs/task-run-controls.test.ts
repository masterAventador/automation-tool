import { describe, expect, it } from "vitest";

import {
  TaskRunControlGatewayError,
  parseTaskRunControlReceipt,
  validateTaskRunControlInput,
} from "./task-run-controls";

const TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const RECEIPT = {
  commandId: "16fd2706-8baf-433b-82eb-8c7fada847da",
  taskId: TASK_ID,
  executionAttemptId: "adff54bd-3571-44da-8acd-5ea15695e5e9",
  sequence: 2,
  commandType: "task.pause",
  status: "pending",
};

describe("Task run control contract", () => {
  it("accepts one exact public receipt bound to the requested operation", () => {
    expect(parseTaskRunControlReceipt(RECEIPT, TASK_ID, "pause")).toEqual(RECEIPT);
  });

  it("rejects malformed input and cross-operation receipts without disclosure", () => {
    for (const operation of ["pause", "resume", "cancel", "emergency_stop"] as const) {
      expect(() =>
        validateTaskRunControlInput(TASK_ID, `task-run:${operation}:safe-key`, operation),
      ).not.toThrow();
    }

    for (const invoke of [
      () => validateTaskRunControlInput("private-task", "task-run:pause:safe", "pause"),
      () => validateTaskRunControlInput(TASK_ID, "contains space", "pause"),
      () => parseTaskRunControlReceipt({ ...RECEIPT, commandType: "task.resume" }, TASK_ID, "pause"),
      () => parseTaskRunControlReceipt({ ...RECEIPT, privateField: "secret=value" }, TASK_ID, "pause"),
    ]) {
      expect(invoke).toThrow(TaskRunControlGatewayError);
      try {
        invoke();
      } catch (error) {
        expect(String(error)).not.toMatch(/private-task|secret=value/);
      }
    }
  });
});
