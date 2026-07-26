import type { Page } from "@playwright/test";

/**
 * Fill the title and description a publish needs before it can start.
 *
 * The publish button is disabled until both are written — `PublishWorkspace`
 * gates it on `publishable`, and `ui-harness.spec.ts` asserts that disabled
 * state on purpose: both the platform and the executor refuse unreadable copy,
 * and finding that out after the visible operations browser has been opened
 * wastes the only one there is.
 *
 * This lives here rather than inside one spec because a second spec now walks
 * the same path. On 2026-07-26 `upstream-name-leak.spec.ts` clicked publish
 * without writing anything and spent thirty seconds waiting for a button the
 * product was correctly keeping disabled.
 */
export async function writeThePublishCopy(page: Page): Promise<void> {
  await page.getByLabel("标题").fill("三分钟讲清油皮护肤");
  await page.getByLabel("简介").fill("从洁面到防晒，按顺序讲一遍。");
}
