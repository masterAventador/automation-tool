import { browser, expect } from "@wdio/globals";

/**
 * The one route through the desktop shell, for the WebdriverIO suite.
 *
 * The Playwright suite has had `frontend/e2e/navigation.ts` for a while and it
 * is why that suite survived the AI-first redesign (`c4d0d14`): one module knew
 * the route, one commit updated it, 78 tests carried on. This suite had written
 * the route out by hand in 46 of its 48 files, so the same redesign broke all of
 * them at once — and nobody saw it, because no gate runs this suite in bulk.
 * Each spec is driven by its own `run_*_acceptance.py`, so a broken one is only
 * discovered by whoever happens to need that acceptance next.
 *
 * `scripts/test_desktop_e2e_navigation.py` keeps this module naming the same
 * destinations as the Playwright one. That is the chain that makes it stay
 * true: the Playwright suite runs, a renamed sidebar entry turns it red, and the
 * gate then refuses to let this module drift away from it.
 */

/**
 * The shell itself, which is what survives a redesign of what is inside it.
 *
 * `h2=RPA 运营工作台` used to be the signal and no longer exists: the redesign
 * replaced the single workbench heading with a per-section one, and the section
 * the App lands on — the assistant — has no heading at all. The navigation
 * landmark is the shell rather than anything in it.
 */
export const WORKBENCH_SHELL = "nav[aria-label='桌面主导航']";

/**
 * Text that proves the workbench is mounted, for the assertions that require it
 * to be *absent*.
 *
 * Those are the dangerous half of the redesign's damage. Five specs asserted
 * `doesNotMatch(bodyText, /RPA 运营工作台/)` — the check that a signed-out or
 * revoked App must not reach the workbench. That string appears nowhere in the
 * product now, so the assertion passes over an App that is showing the
 * workbench in full. A gate that cannot fail is worse than a missing one: it
 * reports safety it is not providing.
 *
 * Deliberately just the landmark. A first version also matched body text
 * against 「AI 运营助理」/「给 AI 助理发消息」, and both were wrong: the first
 * only renders on the assistant section, and the second is an `aria-label` —
 * `getText()` returns rendered text and never sees an attribute, so that half
 * was dead code pretending to be a second line of defence. The landmark on its
 * own is exact: `StartupGate` either returns the shell or returns the repair
 * screen, never both, so the nav element exists if and only if the workbench is
 * mounted.
 */
export async function workbenchIsMounted(): Promise<boolean> {
  return browser.$(WORKBENCH_SHELL).isExisting();
}

/**
 * Wait for the App to mount the workbench, and refuse anything else.
 *
 * This replaced `await expect(h2).toHaveText("RPA 运营工作台")`, which asserted
 * two things — the App finished starting, *and* it is on the workbench. A first
 * version returned `"workbench" | "repair"` and every one of the 42 call sites
 * discarded the value, which quietly turned the second assertion into "either
 * outcome is fine". Worse, it then had exactly the shape of a navigation call —
 * awaited, typed, returning — so the migration script could substitute it for
 * one and nothing anywhere would object. Seven specs lost their navigation that
 * way.
 *
 * So the default is strict. The handful of specs that exist to exercise the
 * startup gate ask for it explicitly, and that ask is visible at the call site
 * rather than implied by a discarded return value.
 */
export async function waitForStartup(
  { allowRepair = false, timeout = 120_000 }: {
    readonly allowRepair?: boolean;
    readonly timeout?: number;
  } = {},
): Promise<"workbench" | "repair"> {
  await browser.waitUntil(
    async () =>
      (await workbenchIsMounted()) ||
      (await browser.$("button=打开本地修复工具").isExisting()),
    { timeout, interval: 1_000, timeoutMsg: "App never left the startup check" },
  );
  if (await workbenchIsMounted()) return "workbench";
  if (allowRepair) return "repair";
  throw new Error(
    "App stopped at the startup repair gate; pass { allowRepair: true } if that " +
      "is what this spec is testing",
  );
}

/**
 * Click a sidebar destination by name.
 *
 * Matches a *descendant*'s text rather than the whole item's, because the shell
 * hangs an antd `Badge` on 创作 whose count is a word — 失败 / 未知 / 完成 — so
 * the item's own `normalize-space()` becomes 「创作完成」 and an exact match on
 * the item stops finding it. `WorkbenchShell.tsx` states this requirement in so
 * many words next to the badge.
 */
export async function openWorkbenchSection(name: string): Promise<void> {
  await browser
    .$(`//*[@role='menuitem'][.//*[normalize-space()='${name}'] or normalize-space()='${name}']`)
    .click();
}

/**
 * Click a segmented control item by its label.
 *
 * `*` rather than `div`: antd renders a Segmented item as a `label`, which the
 * Playwright side never had to know because `.ant-segmented-item` does not
 * constrain the tag.
 */
async function openSegment(label: string): Promise<void> {
  await browser
    .$(`//*[contains(@class,'ant-segmented-item')][.//*[normalize-space()='${label}']]`)
    .click();
}

const STUDIO = "section[aria-label='视频制作工作区']";

/**
 * Open the full studio panel, unless it is already open.
 *
 * Idempotent on purpose. The embedded Tauri service keeps one App alive across
 * every spec in a run, and the studio panel does not close itself — so the
 * second caller lands on a page that has no 创作 segment and no
 * 打开完整制作面板 button, and a helper that assumed the landing page would
 * fail on «element wasn't found» with no hint that the real cause was the
 * previous test. Measured 2026-07-29 in CQ-01: test 1 opened it, tests 2 and 3
 * both died this way.
 */
