import assert from "node:assert/strict";

import { browser } from "@wdio/globals";

const phase = process.env.AUTOMATION_TOOL_U906_PHASE;
const loginName = process.env.AUTOMATION_TOOL_U906_LOGIN_NAME;
const password = process.env.AUTOMATION_TOOL_U906_PASSWORD;

async function bodyText(): Promise<string> {
  return browser.$("body").getText();
}

async function waitForText(text: string): Promise<void> {
  await browser.waitUntil(async () => (await bodyText()).includes(text), {
    timeout: 20_000,
    timeoutMsg: `U9-06 phase ${phase ?? "unknown"} did not reach its safe UI state`,
  });
}

async function invokeNative(command: string, args: Record<string, unknown>): Promise<unknown> {
  const result = (await browser.executeAsync(
    (nativeCommand, nativeArgs, done) => {
      const internal = window as typeof window & {
        __TAURI_INTERNALS__: {
          invoke(commandName: string, commandArgs: Record<string, unknown>): Promise<unknown>;
        };
      };
      void internal.__TAURI_INTERNALS__.invoke(nativeCommand, nativeArgs).then(
        (value) => done({ ok: true, value }),
        () => done({ ok: false }),
      );
    },
    command,
    args,
  )) as { readonly ok: boolean; readonly value?: unknown };
  assert.equal(result.ok, true, `U9-06 native ${command} failed`);
  return result.value;
}

async function login(): Promise<void> {
  assert.ok(loginName && password);
  const snapshot = await invokeNative("login_product_account", { loginName, password });
  assert.deepEqual(snapshot, {
    state: "authenticated",
    account: {
      userId: (snapshot as { account: { userId: string } }).account.userId,
      loginName,
      status: "active",
    },
  });
  assert.doesNotMatch(JSON.stringify(snapshot), /atas1|atrs1|atdc1/);
  await browser.refresh();
  await waitForText(loginName);
  assert.doesNotMatch(await bodyText(), /atas1|atrs1|atdc1/);
}

describe("U9-06 hidden real-Tauri account and device lifecycle", () => {
  it(`completes the isolated ${phase ?? "missing"} phase`, async () => {
    assert.ok(
      phase &&
        [
          "login",
          "restart",
          "offline",
          "session-invalid",
          "relogin",
          "disabled",
          "post-restore-login",
          "device-revoke",
        ].includes(phase),
    );

    if (["login", "relogin", "post-restore-login"].includes(phase)) {
      await waitForText("登录自动化运营工具");
      await login();
      const devices = (await invokeNative("list_product_account_devices", {})) as readonly {
        installationId: string;
        status: string;
        revision: number;
      }[];
      assert.equal(devices.length, 1);
      assert.equal(devices[0]?.status, "active");
      return;
    }
    if (phase === "restart") {
      assert.ok(loginName);
      await waitForText(loginName);
      assert.doesNotMatch(await bodyText(), /登录自动化运营工具|atas1|atrs1/);
      return;
    }
    if (phase === "offline") {
      await waitForText("暂时无法确认账号状态");
      assert.doesNotMatch(await bodyText(), /RPA 运营工作台|atas1|atrs1/);
      return;
    }
    if (phase === "session-invalid" || phase === "disabled") {
      await waitForText("登录自动化运营工具");
      assert.doesNotMatch(await bodyText(), /RPA 运营工作台|atas1|atrs1/);
      return;
    }

    assert.equal(phase, "device-revoke");
    assert.ok(loginName);
    await waitForText(loginName);
    const devices = (await invokeNative("list_product_account_devices", {})) as readonly {
      installationId: string;
      status: string;
      revision: number;
    }[];
    const current = devices.find((device) => device.status === "active");
    assert.ok(current);
    const revoked = await invokeNative("revoke_product_account_device", {
      installationId: current.installationId,
      expectedRevision: current.revision,
    });
    assert.equal((revoked as { status: string }).status, "revoked");
    await browser.refresh();
    await waitForText("当前安装实例已失效");
    assert.doesNotMatch(await bodyText(), /RPA 运营工作台|atas1|atrs1|atdc1/);
  });
});
