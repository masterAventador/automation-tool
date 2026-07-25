import { expect, test, type Page } from "@playwright/test";

function failOnConsoleErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      errors.push(message.text());
    }
  });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

test("available Harness opens the no-login RPA workbench", async ({ page }) => {
  const consoleErrors = failOnConsoleErrors(page);

  await page.goto("/harness.html?health=available");

  await expect(page.locator("html")).toHaveAttribute(
    "data-runtime",
    "automation-tool-test-harness",
  );
  await expect(page.getByRole("heading", { name: "RPA 运营工作台" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "桌面主导航" })).toBeVisible();
  await expect(page.getByRole("button", { name: /登录|注册/ })).toHaveCount(0);
  expect(consoleErrors).toEqual([]);
});

test("unavailable Harness shows safe diagnostics instead of login", async ({ page }) => {
  const consoleErrors = failOnConsoleErrors(page);

  await page.goto("/harness.html?health=unavailable");

  await expect(page.getByRole("heading", { name: "暂时无法连接业务服务" })).toBeVisible();
  await expect(page.getByText("控制服务不可用")).toBeVisible();
  await expect(page.getByRole("button", { name: "重新检查" })).toBeVisible();
  await expect(page.getByRole("button", { name: /登录|注册/ })).toHaveCount(0);
  expect(consoleErrors).toEqual([]);
});

test("flaky Harness recovers through the real retry interaction", async ({ page }) => {
  const consoleErrors = failOnConsoleErrors(page);

  await page.goto("/harness.html?health=flaky");
  await expect(page.getByRole("heading", { name: "暂时无法连接业务服务" })).toBeVisible();

  await page.getByRole("button", { name: "重新检查" }).click();

  await expect(page.getByRole("heading", { name: "RPA 运营工作台" })).toBeVisible();
  expect(consoleErrors).toEqual([]);
});

test("revoked Harness shows the distinct Installation diagnostic", async ({ page }) => {
  const consoleErrors = failOnConsoleErrors(page);

  await page.goto("/harness.html?health=revoked");

  await expect(page.getByRole("heading", { name: "当前安装实例已失效" })).toBeVisible();
  await expect(page.getByText("安装实例授权不可用")).toBeVisible();
  await expect(page.getByRole("button", { name: /登录|注册/ })).toHaveCount(0);
  expect(consoleErrors).toEqual([]);
});

test("Task lifecycle Harness covers control, success, and refresh recovery", async ({
  page,
}) => {
  const consoleErrors = failOnConsoleErrors(page);
  await page.goto("/harness.html?health=available&scenario=task-lifecycle");

  await page.getByRole("menuitem", { name: "新建任务" }).click();
  await page.getByLabel("搜索关键词").fill("T3-19 取消链路");
  await page.getByLabel("单任务目标上限").fill("3");
  await page.getByRole("button", { name: "创建任务" }).click();
  const controlledReceipt = page.getByText(/任务已创建：[0-9a-f-]{36}/);
  await expect(controlledReceipt).toBeVisible();
  await page.getByRole("button", { name: "查看运行详情" }).click();
  await expect(page.getByRole("heading", { name: "任务运行详情" })).toBeVisible();
  await expect(page.getByText("任务开始")).toBeVisible();

  await page.getByRole("button", { name: /暂.*停/ }).click();
  await expect(page.getByText("任务已暂停")).toBeVisible();
  await page.getByRole("button", { name: /恢.*复/ }).click();
  await expect(page.getByText("任务已恢复")).toBeVisible();
  await page.getByRole("button", { name: "取消任务" }).click();
  await page.getByRole("button", { name: "确认取消" }).click();
  await expect(page.getByText("任务已取消")).toBeVisible();

  await page.getByRole("button", { name: "返回工作台" }).click();
  await page.getByRole("menuitem", { name: "新建任务" }).click();
  await page.getByLabel("搜索关键词").fill("T3-19 成功链路");
  await page.getByRole("button", { name: "创建任务" }).click();
  const succeededReceipt = page.getByText(/任务已创建：[0-9a-f-]{36}/);
  await expect(succeededReceipt).toBeVisible();
  const succeededTaskId = (await succeededReceipt.textContent())?.match(
    /[0-9a-f-]{36}/,
  )?.[0];
  expect(succeededTaskId).toBeTruthy();
  await page.getByRole("button", { name: "查看运行详情" }).click();
  await expect(page.getByText("任务完成")).toBeVisible();
  await expect(page.getByText("100%")).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: "RPA 运营工作台" })).toBeVisible();
  await page.getByRole("button", { name: succeededTaskId! }).click();
  await expect(page.getByRole("heading", { name: "任务运行详情" })).toBeVisible();
  await expect(page.getByText("任务完成")).toBeVisible();
  await expect(page.getByText("已成功").first()).toBeVisible();
  expect(consoleErrors).toEqual([]);
});