async function openStudioPanel(segment: string): Promise<ReturnType<typeof browser.$>> {
  const open = await browser.$(STUDIO);
  if (!(await open.isDisplayed().catch(() => false))) {
    await openWorkbenchSection("创作");
    await openSegment(segment);
    await browser.$("button=打开完整制作面板").click();
  }
  const studio = await browser.$(STUDIO);
  await expect(studio).toBeDisplayed();
  return studio;
}

/** The brand-motion studio, with its method already chosen. */
export async function openVideoStudio(): Promise<ReturnType<typeof browser.$>> {
  const studio = await openStudioPanel("品牌动效成片");
  await studio.$("button[aria-label='选择品牌动效成片']").click();
  return studio;
}

/** The same studio by way of the material-montage method. */
export async function openMaterialVideoStudio(): Promise<ReturnType<typeof browser.$>> {
  return openStudioPanel("智能素材成片");
}

/** The lightweight editing workspace. */
export async function openVideoEditing(): Promise<void> {
  await openWorkbenchSection("创作");
  await openSegment("轻量剪辑");
}

/** The publishing workspace, on its 新建发布 step. */
export async function openPublishingWorkspace(): Promise<void> {
  await openWorkbenchSection("发布");
  await browser.$("//button[contains(normalize-space(),'新建发布')]").click();
}

/** The automation run history. */
export async function openAutomationRuns(): Promise<void> {
  await openWorkbenchSection("自动化");
  await browser.$("button=查看运行记录").click();
}

/** The automation task composer. */
export async function openTaskCreate(): Promise<void> {
  await openWorkbenchSection("自动化");
  await browser.$("//button[contains(normalize-space(),'新建运营任务')]").click();
}

/**
 * Settings and diagnostics, with the page confirmed on screen.
 *
 * The two specs this replaced each returned only after asserting their own
 * settings card was displayed. Collapsing them to a bare click would have made
 * this helper return while the page was still whatever it was — so the landing
 * assertion stays here, once, instead of twice.
 */
export async function openSettings(): Promise<void> {
  await openWorkbenchSection("设置");
  await expect(await browser.$("h2")).toHaveText("设置");
}

/** The two creation-method cards, with their comparison rows opened.
 *
 * The redesign folded the ten comparison rows into a collapse panel on each
 * card, shut by default — the reason is written next to it in
 * `VideoStudio.tsx`: opening them pushes the page back to 1240px. A spec that
 * reads the rows without opening them reads a one-line summary instead and
 * reports the copy as missing.
 *
 * Two traps this helper exists to hold in one place, both paid for once
 * already:
 *
 * * `card.$("//…")` searches from the *document root*, not from the card, so
 *   the second iteration toggles the first card again and shuts it. It has to
 *   be `.//`;
 * * the collapse is animated, so the rows are not in the DOM at the moment the
 *   click returns.
 */
export async function openCreationMethodCards(
  studio: ReturnType<typeof browser.$>,
): Promise<ReturnType<typeof browser.$>[]> {
  const cards = await studio.$$("article.video-method-card");
  const opened: ReturnType<typeof browser.$>[] = [];
  for (let index = 0; index < (await cards.length); index += 1) {
    const card = cards[index]!;
    const toggle = await card.$(".//*[contains(@class,'ant-collapse-header')]");
    if (await toggle.isExisting()) {
      await toggle.click();
      await browser.waitUntil(
        async () => (await card.getText()).includes("最适合"),
        { timeout: 10_000, timeoutMsg: "制作方式卡片的详细说明没有展开" },
      );
    }
    opened.push(card);
  }
  return opened;
}

/**
 * 等某一条任务出现在运行记录里，按它的标识而不是按它的显示名。
 *
 * 列表的行名改版后是创建时刻（`07-29 12:01:54 的任务`），不再印 UUID——那是有意的
 * 可读性改动，`Workbench.test.tsx` 有一条测试专门守着。标识仍在，作为惰性的
 * `data-task-id`（`Workbench.tsx`）。任务详情页仍印完整 UUID。
 *
 * 失败时把页面上**实际存在的**行都报出来。2026-07-29 有两条 spec（task-run、
 * task-discovery）在「处理完任务 A 之后任务 B 的行不见了」这个形状上各红了三轮，
 * 而每一轮的错误只说「没等到 X」——那句话对「它在别处」「它被挤出前五条」
 * 「页面根本不是运行记录」三种情况长得一模一样，所以三轮都在猜。
 */
export async function waitForTaskRow(taskId: string, timeout = 90_000): Promise<void> {
  try {
    await browser.waitUntil(
      async () => browser.$(`button[data-task-id="${taskId}"]`).isExisting(),
      { timeout },
    );
  } catch {
    const present = await browser.execute(() =>
      Array.from(document.querySelectorAll<HTMLElement>("[data-task-id]")).map(
        (node) => `${node.dataset["taskId"]}｜${node.textContent ?? ""}`,
      ),
    );
    const heading = await browser.$("h2").getText().catch(() => "(无 h2)");
    // 正文摘要：h2 与行数都说不出「这是哪一页」时，只有它能。
    const body = (await browser.$("body").getText().catch(() => "(读不到 body)"))
      .replace(/\s+/g, " ")
      .slice(0, 400);
    throw new Error(
      `运行记录里没有出现任务 ${taskId}；`
      + `当前页面 h2=「${heading}」；`
      + `页面上实际有 ${present.length} 行：${JSON.stringify(present)}；`
      + `正文前 400 字：${body}`,
    );
  }
}
