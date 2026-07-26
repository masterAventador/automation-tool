import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface TaskProjectionSummary {
  readonly installationId: string;
  readonly taskId: string;
  readonly phases: readonly string[];
  readonly eventSequences: readonly number[];
  readonly finalStatus: string;
  readonly finalRevision: number;
  readonly finalLastEventSequence: number;
}

interface ProjectionExecutionResult {
  readonly ok: boolean;
  readonly summary?: TaskProjectionSummary;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("Task projection production-path acceptance", () => {
  it("loads a snapshot then reaches terminal through the hidden App Tauri Channel", async () => {
    const heading = await browser.$("h2");
    await expect(heading).toHaveText("RPA 运营工作台");

    const result = (await browser.executeAsync((done) => {
      const runtime = globalThis as unknown as {
        __automationToolTaskProjectionAcceptance?: () => Promise<TaskProjectionSummary>;
      };
      const run = runtime.__automationToolTaskProjectionAcceptance;
      if (run === undefined) {
        done({ ok: false });
        return;
      }
      run().then(
        (summary) => done({ ok: true, summary }),
        () => done({ ok: false }),
      );
    })) as ProjectionExecutionResult;

    assert.equal(result.ok, true);
    const summary = result.summary;
    assert.ok(summary);
    assert.match(summary.installationId, UUID_V4);
    assert.match(summary.taskId, UUID_V4);
    assert.deepEqual(summary.eventSequences, [1, 2, 3, 4, 5]);
    assert.equal(summary.finalStatus, "succeeded");
    // Confirming the Task's targets establishes revision 2 before the five
    // executor events; the production offer guard refuses to deliver the offer
    // without that confirmation, so the terminal revision cannot be lower.
    assert.equal(summary.finalRevision, 7);
    assert.equal(summary.finalLastEventSequence, 5);
    assert.equal(summary.phases[0], "live");
    assert.equal(summary.phases[summary.phases.length - 1], "terminal");
    assert.ok(!summary.phases.includes("degraded"));
  });
});
