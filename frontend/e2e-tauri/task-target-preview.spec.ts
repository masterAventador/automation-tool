import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface PreviewSummary {
  readonly installationId: string;
  readonly taskId: string;
  readonly pageRevision: number;
  readonly initialTaskRevision: number;
  readonly excludedTaskRevision: number;
  readonly confirmedTaskRevision: number;
  readonly selectedTargetCount: number;
  readonly userExcludedTargetCount: number;
  readonly confirmed: boolean;
  readonly finalStatus: string;
  readonly replayRevision: number;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("Task target preview production-path acceptance", () => {
  it("lists, excludes, confirms, and replays through the hidden real App", async () => {
    const heading = await browser.$("h2");
    await expect(heading).toHaveText("RPA 运营工作台");

    const summary = (await browser.tauri.execute(({ core }) =>
      core.invoke("preview_task_for_acceptance"),
    )) as PreviewSummary;

    assert.match(summary.installationId, UUID_V4);
    assert.match(summary.taskId, UUID_V4);
    assert.equal(summary.pageRevision, 1);
    assert.equal(summary.excludedTaskRevision, summary.initialTaskRevision + 1);
    assert.equal(summary.confirmedTaskRevision, summary.excludedTaskRevision + 1);
    assert.equal(summary.selectedTargetCount, 1);
    assert.equal(summary.userExcludedTargetCount, 1);
    assert.equal(summary.confirmed, true);
    assert.equal(summary.finalStatus, "queued");
    assert.equal(summary.replayRevision, summary.confirmedTaskRevision);
  });
});
