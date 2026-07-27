import { expect, test, type Page } from "@playwright/test";
import { openAutomationRuns, openTaskCreate, openVideoStudio } from "./navigation";

/**
 * T99: no control carries a widget's internal state word in its accessible name.
 *
 * antd's `Collapse` supplies its own expand arrow, and that arrow is an
 * `<span role="img">` whose `aria-label` is the literal English string
 * "collapsed" or "expanded" — see `antd/es/collapse/Collapse.js`, which builds
 * it as `"aria-label": panelProps.isActive ? 'expanded' : 'collapsed'`. A
 * `button` takes its name from its content, so the arrow's label is swallowed
 * into it and Chromium reports the header as
 *
 *     button "collapsed 诊断信息"
 *
 * — an English state word read aloud in the middle of a Chinese label, and
 * redundant besides, because the same button already carries `aria-expanded`.
 *
 * Why nobody noticed for so long: Playwright's `getByRole(name: "…")` matches a
 * *substring* by default, so `getByRole("button", { name: "诊断信息" })` keeps
 * passing against the polluted name. The two existing call sites had even
 * written the pollution down —`e2e/workbench-home.spec.ts` and
 * `Workbench.test.tsx` both carry the comment "accessible name is
 * `<折叠状态> 诊断信息`" and match with a regex to work around it. The defect was
 * documented and tested around rather than fixed.
 *
 * So every assertion here is either `exact: true` or a scan of the rendered
 * `ariaSnapshot()`. A substring assertion cannot fail on this defect and is
 * worth nothing as a guard against it.
 */

/** Roles whose accessible name is computed from their content, so they can absorb a child's label. */
const NAME_FROM_CONTENT =
  "button|link|menuitem|menuitemcheckbox|menuitemradio|tab|checkbox|radio|switch|option|heading|treeitem|gridcell|cell|columnheader|rowheader";

/** A widget state word that belongs in an ARIA state, never in a name. */
const STATE_WORDS = /\b(collapsed|expanded|selected|checked|disabled|active)\b/i;

/**
 * Every accessible name in the rendered tree, read from the snapshot Chromium
 * actually exposes rather than from the JSX that was meant to produce it.
 */
async function namesOf(page: Page): Promise<string[]> {
  const tree = await page.locator("body").ariaSnapshot();
  const pattern = new RegExp(`^\\s*-\\s*(?:${NAME_FROM_CONTENT})\\s+"([^"]*)"`, "gmu");
  return Array.from(tree.matchAll(pattern), (match) => match[1]);
}

/** Fail if any name on the current page carries a state word. */
async function expectNoStateWordInNames(page: Page, where: string): Promise<void> {
  const polluted = (await namesOf(page)).filter((name) => STATE_WORDS.test(name));
  expect(polluted, `${where} 把控件状态词混进了可访问名`).toEqual([]);
}

async function openLegalNotice(page: Page): Promise<void> {
  await page.goto("/harness.html?health=available");
  await page.getByRole("menuitem", { name: "设置" }).click();
  await page.getByRole("button", { name: "开源软件许可" }).click();
  await expect(page.getByRole("heading", { name: "开源软件许可" })).toBeVisible();
}

async function openWorkbenchWithATask(page: Page): Promise<void> {
  await page.goto("/harness.html?health=available&scenario=task-lifecycle");
  await openTaskCreate(page);
  await page.getByLabel("搜索关键词").fill("T99 可访问名");
  await page.getByRole("button", { name: "创建任务" }).click();
  await expect(page.getByText(/任务已创建：[0-9a-f-]{36}/)).toBeVisible();
  await openAutomationRuns(page);
  await expect(page.getByRole("heading", { name: "当前任务" })).toBeVisible();
}

test("许可证全文的展开按钮，名字就是它的标签", async ({ page }) => {
  await openLegalNotice(page);

  // `exact` is the whole point: without it this passes against
  // "collapsed MIT 许可证全文（英文原文）".
  await expect(
    page.getByRole("button", { name: "MIT 许可证全文（英文原文）", exact: true }),
  ).toBeVisible();

  await expectNoStateWordInNames(page, "开源软件许可（全部折叠）");
});

test("许可证全文展开之后，名字仍然只是它的标签", async ({ page }) => {
  await openLegalNotice(page);
  await page.getByRole("button", { name: "MIT 许可证全文（英文原文）", exact: true }).click();
  // The panel keys are lower-case (`LICENSE_TEXTS`); only the labels are not.
  await expect(page.getByTestId("license-text-mit")).toBeVisible();

  // The expanded state is carried by `aria-expanded`, which is where it belongs.
  await expect(
    page.getByRole("button", { name: "MIT 许可证全文（英文原文）", exact: true }),
  ).toHaveAttribute("aria-expanded", "true");

  await expectNoStateWordInNames(page, "开源软件许可（展开一项）");
});

test("工作台的诊断信息折叠，名字就是「诊断信息」", async ({ page }) => {
  await openWorkbenchWithATask(page);

  await expect(page.getByRole("button", { name: "诊断信息", exact: true })).toBeVisible();

  await expectNoStateWordInNames(page, "工作台（有运行中的任务）");
});

test("视频制作的详细说明折叠，名字就是它的标签", async ({ page }) => {
  await page.goto("/harness.html?health=available");
  await openVideoStudio(page);

  for (const method of ["智能素材成片", "品牌动效成片"]) {
    await expect(
      page.getByRole("button", { name: `${method}的详细说明`, exact: true }),
    ).toBeVisible();
  }

  await expectNoStateWordInNames(page, "视频制作");
});

test("每个页面的可访问名里都没有控件状态词", async ({ page }) => {
  await page.goto("/harness.html?health=available&scenario=publishing");

  for (const label of [
    "AI 助理",
    "热点发现",
    "创作",
    "发布",
    "消息与互动",
    "自动化",
    "账号与平台",
    "设置",
  ]) {
    await page.getByRole("menuitem", { name: label }).click();
    await expectNoStateWordInNames(page, `页面 ${label}`);
  }
});
