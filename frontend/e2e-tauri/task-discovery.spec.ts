import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface TaskDiscoverySummary {
  readonly installationId: string;
  readonly taskId: string;
  readonly commandId: string;
  readonly executionAttemptId: string;
  readonly commandStatus: string;
  readonly startedStatus: string;
  readonly startedRevision: number;
  readonly startedEventSequence: number;
  readonly finalStatus: string;
  readonly finalRevision: number;
  readonly finalEventSequence: number;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("Task discovery production-path acceptance", () => {
  it("converges candidates through the hidden real App and formal Executor", async () => {
    const heading = await browser.$("h2");
    await expect(heading).toHaveText("RPA 运营工作台");

    const summary = (await browser.tauri.execute(({ core }) =>
      core.invoke("discover_task_for_acceptance"),
    )) as TaskDiscoverySummary;

    assert.match(summary.installationId, UUID_V4);
    assert.match(summary.taskId, UUID_V4);
    assert.match(summary.commandId, UUID_V4);
    assert.match(summary.executionAttemptId, UUID_V4);
    assert.equal(summary.commandStatus, "pending");
    assert.equal(summary.startedStatus, "discovering_targets");
    assert.equal(summary.startedRevision, 2);
    assert.equal(summary.startedEventSequence, 1);
    assert.equal(summary.finalStatus, "awaiting_confirmation");
    assert.equal(summary.finalRevision, 3);
    assert.equal(summary.finalEventSequence, 2);
  });
});
