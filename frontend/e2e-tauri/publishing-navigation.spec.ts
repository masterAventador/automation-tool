import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";

import { openPublishingWorkspace, waitForStartup } from "./navigation";

/**
 * PB-07 的正常用户路径：从真实 App 的左侧导航点进「发布」，断言操作者**看到**什么。
 *
 * 同目录的 `publishing.spec.ts` 走的是 `core.invoke("get_publish_workspace")`，
 * 直接调 Tauri Command——它证明的是桥的契约，不是导航。那条 spec 的文件头写着
 * 「clicking through to the page in the real App」未覆盖，理由是 debug 构建会停在
 * 启动环境闸。这条 spec 用 CQ-02 同一套 `video_studio_startup_harness` 把启动环境
 * 备好，于是工作台能挂载，发布页可以从正常入口点进去。
 *
 * PB-07 明确要求的两件事，只有在这里才能证明：
 *   1. 发布页只展示 B站/抖音两个平台；
 *   2. B站没有凭据时显示可理解的「待配置」，**而不是让整个发布模块启动失败**。
 */

const MECHANISM_WORDS = [
  "browser_use",
  "browseruse",
  "playwright",
  "chromium",
  "official_api",
  "officialapi",
  "moneyprinter",
  "hyperframes",
];

const AWAITING_CONFIGURATION_HINT = "请先到设置中配置该平台的发布凭据；其他平台不受影响。";

describe("PB-07 publish page normal-navigation acceptance", () => {
  before(async () => {
    await browser.refresh();
    await waitForStartup();
  });

  it("shows exactly the two supported platforms and why each is not ready yet", async () => {
    await openPublishingWorkspace();

    const body = await browser.$("body");
    const text = await body.getText();

    // 平台名。断言两个都在，且第三个平台名一个都没有——PB-07 要求发布页只展示
    // B站/抖音，多出任何一个都是产品越界。
    assert.match(text, /B站/, "发布页应展示 B站");
    assert.match(text, /抖音/, "发布页应展示 抖音");
    for (const absent of ["快手", "视频号", "小红书", "微博"]) {
      assert.doesNotMatch(
        text,
        new RegExp(absent),
        `发布页不应出现第三个平台：${absent}`,
      );
    }

    // 本机没有配 B站 凭据、也没有登录抖音，产品必须把这两种「还不能发」讲清楚，
    // 而不是整页报错。
    assert.match(text, /待配置/, "B站 未配置凭据时应显示「待配置」");
    assert.match(text, /待登录/, "抖音 未登录时应显示「待登录」");
    assert.match(
      text,
      new RegExp(AWAITING_CONFIGURATION_HINT.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")),
      "「待配置」必须附带可理解的下一步说明",
    );

    // 「B站未配置凭据时不让整个发布模块启动失败」这一条的证据，就是上面那几条正向
    // 断言本身：两个平台的卡片、各自的状态标签和那句下一步说明都真的渲染出来了。
    // 模块级失败时页面不会有这些，只会剩一条错误。所以这里额外确认没有整模块的
    // 错误提示占据页面。
    await expect(await browser.$(".ant-alert-error")).not.toBeDisplayed();
  });

  it("never tells the operator how a platform is reached", async () => {
    await openPublishingWorkspace();

    const raw = await browser.$("body").getText();
    // 先证明页面确实是发布工作区：否则下面全是 doesNotMatch，页面空白也会绿——
    // 这正是本 spec 要防的那种假通过。
    assert.match(raw, /B站/, "断言机制词之前，先确认停在发布工作区");
    const text = raw.toLowerCase();

    for (const word of MECHANISM_WORDS) {
      assert.doesNotMatch(
        text,
        new RegExp(word),
        `发布页泄漏了平台接入机制：${word}`,
      );
    }
  });
});
