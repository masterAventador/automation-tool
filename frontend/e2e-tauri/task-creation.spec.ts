import assert from "node:assert/strict";

import { browser } from "@wdio/globals";
import {
  waitForStartup,
} from "./navigation";

interface TaskCreationSummary {
  readonly installationId: string;
  readonly taskId: string;
  readonly status: string;
  readonly revision: number;
  readonly replayed: boolean;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("Task creation production-path acceptance", () => {
  it("creates and replays one Task from the hidden real Tauri App", async () => {
    await waitForStartup();

    const summary = (await browser.tauri.execute(({ core }) =>
      core.invoke("create_task_for_acceptance"),
    )) as TaskCreationSummary;

    assert.deepEqual(Object.keys(summary).sort(), [
      "installationId",
      "replayed",
      "revision",
      "status",
      "taskId",
    ]);
    assert.match(summary.installationId, UUID_V4);
    assert.match(summary.taskId, UUID_V4);
    assert.equal(summary.status, "draft");
    assert.equal(summary.revision, 1);
    assert.equal(summary.replayed, true);
  });
});
