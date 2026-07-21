import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface TaskTerminationSummary {
  readonly installationId: string;
  readonly cancelTaskId: string;
  readonly cancelCommandType: string;
  readonly cancelCommandStatus: string;
  readonly cancelSequence: number;
  readonly cancelEventType: string;
  readonly cancelFinalStatus: string;
  readonly cancelFinalRevision: number;
  readonly emergencyTaskId: string;
  readonly emergencyCommandType: string;
  readonly emergencyCommandStatus: string;
  readonly emergencySequence: number;
  readonly emergencyEventType: string;
  readonly emergencyFinalStatus: string;
  readonly emergencyFinalRevision: number;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const CANCEL_OUTCOME_UNCERTAIN =
  process.env.AUTOMATION_TOOL_H802_CANCEL_OUTCOME_UNCERTAIN === "1";
const CONFIRMED_TASK_REVISION =
  process.env.AUTOMATION_TOOL_TASK_TERMINATION_CONFIRMED_REVISION === "1";

describe("Task termination production-path acceptance", () => {
  it("cancels and emergency-stops through the hidden real App and formal Executor", async () => {
    const heading = await browser.$("h2");
    await expect(heading).toHaveText("RPA 运营工作台");

    const summary = (await browser.tauri.execute(({ core }) =>
      core.invoke("terminate_tasks_for_acceptance"),
    )) as TaskTerminationSummary;

    assert.match(summary.installationId, UUID_V4);
    assert.match(summary.cancelTaskId, UUID_V4);
    assert.match(summary.emergencyTaskId, UUID_V4);
    assert.notEqual(summary.cancelTaskId, summary.emergencyTaskId);
    assert.equal(summary.cancelCommandType, "task.cancel");
    assert.equal(summary.cancelCommandStatus, "pending");
    assert.equal(summary.cancelSequence, 2);
    assert.equal(
      summary.cancelEventType,
      CANCEL_OUTCOME_UNCERTAIN ? "task.outcome_uncertain" : "task.cancelled",
    );
    assert.equal(
      summary.cancelFinalStatus,
      CANCEL_OUTCOME_UNCERTAIN ? "outcome_uncertain" : "cancelled",
    );
    assert.equal(summary.cancelFinalRevision, CONFIRMED_TASK_REVISION ? 6 : 5);
    assert.equal(summary.emergencyCommandType, "task.emergency_stop");
    assert.equal(summary.emergencyCommandStatus, "pending");
    assert.equal(summary.emergencySequence, 2);
    assert.equal(summary.emergencyEventType, "task.outcome_uncertain");
    assert.equal(summary.emergencyFinalStatus, "outcome_uncertain");
    assert.equal(summary.emergencyFinalRevision, CONFIRMED_TASK_REVISION ? 6 : 5);
  });
});
