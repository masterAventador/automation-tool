import type { Page } from "@playwright/test";

export async function openSidebarDestination(page: Page, name: string): Promise<void> {
  await page.getByRole("menuitem", { name }).click();
}

export async function openAutomationRuns(page: Page): Promise<void> {
  await openSidebarDestination(page, "自动化");
  await page.getByRole("button", { name: "查看运行记录" }).click();
}

export async function openTaskCreate(page: Page): Promise<void> {
  await openSidebarDestination(page, "自动化");
  await page.getByRole("button", { name: /新建运营任务/ }).click();
}

export async function openVideoStudio(page: Page): Promise<void> {
  await openSidebarDestination(page, "创作");
  await page
    .locator(".ant-segmented-item")
    .filter({ hasText: "智能素材成片" })
    .click();
  await page.getByRole("button", { name: "打开完整制作面板" }).click();
}

export async function openVideoEditing(page: Page): Promise<void> {
  await openSidebarDestination(page, "创作");
  await page.locator(".ant-segmented-item").filter({ hasText: "轻量剪辑" }).click();
}

export async function openPublishingWorkspace(page: Page): Promise<void> {
  await openSidebarDestination(page, "发布");
  await page.getByRole("button", { name: /新建发布/ }).click();
}
