import { expect, test } from "@playwright/test";

/**
 * The one page in the product that is allowed — and required — to name the
 * upstream projects. `e2e/upstream-name-leak.spec.ts` proves the opposite for
 * every other page; the two specs together are the whole rule.
 *
 * The path is the declared exception in
 * `contracts/quality/user-facing-terminology.v1.json`
 * (`allowedLegalDisclosurePaths`), so the static scan skips it. Nothing else
 * checks that the page a user can actually reach still carries the notice,
 * which is what this walks: 设置与诊断, the entry at its foot, click, read what
 * is rendered.
 *
 * The notice left the sidebar on 2026-07-26 (see
 * `docs/development/FIX-open-source-notice-demotion.md`). Demoting it is only
 * allowed as long as it stays reachable, so the walk below is the thing that
 * keeps the demotion honest.
 */

test("the legal notice is reachable from settings and names both projects", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));

  await page.goto("/harness.html?health=available");
  await expect(page.getByRole("heading", { name: "RPA 运营工作台" })).toBeVisible();

  await expect(page.getByRole("menuitem", { name: "第三方软件声明" })).toHaveCount(0);
  await expect(page.getByRole("menuitem", { name: "开源软件许可" })).toHaveCount(0);

  await page.getByRole("menuitem", { name: "设置与诊断" }).click();
  await page.getByRole("button", { name: "开源软件许可" }).click();

  await expect(page.getByRole("heading", { name: "开源软件许可" })).toBeVisible();

  const projects = page.getByRole("region", { name: "上游开源项目" });
  await expect(projects).toBeVisible();
  await expect(projects.getByRole("heading", { name: "MoneyPrinterTurbo" })).toBeVisible();
  await expect(projects.getByRole("heading", { name: "hyperframes" })).toBeVisible();
  await expect(projects.getByText(/MIT/)).toBeVisible();
  await expect(projects.getByText(/Apache-2\.0/)).toBeVisible();
  await expect(projects.getByText(/v1\.3\.2/)).toBeVisible();
  await expect(projects.getByText(/v0\.7\.68/)).toBeVisible();

  // A licence notice is worthless if it does not say what the code is used for.
  await expect(projects.getByText(/智能素材成片/)).toBeVisible();
  await expect(projects.getByText(/品牌动效成片/)).toBeVisible();

  await expect(page.getByRole("region", { name: "字体与素材权利" })).toBeVisible();
  await expect(
    page.getByRole("region", { name: "为什么这些名称只出现在本页" }),
  ).toBeVisible();

  expect(consoleErrors).toEqual([]);
});

test("leaving the notice takes the upstream names off the screen again", async ({
  page,
}) => {
  await page.goto("/harness.html?health=available");
  await page.getByRole("menuitem", { name: "设置与诊断" }).click();
  await page.getByRole("button", { name: "开源软件许可" }).click();
  await expect(page.getByRole("region", { name: "上游开源项目" })).toBeVisible();

  await page.getByRole("menuitem", { name: "视频制作" }).click();
  await expect(page.getByRole("heading", { name: "视频制作" })).toBeVisible();

  const tree = (await page.locator("body").ariaSnapshot()).toLowerCase();
  for (const name of ["moneyprinterturbo", "hyperframes"]) {
    expect(tree).not.toContain(name);
  }
});
