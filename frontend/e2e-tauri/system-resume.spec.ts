import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { writeFile } from "node:fs/promises";

import { browser, expect } from "@wdio/globals";

interface Preparation {
  readonly installationId: string;
}

interface ExecutorStatus {
  readonly restartCount: number;
  readonly state: string;
}

interface ExecutorDiagnosticsSnapshot {
  readonly lines: string[];
}

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (value === undefined || value.length === 0) throw new Error(`Missing H8-08 value: ${name}`);
  return value;
}

async function signal(path: string, value: unknown = {}): Promise<void> {
  await writeFile(path, `${JSON.stringify(value)}\n`, {
    encoding: "utf8",
    mode: 0o600,
    flag: "wx",
  });
}

async function waitForSignal(path: string, label: string): Promise<void> {
  await browser.waitUntil(() => existsSync(path), {
    timeout: 120_000,
    interval: 100,
    timeoutMsg: `H8-08 runner did not publish ${label}`,
  });
}

describe("H8-08 hidden App system-resume acceptance", () => {
  it("keeps one Executor and exposes fixed recovery diagnostics through App IPC", async () => {
    await expect(await browser.$("h2")).toHaveText("RPA 运营工作台");
    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_system_resume_for_acceptance"),
    )) as Preparation;
    assert.match(preparation.installationId, UUID_V4);
    const started = (await browser.tauri.execute(({ core }) =>
      core.invoke("restart_executor"),
    )) as ExecutorStatus;
    assert.equal(started.state, "running");
    assert.equal(started.restartCount, 0);
    const appProcessId = (await browser.tauri.execute(({ core }) =>
      core.invoke("app_process_id_for_acceptance"),
    )) as number;
    assert.equal(Number.isSafeInteger(appProcessId) && appProcessId > 1, true);
    await signal(requiredEnvironment("AUTOMATION_TOOL_H808_EXECUTOR_READY_SIGNAL"), {
      appProcessId,
      installationId: preparation.installationId,
    });

    await waitForSignal(
      requiredEnvironment("AUTOMATION_TOOL_H808_EXECUTOR_RESUMED_SIGNAL"),
      "the resumed Executor",
    );
    let diagnostics: string[] = [];
    await browser.waitUntil(
      async () => {
        const snapshot = (await browser.tauri.execute(({ core }) =>
          core.invoke("get_executor_diagnostics"),
        )) as ExecutorDiagnosticsSnapshot;
        assert.equal(Array.isArray(snapshot.lines), true);
        diagnostics = snapshot.lines;
        return (
          diagnostics.includes("executor.recovery system_suspension_detected") &&
          diagnostics.includes("executor.recovery transport_recovered")
        );
      },
      {
        timeout: 60_000,
        interval: 250,
        timeoutMsg: `H8-08 fixed diagnostics did not recover: ${diagnostics.join(" | ")}`,
      },
    );
    const recovered = (await browser.tauri.execute(({ core }) =>
      core.invoke("get_executor_status"),
    )) as ExecutorStatus;
    assert.equal(recovered.state, "running");
    assert.equal(recovered.restartCount, 0);
    assert.equal(diagnostics.every((line) => !line.includes("/Users/") && !line.includes("token")), true);
    await signal(requiredEnvironment("AUTOMATION_TOOL_H808_DIAGNOSTICS_OBSERVED_SIGNAL"), {
      installationId: preparation.installationId,
      restartCount: recovered.restartCount,
    });
    await waitForSignal(
      requiredEnvironment("AUTOMATION_TOOL_H808_FACTS_VERIFIED_SIGNAL"),
      "the exact resumed process and ledger facts",
    );
  });
});
