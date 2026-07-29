import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { browser, expect } from "@wdio/globals";
import {
  openMaterialVideoStudio,
  waitForStartup,
} from "./navigation";

interface MotionStylePresetContract {
  readonly presets: ReadonlyArray<{
    readonly id: string;
    readonly displayName: string;
  }>;
}

const contract = JSON.parse(
  readFileSync(
    resolve(process.cwd(), "../contracts/video/motion-style-presets.v1.json"),
    "utf-8",
  ),
) as MotionStylePresetContract;

describe("BM-06/BM-07 production App motion style catalog acceptance", () => {
  it("recommends, brand-tunes and expands all Chinese styles with keyboard selection", async () => {
    // The embedded service reuses the App across spec workers. Reloading keeps
    // this scenario's "no method selected" gate independent of earlier tests.
    await browser.refresh();
    await waitForStartup();

    const studio = await openMaterialVideoStudio();

    await studio.$("div[role='tab']=制作设置").click();
    await expect(studio).toHaveText(expect.stringContaining("尚未选择制作方式"));
    assert.equal(
      await studio.$("div[role='radiogroup'][aria-label='选择整体画面风格']").isExisting(),
      false,
      "style catalog must stay gated before the brand motion method is selected",
    );

    await studio.$("div[role='tab']=新建视频").click();
    await studio.$("button[aria-label='选择品牌动效成片']").click();
    await studio.$("div[role='tab']=制作设置").click();

    const group = await studio.$("div[role='radiogroup'][aria-label='选择整体画面风格']");
    await expect(group).toBeDisplayed();
    await expect(studio).toHaveText(expect.stringContaining("尚未选择风格"));

    const recommendations = await group.$$("div[role='radio']");
    assert.equal(recommendations.length, 3, "catalog must recommend exactly three styles first");
    for (let index = 0; index < recommendations.length; index += 1) {
      await expect(recommendations[index]!).toHaveText(expect.stringContaining("推荐"));
    }

    const headline = await studio.$("input[aria-label='预览标题']");
    const previewBody = await studio.$("textarea[aria-label='预览正文']");
    await headline.setValue("本周销售增长 38%");
    await previewBody.setValue("华东区和续费业务共同推动增长。");
    await studio.$("input[aria-label='品牌主色']").setValue("#1234ab");
    await studio.$("input[aria-label='品牌辅助色']").setValue("#f2eadb");
    await studio.$("input[aria-label='品牌字体']").setValue("Acme Sans");
    const logoBytes =
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";
    await browser.execute(
      (encodedLogo: string) => {
        const input = document.querySelector<HTMLInputElement>(
          "input[aria-label='品牌 Logo 文件']",
        );
        if (input === null) throw new Error("brand logo input is missing");
        const raw = globalThis.atob(encodedLogo);
        const bytes = Uint8Array.from(raw, (character) => character.charCodeAt(0));
        const transfer = new DataTransfer();
        transfer.items.add(new File([bytes], "avatar.png", { type: "image/png" }));
        Object.defineProperty(input, "files", {
          configurable: true,
          value: transfer.files,
        });
        input.dispatchEvent(new Event("change", { bubbles: true }));
      },
      logoBytes,
    );

    const actualPreview = await studio.$("section[aria-label='实际内容风格预览']");
    await expect(actualPreview).toHaveText(expect.stringContaining("本周销售增长 38%"));
    await expect(actualPreview).toHaveText(
      expect.stringContaining("华东区和续费业务共同推动增长。"),
    );
    await expect(actualPreview).toHaveText(expect.stringContaining("Acme Sans"));
    await expect(actualPreview).toHaveText(expect.stringContaining("avatar.png"));
    await expect(actualPreview.$("img[alt='品牌 Logo 预览']")).toBeDisplayed();

    await studio.$("button=查看全部 12 套风格").click();
    const radios = await group.$$("div[role='radio']");
    assert.equal(radios.length, 12, "catalog must expose exactly twelve styles");
    assert.equal(contract.presets.length, 12, "locked contract must expose twelve presets");
    for (let index = 0; index < contract.presets.length; index += 1) {
      const preset = contract.presets[index]!;
      const radio = radios[index]!;
      assert.equal(
        await radio.getAttribute("aria-label"),
        preset.displayName,
        `style ${index} must use the contract Chinese name`,
      );
      assert.equal(await radio.getAttribute("aria-checked"), "false");
      await expect(radio).toHaveText(expect.stringContaining("适用场景"));
      await expect(radio).toHaveText(expect.stringContaining("风格标签"));
      await expect(radio).toHaveText(expect.stringContaining("实际内容预览"));
    }

    const first = contract.presets[0]!;
    const second = contract.presets[1]!;
    await radios[0]!.click();
    assert.equal(await radios[0]!.getAttribute("aria-checked"), "true");
    await expect(studio).toHaveText(
      expect.stringContaining(`已选择风格：${first.displayName}`),
    );

    await browser.keys(["ArrowRight"]);
    await browser.keys(["Enter"]);
    assert.equal(await radios[1]!.getAttribute("aria-checked"), "true");
    assert.equal(await radios[0]!.getAttribute("aria-checked"), "false");
    await expect(studio).toHaveText(
      expect.stringContaining(`已选择风格：${second.displayName}`),
    );

    let checkedCount = 0;
    for (let index = 0; index < radios.length; index += 1) {
      if ((await radios[index]!.getAttribute("aria-checked")) === "true") {
        checkedCount += 1;
      }
    }
    assert.equal(checkedCount, 1, "exactly one style may stay selected");

    const body = await browser.$("body").getText();
    assert.doesNotMatch(body, /moneyprinter|hyperframes|b-roll/i);
    assert.doesNotMatch(
      body,
      /biennale|blockframe|blue-professional|bold-poster|broadside|capsule|cartesian|cobalt-grid|coral|creative-mode|daisy-days|editorial-forest|code-editorial/i,
    );

    // 回到工作台首页，避免把页面状态遗留给后续验收用例。
    await waitForStartup();
  });
});
