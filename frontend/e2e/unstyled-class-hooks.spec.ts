import { expect, test, type Page } from "@playwright/test";

import { MINIMUM_WINDOW, PRODUCTION_WINDOW } from "./production-window";

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
 * 1280x800 and 960x640 turned up two that actually change what the operator
 * sees. Both are pinned below. The rest of the unmatched names — `.legal-notice-item`,
 * `.model-service-settings-card`, `.video-editing-create-form`,
 * `.video-editing-project-row` and friends — were measured too and are correct
 * without a rule, because a `Card`, a `<section>` and a block-level `<li>` fill
 * their parent unaided. Those are spare hooks, not defects, and are deliberately
 * not asserted here.
 *
 * Both window sizes come from `production-window.ts`, which reads them out of
 * `src-tauri/tauri.conf.json` — the file Tauri itself reads. Neither number is
 * written here. Copying them is what broke the baseline once already (T96): the
 * shared config said 800 while every spec ran at 720, so the numbers in the
 * headers were not the numbers the assertions ran against.
 *
 * `PRODUCTION_WINDOW` is already what the shared config hands every spec. The
 * credential-form describe still names it, because it runs the same body at both
 * sizes and the size is what distinguishes the two runs; the clip-row describe
 * below takes the config's word for it and asks for nothing.
 */

const HARNESS = "/harness.html?health=available";

const SOURCE_ARTIFACTS = [
  "6f1a2b3c-4d5e-4f60-8a1b-2c3d4e5f6071",
  "7a2b3c4d-5e6f-4071-9b2c-3d4e5f607182",
];

async function openSettings(page: Page): Promise<void> {
  await page.goto(HARNESS);
  await page.getByRole("menuitem", { name: "设置与诊断" }).click();
  await expect(page.getByRole("heading", { name: "剪辑服务凭据" })).toBeVisible();
}

/**
 * Width of an element and of the content box of the element it sits in.
 *
 * Both, because widening only the outer container is worse than widening
 * nothing: on 视频剪辑 that produced a full-width card with its right half
 * blank, which passed an outer-width assertion while looking wrong on screen.
 * Every level from the card body down to the individual field is checked.
 */
function fitsInParent(page: Page, selector: string) {
  return page.locator(selector).evaluate((element) => {
    const parent = element.parentElement;
    if (parent === null) {
      throw new Error(`${element.className} 没有父元素`);
    }
    const style = getComputedStyle(parent);
    return {
      width: Math.round(element.getBoundingClientRect().width),
      parent: Math.round(
        parent.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
      ),
    };
  });
}

async function expectFillsItsParent(page: Page, selector: string, label: string): Promise<void> {
  const { width, parent } = await fitsInParent(page, selector);
  expect(width, `${label}：${selector} 只用了它拿到的 ${parent}px 中的 ${width}px`).toBeGreaterThanOrEqual(
    parent - 1,
  );
}

/**
 * 视频剪辑服务 credential form.
 *
 * It sits directly under 模型服务 on the same page and is built the same way —
 * a `Card`, a vertical `Space`, a `<section>`, an inner vertical `Space` of
 * `<label>`s wrapping a `Select` and two `Input`s. 模型服务 declares
 * `.model-service-settings-stack`, `.model-service-fields`,
 * `.model-service-fields label`, `.model-service-fields .ant-select` and
 * `.model-service-fields .ant-input-password`; 视频剪辑服务 declares nothing at
 * all, so its inner `Space` shrink-wrapped to 358px of a 942px card and the
 * region `Select` fell back to antd's own 134px. Two cards, one page, one at
 * full width and one at a third of it.
 */
for (const size of [PRODUCTION_WINDOW, MINIMUM_WINDOW]) {
  test.describe(`视频剪辑服务凭据表单填满它的卡片 @ ${size.width}x${size.height}`, () => {
    test.use({ viewport: size });

    test("表单每一层都填满上一层", async ({ page }) => {
      await openSettings(page);

      await expectFillsItsParent(page, ".video-editing-service-stack", "视频剪辑服务");
      await expectFillsItsParent(page, ".video-editing-service-fields", "视频剪辑服务");
      await expectFillsItsParent(page, ".video-editing-service-inputs", "视频剪辑服务");
    });

    test("地域下拉和密钥输入框填满表单", async ({ page }) => {
      await openSettings(page);

      const form = await page
        .locator(".video-editing-service-inputs")
        .evaluate((element) => Math.round(element.getBoundingClientRect().width));

      for (const [selector, name] of [
        [".video-editing-service-inputs .ant-select", "服务地域下拉"],
        [".video-editing-service-inputs .ant-input-password", "AccessKey Secret 输入框"],
      ] as const) {
        const width = await page
          .locator(selector)
          .first()
          .evaluate((element) => Math.round(element.getBoundingClientRect().width));
        expect(width, `视频剪辑服务：${name}只用了表单 ${form}px 中的 ${width}px`).toBeGreaterThanOrEqual(
          form - 1,
        );
      }
    });

    test("和同一页的模型服务表单一样宽", async ({ page }) => {
      await openSettings(page);

      const widths = await page.evaluate(() => {
        const widthOf = (selector: string): number => {
          const element = document.querySelector(selector);
          return element === null ? -1 : Math.round(element.getBoundingClientRect().width);
        };
        return {
          model: widthOf(".model-service-fields"),
          editing: widthOf(".video-editing-service-inputs"),
        };
      });

      expect(widths.model, "设置页上找不到模型服务表单").toBeGreaterThan(0);
      expect(
        widths.editing,
        `同一页的两张凭据卡不一样宽：模型服务 ${widths.model}px，视频剪辑服务 ${widths.editing}px`,
      ).toBe(widths.model);
    });
  });
}

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
    await page.getByRole("menuitem", { name: "视频剪辑" }).click();
    await page.getByLabel("剪辑项目标题").fill("转场控件验收项目");
    await page.getByLabel("输入素材引用").fill(SOURCE_ARTIFACTS.join("\n"));
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
