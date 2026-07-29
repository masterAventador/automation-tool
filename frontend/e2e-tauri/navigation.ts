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
 * These are read off the redesign's own acceptance
 * (`WorkbenchShell.redesign.test.tsx`), so they are the strings the shell is
 * required to render rather than ones observed once.
 */
export const WORKBENCH_MARKERS: readonly string[] = [
  "AI 运营助理",
  "给 AI 助理发消息",
];

/** Whether any part of the workbench shell is on screen right now. */
export async function workbenchIsMounted(): Promise<boolean> {
  if (await browser.$(WORKBENCH_SHELL).isExisting()) return true;
  const body = await browser.$("body").getText();
  return WORKBENCH_MARKERS.some((marker) => body.includes(marker));
}

/**
 * Wait until the App has either mounted the shell or stopped at the startup
 * gate, and say which.
 *
 * Returns rather than throws on the repair path: several specs exist precisely
 * to exercise it, and a helper that treated it as a failure would make them
 * unable to use this module — which is how 46 copies started.
 */
export async function waitForStartup(timeout = 120_000): Promise<"workbench" | "repair"> {
  await browser.waitUntil(
    async () =>
      (await workbenchIsMounted()) ||
      (await browser.$("button=打开本地修复工具").isExisting()),
    { timeout, interval: 1_000, timeoutMsg: "App never left the startup check" },
  );
  return (await workbenchIsMounted()) ? "workbench" : "repair";
}

/** Click a sidebar destination by its accessible name. */
export async function openWorkbenchSection(name: string): Promise<void> {
  await browser.$(`//*[@role='menuitem'][normalize-space()='${name}']`).click();
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

/** The brand-motion studio, with its method already chosen. */
export async function openVideoStudio(): Promise<ReturnType<typeof browser.$>> {
  await openWorkbenchSection("创作");
  await openSegment("品牌动效成片");
  await browser.$("button=打开完整制作面板").click();
  const studio = await browser.$("section[aria-label='视频制作工作区']");
  await expect(studio).toBeDisplayed();
  await studio.$("button[aria-label='选择品牌动效成片']").click();
  return studio;
}

/** The same studio by way of the material-montage method. */
export async function openMaterialVideoStudio(): Promise<ReturnType<typeof browser.$>> {
  await openWorkbenchSection("创作");
  await openSegment("智能素材成片");
  await browser.$("button=打开完整制作面板").click();
  const studio = await browser.$("section[aria-label='视频制作工作区']");
  await expect(studio).toBeDisplayed();
  return studio;
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

/** Settings and diagnostics. */
export async function openSettings(): Promise<void> {
  await openWorkbenchSection("设置");
}
