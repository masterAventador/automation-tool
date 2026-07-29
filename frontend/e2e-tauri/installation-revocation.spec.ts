import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";
import {
  waitForStartup,
} from "./navigation";

interface RegistrationSummary {
  readonly installationId: string;
  readonly revision: number;
}

describe("Installation revocation production-path acceptance", () => {
  it("moves the hidden real App from the workbench to the revoked diagnostic", async () => {
    await waitForStartup();

    const registration = (await browser.tauri.execute(({ core }) =>
      core.invoke("register_installation_for_revocation_acceptance"),
    )) as RegistrationSummary;
    assert.deepEqual(Object.keys(registration).sort(), ["installationId", "revision"]);
    assert.match(
      registration.installationId,
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    assert.equal(registration.revision, 1);

    await browser.waitUntil(
      async () => {
        await browser.refresh();
        const heading = await browser.$("h2");
        return (await heading.isExisting()) && (await heading.getText()) === "当前安装实例已失效";
      },
      {
        timeout: 60_000,
        interval: 500,
        timeoutMsg: "The hidden App did not enter the revoked Installation diagnostic",
      },
    );

    const revokedHeading = await browser.$("h2");
    await expect(revokedHeading).toHaveText("当前安装实例已失效");
    const body = await browser.$("body");
    await expect(body).toHaveText(
      expect.stringContaining("安装实例授权不可用"),
    );
    await expect(body).not.toHaveText(expect.stringContaining("账号登录"));
  });
});
