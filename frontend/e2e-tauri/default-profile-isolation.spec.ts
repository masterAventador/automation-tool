import assert from "node:assert/strict";
import { access, writeFile } from "node:fs/promises";
import { isAbsolute } from "node:path";

import { browser, expect } from "@wdio/globals";

interface Preparation {
  readonly installationId: string;
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SCAN_FACT = "请在打开的运营浏览器中扫码登录。";

function handshakePath(name: "AUTOMATION_TOOL_B516_READY_FILE" | "AUTOMATION_TOOL_B516_RELEASE_FILE") {
  const value = process.env[name];
  assert.ok(value && isAbsolute(value), `${name} is unavailable`);
  return value;
}

async function fileExists(path: string): Promise<boolean> {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

describe("B5-16 default browser Profile isolation", () => {
  it("keeps the App-owned headless browser alive for an external process and file audit", async () => {
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_platform_session_reuse_for_acceptance"),
    )) as Preparation;
    assert.match(preparation.installationId, UUID_V4);
    await browser.tauri.execute(({ core }) =>
      core.invoke("select_browser", { browser: "google_chrome" }),
    );

    await browser.$("li=平台状态").click();
    await expect(await browser.$("h2")).toHaveText("平台状态");
    await browser.$("button=打开登录处理").click();
    await browser.waitUntil(
      async () => (await browser.$("body").getText()).includes(SCAN_FACT),
      { timeout: 120_000, timeoutMsg: "B5-16 scan handoff did not converge" },
    );

    const ready = handshakePath("AUTOMATION_TOOL_B516_READY_FILE");
    const release = handshakePath("AUTOMATION_TOOL_B516_RELEASE_FILE");
    await writeFile(ready, "ready", { encoding: "ascii", flag: "wx", mode: 0o600 });
    await browser.waitUntil(() => fileExists(release), {
      timeout: 180_000,
      interval: 100,
      timeoutMsg: "B5-16 runtime audit did not release the hidden App",
    });

    await browser.tauri.execute(({ core }) => core.invoke("exit_app_for_acceptance"));
  });
});
