import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface TaskQuerySummary {
  readonly installationId: string;
  readonly firstPageCount: number;
  readonly secondPageCount: number;
  readonly detailMatched: boolean;
  readonly foreignHidden: boolean;
  readonly cursorOpaque: boolean;
}

describe("Task query production-path acceptance", () => {
  it("pages, reads detail, and hides foreign Tasks from the hidden real App", async () => {
    const heading = await browser.$("h2");
    await expect(heading).toHaveText("RPA 运营工作台");

    const summary = (await browser.tauri.execute(({ core }) =>
      core.invoke("query_tasks_for_acceptance"),
    )) as TaskQuerySummary;

    assert.deepEqual(Object.keys(summary).sort(), [
      "cursorOpaque",
      "detailMatched",
      "firstPageCount",
      "foreignHidden",
      "installationId",
      "secondPageCount",
    ]);
    assert.match(
      summary.installationId,
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    assert.equal(summary.firstPageCount, 2);
    assert.equal(summary.secondPageCount, 1);
    assert.equal(summary.detailMatched, true);
    assert.equal(summary.foreignHidden, true);
    assert.equal(summary.cursorOpaque, true);
  });
});
