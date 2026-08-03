import { expect, test } from "@playwright/test";

import { openVideoEditing } from "./navigation";

/**
 * Class names that were written in the JSX and never given a rule.
 *
 * This is the defect T88 found on 视频剪辑 and it is not a one-off: writing
 * `className="x"` is silent when no `.x` exists, so the element simply keeps
 * whatever the browser or antd gave it. A `Space` keeps `inline-flex` and
 * shrink-wraps; a bare `<select>` keeps the platform's own widget. Neither
 * fails a build, a type check or a lint, and neither is visible in review —
 * only on screen, at a real window size.
 *
 * A sweep of every `className` string under `src/` against `global.css` at
 * 1280x800 and 960x640 turned up two that actually changed what the operator
 * saw. One was the 视频剪辑服务 credential form's fields; LE-01 deleted that
 * form along with the Aliyun route it configured, so the assertions pinning it
 * went with it. The other — the timeline clip row's 转场 dropdown — is still
 * pinned below. The rest of the unmatched names — `.legal-notice-item`,
 * `.model-service-settings-card`, `.video-editing-create-form`,
 * `.video-editing-project-row` and friends — were measured too and are correct
 * without a rule, because a `Card`, a `<section>` and a block-level `<li>` fill
 * their parent unaided. Those are spare hooks, not defects, and are deliberately
 * not asserted here.
 */

const HARNESS = "/harness.html?health=available";

/**
 * The 转场 control in a timeline clip row.
 *
 * It is the app's only bare `<select>`; everything beside it in that row is an
 * antd `Input` or `Button`. `.video-editing-transition-select` was written on
 * it and never given a rule, so it kept the platform widget: 19px tall against
 * 32px inputs, Arial against the app's system stack, 13.33px text against 14px,
 * square corners against 8px. The assertions compare it to the `Input` it sits
 * next to rather than to fixed numbers, so a theme change moves both together.
 */
test.describe("片段行的转场下拉和它旁边的输入框长得一样", () => {
  test("高度、字体和圆角与同一行的输入框一致", async ({ page }) => {
    await page.goto(HARNESS);
    await openVideoEditing(page);
    await page.getByLabel("剪辑项目标题").fill("转场控件验收项目");
    await page.getByRole("button", { name: "创建剪辑项目" }).click();
    await expect(page.getByText("已创建剪辑项目：转场控件验收项目")).toBeVisible();
    await page.getByRole("tab", { name: "时间轴编辑" }).click();

    const row = await page.locator(".video-editing-clip").first().evaluate((clip) => {
      const measure = (element: Element | null) => {
        if (element === null) return null;
        const style = getComputedStyle(element);
        return {
          height: Math.round(element.getBoundingClientRect().height),
          fontSize: style.fontSize,
          fontFamily: style.fontFamily,
          radius: style.borderTopLeftRadius,
        };
      };
      return {
        select: measure(clip.querySelector(".video-editing-transition-select")),
        input: measure(clip.querySelector(".ant-input-affix-wrapper")),
      };
    });

    expect(row.select, "片段行里找不到转场下拉").not.toBeNull();
    expect(row.input, "片段行里找不到时长输入框").not.toBeNull();

    expect(
      row.select!.height,
      `转场下拉 ${row.select!.height}px 高，同一行的输入框 ${row.input!.height}px 高`,
    ).toBe(row.input!.height);

    expect(
      row.select!.fontFamily,
      `转场下拉用的是 ${row.select!.fontFamily}，同一行的输入框用的是 ${row.input!.fontFamily}`,
    ).toBe(row.input!.fontFamily);

    expect(
      row.select!.fontSize,
      `转场下拉字号 ${row.select!.fontSize}，同一行的输入框 ${row.input!.fontSize}`,
    ).toBe(row.input!.fontSize);

    expect(
      row.select!.radius,
      `转场下拉圆角 ${row.select!.radius}，同一行的输入框 ${row.input!.radius}`,
    ).toBe(row.input!.radius);
  });
});
