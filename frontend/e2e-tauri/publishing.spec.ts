import assert from "node:assert/strict";

import { browser } from "@wdio/globals";

/**
 * PB-07 production-path acceptance.
 *
 * Everything below the assertions is the real thing: the real Tauri App, its
 * real WebView, real IPC, and the real Rust publish Commands. What it does not
 * do is publish anything — no platform account exists on this machine, so the
 * chain is exercised up to the point where a real account would be required,
 * and that last step stays with PB-08.
 *
 * The one fact this can establish that no lower layer can: the availability the
 * operator sees comes from the bridge reading the session itself, not from the
 * page asserting its own readiness. Every layer below injects a double, and a
 * double reports whatever it was told to.
 *
 * Not covered here: clicking through to the page in the real App. This debug
 * build stops at the startup environment gate ("桌面运行环境需要处理") because
 * the machine has no staged executor package or embedded browser, so the
 * workbench never mounts. The navigation path is covered by the Playwright UI
 * Harness instead; enabling it here needs a prepared startup environment.
 */

interface PublishPlatformState {
  readonly platform: string;
  readonly availability: string;
}

interface PublishWorkspaceSnapshot {
  readonly platforms: readonly PublishPlatformState[];
  readonly stage: string;
  readonly target: string | null;
  readonly approval: unknown;
  readonly outcome: string | null;
  readonly retryable: boolean;
  readonly audit: readonly unknown[];
}

const MECHANISM_WORDS = [
  "browser_use",
  "browseruse",
  "playwright",
  "chromium",
  "official_api",
  "officialapi",
];

describe("Publishing production-path acceptance", () => {
  it("reads platform availability from the real bridge, not from the page", async () => {
    const snapshot = (await browser.tauri.execute(({ core }) =>
      core.invoke("get_publish_workspace"),
    )) as PublishWorkspaceSnapshot;

    assert.deepEqual(
      snapshot.platforms.map((entry) => entry.platform),
      ["bilibili", "douyin"],
    );
    // Nothing is configured or signed in on this machine, and the bridge says
    // so rather than reporting a platform the App could not actually use.
    assert.equal(
      snapshot.platforms.find((entry) => entry.platform === "bilibili")?.availability,
      "awaiting_configuration",
    );
    assert.equal(
      snapshot.platforms.find((entry) => entry.platform === "douyin")?.availability,
      "awaiting_sign_in",
    );
    assert.equal(snapshot.stage, "idle");
    assert.equal(snapshot.target, null);
    assert.equal(snapshot.approval, null);
    assert.equal(snapshot.outcome, null);
    assert.equal(snapshot.retryable, false);
    assert.deepEqual(snapshot.audit, []);
  });

  it("refuses to start a publish on a platform that is not usable", async () => {
    const rejection = (await browser.tauri.execute(({ core }) =>
      core
        .invoke("begin_publish", {
          platform: "douyin",
          publishJobId: "423e4567-e89b-42d3-a456-426614174001",
          artifactPath: "/videos/acceptance-clip.mp4",
          videoSummary: "验收样片 · 1.2 MB",
          title: "验收标题",
          description: "验收简介",
        })
        .then(() => null)
        .catch((error: unknown) => error),
    )) as { code?: string } | null;

    assert.ok(rejection !== null, "an unusable platform must not start a publish");
    assert.equal(rejection.code, "publish_not_available");
  });

  it("refuses a platform outside the two the product supports", async () => {
    const rejection = (await browser.tauri.execute(({ core }) =>
      core
        .invoke("begin_publish", {
          platform: "kuaishou",
          publishJobId: "423e4567-e89b-42d3-a456-426614174002",
          artifactPath: "/videos/acceptance-clip.mp4",
          videoSummary: "验收样片 · 1.2 MB",
          title: "验收标题",
          description: "验收简介",
        })
        .then(() => null)
        .catch((error: unknown) => error),
    )) as { code?: string } | null;

    assert.ok(rejection !== null, "an unsupported platform must be refused");
    assert.equal(rejection.code, "configuration_invalid");
  });

  it("refuses an approval nobody is waiting on", async () => {
    const rejection = (await browser.tauri.execute(({ core }) =>
      core
        .invoke("approve_publish", {
          publishJobId: "423e4567-e89b-42d3-a456-426614174001",
          confirmationId: "123e4567-e89b-42d3-a456-426614174007",
        })
        .then(() => null)
        .catch((error: unknown) => error),
    )) as { code?: string } | null;

    assert.ok(rejection !== null, "an approval with nothing pending must be refused");
    assert.equal(rejection.code, "publish_nothing_to_confirm");
  });

  it("never tells the operator how a platform is reached", async () => {
    const snapshot = JSON.stringify(
      await browser.tauri.execute(({ core }) => core.invoke("get_publish_workspace")),
    ).toLowerCase();

    for (const word of MECHANISM_WORDS) {
      assert.ok(!snapshot.includes(word), `the bridge projection leaked ${word}`);
    }
  });
});
