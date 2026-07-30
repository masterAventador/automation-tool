import assert from "node:assert/strict";

import { browser } from "@wdio/globals";
import {
  waitForStartup,
} from "./navigation";

interface TaskEventStreamSummary {
  readonly installationId: string;
  readonly taskId: string;
  readonly initialSequences: readonly number[];
  readonly resumedSequences: readonly number[];
  readonly terminal: boolean;
  readonly progressPercent: number | null;
}

describe("Task event stream production-path acceptance", () => {
  it("disconnects, resumes, and reaches terminal through the hidden real App", async () => {
    await waitForStartup();

    const summary = (await browser.tauri.execute(({ core }) =>
      core.invoke("stream_task_events_for_acceptance"),
    )) as TaskEventStreamSummary;

    assert.deepEqual(Object.keys(summary).sort(), [
      "initialSequences",
      "installationId",
      "progressPercent",
      "resumedSequences",
      "taskId",
      "terminal",
    ]);
    assert.match(
      summary.installationId,
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    assert.match(
      summary.taskId,
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    assert.deepEqual(summary.initialSequences, [1, 2]);
    assert.deepEqual(summary.resumedSequences, [3, 4, 5]);
    assert.equal(summary.terminal, true);
    assert.equal(summary.progressPercent, 50);
  });
});
