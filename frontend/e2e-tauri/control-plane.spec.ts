import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

interface AcceptanceSummary {
  readonly healthAvailable: boolean;
  readonly installationId: string;
  readonly initialVersion: number;
  readonly firstCapability: string;
  readonly rotatedVersion: number;
  readonly secondCapability: string;
  readonly revokedVersion: number;
  readonly appSecretRemoved: boolean;
}

describe("Control Plane production-path acceptance", () => {
  it("runs the full lifecycle from the real no-login Tauri workbench", async () => {
    const heading = await browser.$("h2");
    await expect(heading).toHaveText("RPA 运营工作台");

    const summary = (await browser.tauri.execute(({ core }) =>
      core.invoke("run_control_plane_acceptance"),
    )) as AcceptanceSummary;

    assert.deepEqual(Object.keys(summary).sort(), [
      "appSecretRemoved",
      "firstCapability",
      "healthAvailable",
      "initialVersion",
      "installationId",
      "revokedVersion",
      "rotatedVersion",
      "secondCapability",
    ]);
    assert.equal(summary.healthAvailable, true);
    assert.match(
      summary.installationId,
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    assert.equal(summary.initialVersion, 1);
    assert.equal(summary.firstCapability, "app.control-plane");
    assert.equal(summary.rotatedVersion, 2);
    assert.equal(summary.secondCapability, "executor.connect");
    assert.equal(summary.revokedVersion, 2);
    assert.equal(summary.appSecretRemoved, true);
  });
});
