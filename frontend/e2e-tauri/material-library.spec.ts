import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";
import { openVideoEditing, waitForStartup } from "./navigation";

interface MaterialLibraryPreparation {
  readonly installationId: string;
}

const UUID_V4_SOURCE =
  "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}";
const UUID_V4 = new RegExp(UUID_V4_SOURCE, "g");

type BrowserElement = ReturnType<typeof browser.$>;

async function materialIds(workbench: BrowserElement): Promise<readonly string[]> {
  return Array.from(new Set((await workbench.getText()).match(UUID_V4) ?? []));
}

async function importUniqueMaterial(
  workbench: BrowserElement,
  expectedKind: "视频" | "音频" | "图片",
): Promise<string> {
  const before = new Set(await materialIds(workbench));
  await workbench.$("button=导入本机素材").click();
  await browser.waitUntil(
    async () => {
      const text = await workbench.getText();
      return text.includes("素材已导入本机素材库。") && (await materialIds(workbench)).length > before.size;
    },
    { timeout: 90_000, timeoutMsg: `${expectedKind}素材没有完成真实导入` },
  );
  const added = (await materialIds(workbench)).find((candidate) => !before.has(candidate));
  assert.ok(added, `${expectedKind}素材没有产生完整编号`);
  assert.match(added, new RegExp(`^${UUID_V4_SOURCE}$`));
  const card = await materialCard(workbench, added);
  assert.match(await card.getText(), new RegExp(expectedKind));
  return added;
}

async function materialCard(
  workbench: BrowserElement,
  materialId: string,
): Promise<BrowserElement> {
  const card = workbench.$(
    `//div[contains(@class,'material-library-card')][contains(.,'${materialId}')]`,
  );
  await expect(card).toBeDisplayed();
  return card;
}

async function setSourceState(materialId: string, action: "missing" | "unreadable" | "changed") {
  await browser.tauri.execute(
    ({ core }, arguments_) => core.invoke("set_material_source_state_for_acceptance", arguments_),
    { materialId, action },
  );
}

