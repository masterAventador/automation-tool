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
  await expect(page.getByText("Control Plane 不可用")).toBeVisible();
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
