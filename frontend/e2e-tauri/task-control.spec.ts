import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface TaskControlSummary {
  readonly installationId: string;
  readonly taskId: string;
  readonly pauseCommandType: string;
  readonly pauseCommandStatus: string;
  readonly pauseSequence: number;
  readonly pausedEventType: string;
  readonly resumeCommandType: string;
  readonly resumeCommandStatus: string;
  readonly resumeSequence: number;
  readonly resumedEventType: string;
  readonly finalStatus: string;
  readonly finalRevision: number;
}

describe("Task control production-path acceptance", () => {
  it("pauses and resumes through the hidden real App and formal Executor", async () => {
    const heading = await browser.$("h2");
    await expect(heading).toHaveText("RPA 运营工作台");

    const summary = (await browser.tauri.execute(({ core }) =>
      core.invoke("control_task_for_acceptance"),
    )) as TaskControlSummary;

    assert.match(
      summary.installationId,
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    assert.match(
      summary.taskId,
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    assert.equal(summary.pauseCommandType, "task.pause");
    assert.equal(summary.pauseCommandStatus, "pending");
    assert.equal(summary.pauseSequence, 2);
    assert.equal(summary.pausedEventType, "task.paused");
    assert.equal(summary.resumeCommandType, "task.resume");
    assert.equal(summary.resumeCommandStatus, "pending");
    assert.equal(summary.resumeSequence, 3);
    assert.equal(summary.resumedEventType, "task.resumed");
    assert.equal(summary.finalStatus, "running");
    // Target confirmation establishes revision 2 before the four executor events.
    assert.equal(summary.finalRevision, 6);
  });
});
