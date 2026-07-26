import { expect, test, type Page } from "@playwright/test";
import { writeThePublishCopy } from "./publish-copy";

/**
 * CQ-02: no upstream technology name reaches the operator.
 *
 * The static scan (AV-03) reads source literals. It cannot see an accessible
 * name assembled at runtime — `aria-label={`发布到${name}`}` simply does not
 * exist until the page renders — and the accessibility tree is exactly what a
 * screen reader will read out. So this walks the rendered tree instead.
 */

/** Names of the upstream projects and their mechanisms, in the forms a leak takes. */
const UPSTREAM_NAMES = [
  "moneyprinterturbo",
  "money printer turbo",
  "money-printer-turbo",
  "hyperframes",
  "hyper frames",
  "hyper-frames",
  "browser use",
  "browser_use",
  "browseruse",
  "playwright",
  "chromium",
  "webdriver",
  "official_api",
  "b-roll",
  "poc",
];

/**
 * Every navigation entry. 开源软件许可 is deliberately absent: it is no longer a
 * sidebar entry at all (it hangs off the foot of 设置与诊断), and it is the
 * declared legal exception (`allowedLegalDisclosurePaths`) that is *required*
 * to carry the upstream names — `e2e/third-party-software-notice.spec.ts`
 * asserts it does. Do not add it here.
 *
 * 设置与诊断 below is walked without opening that entry, which is exactly the
 * assertion that matters after the demotion: the settings page itself must
 * still be clean even though the notice now hangs off it.
 */
const PAGES = [
  "工作台",
  "新建任务",
  "任务记录",
  "视频制作",
  "视频剪辑",
  "作品发布",
  "平台状态",
  "设置与诊断",
];

async function assertNoLeak(page: Page, where: string): Promise<void> {
  // `ariaSnapshot` renders the tree a screen reader walks, including names
  // assembled at runtime that no source scan can see.
  const tree = await page.locator("body").ariaSnapshot();
  expect(tree.length, `${where} rendered no accessibility tree`).toBeGreaterThan(0);

  const folded = [await page.title(), tree].join("   ").toLowerCase();

  for (const name of UPSTREAM_NAMES) {
    expect(folded, `${where} leaked "${name}" into what the operator can hear`).not.toContain(
      name,
    );
  }
}

test("no page leaks an upstream name into the accessibility tree", async ({ page }) => {
  await page.goto("/harness.html?health=available&scenario=publishing");

  for (const label of PAGES) {
    await page.getByRole("menuitem", { name: label }).click();
    await assertNoLeak(page, `page ${label}`);
  }
});

test("the publish critical point stays clean once it is rendered", async ({ page }) => {
  await page.goto("/harness.html?health=available&scenario=publishing");
  await page.getByRole("menuitem", { name: "作品发布" }).click();
  await writeThePublishCopy(page);
  await page.getByRole("button", { name: /发布到抖音/ }).click();
  await expect(page.getByRole("group", { name: "确认发布内容" })).toBeVisible();

  await assertNoLeak(page, "the publish critical point");
});

test("a settled uncertain publish stays clean in what it explains", async ({ page }) => {
  await page.goto("/harness.html?health=available&scenario=publishing-uncertain");
  await page.getByRole("menuitem", { name: "作品发布" }).click();
  await writeThePublishCopy(page);
  await page.getByRole("button", { name: /发布到抖音/ }).click();
  await page.getByRole("button", { name: /确认发布/ }).click();
  await expect(page.getByText("结果待人工确认")).toBeVisible();

  await assertNoLeak(page, "an uncertain publish outcome");
});

test("a blocked startup diagnostic stays clean in what it names", async ({ page }) => {
  // Error and diagnostic copy is assembled at runtime from codes, which is
  // exactly where an internal name tends to escape.
  await page.goto("/harness.html?health=unavailable");
  await expect(page.getByRole("heading", { name: "暂时无法连接业务服务" })).toBeVisible();

  await assertNoLeak(page, "the startup diagnostic");
});
