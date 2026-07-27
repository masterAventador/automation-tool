import { expect, test, type Page } from "@playwright/test";
import { openAutomationRuns, openTaskCreate } from "./navigation";

/**
 * What the workbench home says, walked the way the customer walks it.
 *
 * The viewport is 1280x800, the size the production Tauri window opens at —
 * `src-tauri/tauri.conf.json` declares it and `playwright.config.ts` reads it
 * from there, so "on the first screen" here means the same thing it means on
 * the customer's machine. (It said "Playwright's default 1280x800" until T96;
 * Playwright's default is 720, which is what this ran at. The assertions below
 * are on rendered text rather than on geometry, so none of them moved.)
 *
 * The 2026-07-26 sweep found the home page leading with the two things a person
 * cannot use: the largest card's body was `Task ID` / `Revision` / `事件水位`,
 * and every row of 最近任务 was labelled with a bare 36-character UUID.
 *
 * Revision and 事件水位 are not junk — they are how an operator checks that the
 * authoritative snapshot and the event projection agree, which is the whole
 * claim of `CLAUDE.md` 4.4. So they are folded into 诊断信息 rather than deleted,
 * and this file asserts both halves: off the first screen, still one click away.
 *
 * The readable name comes from `createdAt` because that is the only human-
 * readable fact the projection carries; `taskSnapshotSchema` is `.strict()` and
 * lists exactly taskId, status, revision, lastEventSequence, createdAt,
 * updatedAt.
 */

const HARNESS = "/harness.html?health=available&scenario=task-lifecycle";

/** A canonical UUID anywhere in a string — what the rows used to be. */
const ANY_UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/;

/** `07-26 21:33:07 的任务` — month-day, time, and what it is. */
const READABLE_TASK_NAME = /^\d{2}-\d{2} \d{2}:\d{2}:\d{2} 的任务$/;

/**
 * Create one Task through the form, the way an operator does.
 *
 * The lifecycle Harness leaves the first Task running and finishes every one
 * after it, so two calls give the home page both a current Task and a finished
 * one to list.
 */
async function createTask(page: Page, keyword: string): Promise<void> {
  await openTaskCreate(page);
  await page.getByLabel("搜索关键词").fill(keyword);
  await page.getByRole("button", { name: "创建任务" }).click();
  await expect(page.getByText(/任务已创建：[0-9a-f-]{36}/)).toBeVisible();
}

async function openWorkbenchWithTwoTasks(page: Page): Promise<void> {
  await page.goto(HARNESS);
  await createTask(page, "T93 运行中的任务");
  await createTask(page, "T93 已完成的任务");
  await openAutomationRuns(page);
  await expect(page.getByRole("heading", { name: "当前任务" })).toBeVisible();
}

test("the workbench home leads with facts a person can read", async ({ page }) => {
  await openWorkbenchWithTwoTasks(page);

  const shown = (await page.locator(".workbench-content").textContent()) ?? "";

  expect(shown, "工作台首页仍然把内部协议计数器当正文").not.toContain("事件水位");
  expect(shown, "工作台首页仍然把内部协议计数器当正文").not.toContain("Revision");
  expect(shown.match(ANY_UUID)?.[0], "工作台首页仍然直接显示任务 UUID").toBeUndefined();
});

test("每一行最近任务都说得出自己是什么时候的", async ({ page }) => {
  await openWorkbenchWithTwoTasks(page);

  const rows = await page.locator(".recent-task-list button").allTextContents();

  expect(rows.length).toBeGreaterThanOrEqual(2);
  for (const row of rows) {
    expect(row, `最近任务的这一行读不出是什么任务：${row}`).toMatch(READABLE_TASK_NAME);
  }
});

test("诊断信息 still holds the counters an operator needs", async ({ page }) => {
  await openWorkbenchWithTwoTasks(page);

  // Exact, because the name is now exactly the label. It used to be
  // "<折叠状态> 诊断信息" — antd labels its expand icon "collapsed"/"expanded"
  // and a name computed from content swallowed it — and this call site matched
  // loosely to work around that. T99 supplied an `aria-hidden` arrow instead
  // (`components/collapse-expand-icon`), so the workaround is gone and the
  // assertion is now strong enough to catch the pollution coming back.
  await page.getByRole("button", { name: "诊断信息", exact: true }).click();

  const diagnostics = page.locator(".current-task-card");
  await expect(diagnostics.getByText("Revision")).toBeVisible();
  await expect(diagnostics.getByText("事件水位")).toBeVisible();
  await expect(diagnostics.getByText(ANY_UUID)).toBeVisible();
});