describe("LE-18 production App material-library acceptance", () => {
  it("imports, previews, recovers and protects real local materials", async () => {
    await waitForStartup();
    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_material_library_for_acceptance"),
    )) as MaterialLibraryPreparation;
    assert.match(preparation.installationId, new RegExp(`^${UUID_V4_SOURCE}$`));

    await openVideoEditing();
    const workbench = await browser.$("section[aria-label='视频剪辑工作区']");
    await expect(workbench).toBeDisplayed();
    await workbench.$("div[role='tab']=素材库").click();
    await browser.waitUntil(async () => (await workbench.getText()).includes("还没有本机素材"));

    const missingMaterialId = await importUniqueMaterial(workbench, "视频");
    const beforeDuplicate = await materialIds(workbench);
    await workbench.$("button=导入本机素材").click();
    await browser.waitUntil(
      async () => (await workbench.getText()).includes("这个文件已经在素材库里"),
      { timeout: 90_000, timeoutMsg: "真实内容摘要没有触发素材去重" },
    );
    assert.deepEqual(await materialIds(workbench), beforeDuplicate);

    const unreadableMaterialId = await importUniqueMaterial(workbench, "音频");
    const changedMaterialId = await importUniqueMaterial(workbench, "图片");
    const referencedMaterialId = await importUniqueMaterial(workbench, "视频");
    const deletableMaterialId = await importUniqueMaterial(workbench, "图片");
    assert.equal((await materialIds(workbench)).length, 5);

    await workbench.$("button=导入本机素材").click();
    await browser.waitUntil(
      async () =>
        !((await workbench.$("button=导入本机素材").getAttribute("class")) ?? "").includes(
          "loading",
        ),
      { timeout: 10_000, timeoutMsg: "取消 picker 后导入按钮没有恢复" },
    );
    assert.equal((await materialIds(workbench)).length, 5);
    assert.doesNotMatch(await workbench.getText(), /素材导入没有完成/);

    const videoCard = await materialCard(workbench, missingMaterialId);
    assert.match(await videoCard.getText(), /视频[\s\S]*320×240[\s\S]*1\.0 秒/);
    assert.match(await videoCard.getText(), /未发现语音/);
    await videoCard.$("button=打开本机预览").click();
    await expect(await videoCard.$("video")).toBeDisplayed();

    const description = "人工确认：蓝色测试画面，可用于片头。";
    const suffix = missingMaterialId.slice(0, 8);
    await videoCard.$(`textarea[aria-label='素材说明 ${suffix}']`).setValue(description);
    await videoCard.$(`button[aria-label='保存说明 ${suffix}']`).click();
    await browser.waitUntil(
      async () => (await workbench.getText()).includes("人工说明已保存"),
      { timeout: 30_000, timeoutMsg: "人工素材说明没有保存" },
    );
    await workbench.$("button[aria-label='刷新素材库']").click();
    await browser.waitUntil(
      async () => (await (await materialCard(workbench, missingMaterialId)).getText()).includes(description),
      { timeout: 30_000, timeoutMsg: "人工素材说明刷新后没有持久化" },
    );
    assert.match(await (await materialCard(workbench, missingMaterialId)).getText(), /人工说明/);

    await workbench.$("div[role='tab']=剪辑项目").click();
    await workbench.$("input[aria-label='剪辑项目标题']").setValue("LE18 素材引用保护");
    await workbench.$("button=创建剪辑项目").click();
    await browser.waitUntil(
      async () => (await workbench.getText()).includes("已创建剪辑项目：LE18 素材引用保护"),
      { timeout: 30_000, timeoutMsg: "真实剪辑项目没有创建" },
    );
    await workbench.$("div[role='tab']=时间轴编辑").click();
    await browser.waitUntil(async () => (await workbench.getText()).includes("尚未保存"));
    await workbench.$("input[aria-label='轨道1片段1素材编号']").setValue(referencedMaterialId);
    await workbench.$("input[aria-label='轨道1片段1时长毫秒']").setValue("1000");
    await workbench.$("input[aria-label='轨道1片段1素材起点毫秒']").setValue("0");
    await workbench.$("button=保存时间轴").click();
    await browser.waitUntil(
      async () => (await workbench.getText()).includes("已保存修订：第 1 版"),
      { timeout: 30_000, timeoutMsg: "真实时间线没有保存素材引用" },
    );

    await workbench.$("div[role='tab']=素材库").click();
    const referencedCard = await materialCard(workbench, referencedMaterialId);
    const referencedSuffix = referencedMaterialId.slice(0, 8);
    await referencedCard.$(`button[aria-label='删除素材 ${referencedSuffix}']`).click();
    await referencedCard.$(`button[aria-label='确认删除素材 ${referencedSuffix}']`).click();
    await browser.waitUntil(
      async () => (await workbench.getText()).includes("暂时不能删除这个素材"),
      { timeout: 30_000, timeoutMsg: "时间线引用没有阻止素材删除" },
    );
    await expect(await materialCard(workbench, referencedMaterialId)).toBeDisplayed();

    const deletableCard = await materialCard(workbench, deletableMaterialId);
    const deletableSuffix = deletableMaterialId.slice(0, 8);
    await deletableCard.$(`button[aria-label='删除素材 ${deletableSuffix}']`).click();
    await deletableCard.$(`button[aria-label='确认删除素材 ${deletableSuffix}']`).click();
    await browser.waitUntil(
      async () =>
        !((await deletableCard.$(`button[aria-label='确认删除素材 ${deletableSuffix}']`).getAttribute("class")) ?? "").includes(
          "loading",
        ),
      { timeout: 30_000, timeoutMsg: "从未引用素材的首次删除没有返回" },
    );
    if (!(await workbench.getText()).includes("素材已从素材库删除")) {
      await deletableCard.$(`button[aria-label='确认删除素材 ${deletableSuffix}']`).click();
      await browser.waitUntil(
        async () => (await workbench.getText()).includes("素材已从素材库删除"),
        { timeout: 30_000, timeoutMsg: "素材本机记录重试后仍未清理" },
      );
    }
    assert.equal((await materialIds(workbench)).includes(deletableMaterialId), false);

    await setSourceState(missingMaterialId, "missing");
    await setSourceState(unreadableMaterialId, "unreadable");
    await setSourceState(changedMaterialId, "changed");
    await workbench.$("button[aria-label='刷新素材库']").click();
    await browser.waitUntil(
      async () => {
        const text = await workbench.getText();
        return (
          text.includes("本机文件不在原位置了") &&
          text.includes("本机文件仍在原位置，但当前无法读取") &&
          text.includes("本机文件已经被替换或修改")
        );
      },
      { timeout: 60_000, timeoutMsg: "三种本机文件失败没有显示不同中文动作" },
    );
  });
});
