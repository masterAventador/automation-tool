import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";
import {
  openCreationMethodCards,
  openMaterialVideoStudio,
  waitForStartup,
} from "./navigation";

describe("VF-07 production App creation method acceptance", () => {
  it("compares and selects exactly two understandable creation methods", async () => {
    // A WDIO session change does not restart the embedded Tauri App. Reload so
    // method and tab state from the preceding spec cannot satisfy this one.
    await browser.refresh();
    await waitForStartup();

    const studio = await openMaterialVideoStudio();
    // Declare this scenario's target page explicitly so a future default-tab
    // change cannot make the acceptance silently inspect a different panel.
    await studio.$("div[role='tab']=新建视频").click();
    const materialMethod = await studio.$("button[aria-label='选择智能素材成片']");
    const motionMethod = await studio.$("button[aria-label='选择品牌动效成片']");
    await expect(materialMethod).toBeDisplayed();
    await expect(motionMethod).toBeDisplayed();
    await expect(materialMethod).toHaveAttribute("aria-pressed", "false");
    await expect(motionMethod).toHaveAttribute("aria-pressed", "false");

    const comparisonLabels = [
      "最适合",
      "不适合",
      "举个例子",
      "外部服务",
      "本机处理",
      "预计耗时",
      "设备占用",
      "临时磁盘",
      "网络消耗",
      "数据与隐私",
    ] as const;
    // The rows live inside each card's collapse panel, shut by default since the
    // redesign — reading them without opening them reads a one-line summary and
    // reports every label as missing (measured 2026-07-29: 0 of 2 for all ten).
    await openCreationMethodCards(studio);
    for (const label of comparisonLabels) {
      const matches = await studio.$$(`dt=${label}`);
      assert.equal(matches.length, 2, `${label} must be explained by both methods`);
    }

    await materialMethod.click();
    await expect(materialMethod).toHaveAttribute("aria-pressed", "true");
    await expect(motionMethod).toHaveAttribute("aria-pressed", "false");
    await expect(studio).toHaveText(expect.stringContaining("已选择：智能素材成片"));

    await motionMethod.click();
    await expect(materialMethod).toHaveAttribute("aria-pressed", "false");
    await expect(motionMethod).toHaveAttribute("aria-pressed", "true");
    await expect(studio).toHaveText(expect.stringContaining("已选择：品牌动效成片"));
    // Same vanished opener as in `video-studio.spec.ts`: `OperationsWorkspace`
    // renders `VideoStudio` with `embedded` unconditionally, which replaces the
    // button with a notice. What this line was really guarding — that choosing
    // 品牌动效成片 does not offer the material-montage opener — is now true by
    // construction, so what is left to assert is that it is not there at all.
    assert.equal(await studio.$("button=打开完整制作界面").isExisting(), false);

    const body = await browser.$("body").getText();
    assert.doesNotMatch(body, /moneyprinter|hyperframes|b-roll/i);
    assert.doesNotMatch(body, /真人生成|网址转视频/);
  });
});
