import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";
import { openSettings, openVideoEditing, waitForStartup } from "./navigation";

interface MaterialLibraryPreparation {
  readonly installationId: string;
}

type BrowserElement = ReturnType<typeof browser.$>;

const UUID_V4_SOURCE =
  "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}";
const UUID_V4 = new RegExp(`^${UUID_V4_SOURCE}$`);
const UUID_V4_IN_TEXT = new RegExp(UUID_V4_SOURCE, "g");
const PROMPT = "制作一条介绍彩色电视测试图的简短视频。";
const MEASUREMENT_MODES = ["enabled", "disabled"] as const;
const TERMINAL_FAILURES = [
  "可用素材不足",
  "现有素材时长太短",
  "没有找到与描述相符的素材",
  "部分素材当前不可用",
  "生成期间素材发生了变化",
  "时间轴已被更新",
  "暂时无法完成内容生成",
  "本机暂存空间不可用",
  "草稿未能安全保存",
  "草稿已生成但成片失败",
  "智能剪辑当前不可用",
] as const;

async function workbench(): Promise<BrowserElement> {
  const value = await browser.$("section[aria-label='视频剪辑工作区']");
  await expect(value).toBeDisplayed();
  return value;
}

async function configureScriptModel(apiKey: string): Promise<void> {
  await openSettings();
  const section = await browser.$(".model-service-purpose--script");
  await expect(section).toBeDisplayed();
  const input = await section.$("input[aria-label='文案模型服务 API Key']");
  await input.setValue(apiKey);
  await section.$("button=保存配置").click();
  await browser.waitUntil(
    async () => (await section.getText()).includes("已配置"),
    { timeout: 30_000, timeoutMsg: "文案模型服务没有通过真实设置页保存" },
  );
  assert.equal(await input.getValue(), "");
  assert.equal((await browser.$("body").getText()).includes(apiKey), false);
}

async function waitForSmartResult(
  value: BrowserElement,
  expectedRevision: number,
  requireUnderstanding: boolean,
): Promise<void> {
  let sawUnderstanding = false;
  await browser.waitUntil(
    async () => {
      const text = await value.getText();
      if (text.includes("正在理解素材")) {
        sawUnderstanding = true;
      }
      const failure = TERMINAL_FAILURES.find((copy) => text.includes(copy));
      if (failure !== undefined) {
        throw new Error(`正式智能剪辑失败：${failure}`);
      }
      return (
        text.includes(`当前修订：第 ${expectedRevision} 版`) ||
        text.includes(`时间轴第 ${expectedRevision} 版`)
      );
    },
    {
      timeout: 900_000,
      interval: 1_000,
      timeoutMsg: `智能剪辑没有生成第 ${expectedRevision} 版时间轴`,
    },
  );
  if (requireUnderstanding) {
    assert.equal(sawUnderstanding, true, "用户没有看到真实素材理解进度");
  }
}

function measurementRequest(): {
  readonly enableThinking: boolean;
  readonly materialCount: number;
} | null {
  const mode = process.env.AUTOMATION_TOOL_LE24_MEASURE_THINKING;
  const rawMaterialCount = process.env.AUTOMATION_TOOL_LE24_MATERIAL_COUNT;
  if (mode === undefined && rawMaterialCount === undefined) {
    return null;
  }
  assert.ok(
    MEASUREMENT_MODES.includes(mode as (typeof MEASUREMENT_MODES)[number]),
    "LE-24 thinking mode must be enabled or disabled",
  );
  assert.match(rawMaterialCount ?? "", /^[1-3]$/);
  return {
    enableThinking: mode === "enabled",
    materialCount: Number(rawMaterialCount),
  };
}

async function materialIds(editing: BrowserElement): Promise<readonly string[]> {
  return Array.from(new Set((await editing.getText()).match(UUID_V4_IN_TEXT) ?? []));
}

async function importControlledMaterials(
  editing: BrowserElement,
  materialCount: number,
): Promise<void> {
  await editing.$("div[role='tab']=素材库").click();
  for (let index = 0; index < materialCount; index += 1) {
    const before = new Set(await materialIds(editing));
    await editing.$("button=导入本机素材").click();
    await browser.waitUntil(
      async () =>
        (await editing.getText()).includes("素材已导入本机素材库。") &&
        (await materialIds(editing)).length > before.size,
      { timeout: 120_000, timeoutMsg: `第 ${index + 1} 条受控图片没有导入` },
    );
  }
  assert.equal((await materialIds(editing)).length, materialCount);
  assert.match(await editing.getText(), /图片/);
}

async function createEditingProject(
  editing: BrowserElement,
  title: string,
): Promise<void> {
  await editing.$("div[role='tab']=剪辑项目").click();
  await editing.$("input[aria-label='剪辑项目标题']").setValue(title);
  await editing.$("button=创建剪辑项目").click();
  await browser.waitUntil(
    async () => (await editing.getText()).includes(`已创建剪辑项目：${title}`),
    { timeout: 30_000, timeoutMsg: "真实 Control Plane 没有创建剪辑项目" },
  );
}

async function runThinkingMeasurement(
  apiKey: string,
  request: NonNullable<ReturnType<typeof measurementRequest>>,
): Promise<void> {
  await openVideoEditing();
  const editing = await workbench();
  await importControlledMaterials(editing, request.materialCount);
  await createEditingProject(editing, `LE24 计时 ${request.materialCount}`);
  await configureScriptModel(apiKey);
  await openVideoEditing();
  const configured = await workbench();
  await configured.$("div[role='tab']=智能剪辑").click();
  await configured.$("textarea[aria-label='一句话描述成片']").setValue(PROMPT);
  const thinking = await configured.$("[role='switch'][aria-label='深度思考']");
  if (request.enableThinking) {
    await thinking.click();
  }
  assert.equal(
    await thinking.getAttribute("aria-checked"),
    String(request.enableThinking),
  );

  const startedAt = performance.now();
  await configured.$("button=生成草稿").click();
  await waitForSmartResult(configured, 1, true);
  const elapsedMs = Math.round(performance.now() - startedAt);
  assert.ok(elapsedMs > 0 && elapsedMs <= 900_000);
  console.log(
    "LE24_MEASUREMENT " +
      JSON.stringify({
        schemaVersion: 1,
        enableThinking: request.enableThinking,
        materialCount: request.materialCount,
        elapsedMs,
      }),
  );
}

describe("LE-19 production App smart-edit acceptance", () => {
  it("imports material, exposes configuration failure, then drafts and renders", async function () {
    this.timeout(1_500_000);
    const apiKey = process.env.AUTOMATION_TOOL_LE19_MODEL_KEY;
    assert.ok(apiKey, "LE-19 acceptance needs a real model key");

    await waitForStartup();
    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_material_library_for_acceptance"),
    )) as MaterialLibraryPreparation;
    assert.match(preparation.installationId, UUID_V4);

    const measurement = measurementRequest();
    if (measurement !== null) {
      await runThinkingMeasurement(apiKey, measurement);
      return;
    }

    await openVideoEditing();
    const editing = await workbench();
    await importControlledMaterials(editing, 1);
    await createEditingProject(editing, "LE19 正式智能剪辑");

    await editing.$("div[role='tab']=智能剪辑").click();
    await editing.$("textarea[aria-label='一句话描述成片']").setValue(PROMPT);
    await editing.$("button=生成草稿").click();
    await browser.waitUntil(
      async () => (await editing.getText()).includes("智能剪辑尚未配置完成"),
      { timeout: 60_000, timeoutMsg: "缺失模型配置没有形成固定用户失败" },
    );
    assert.match(await editing.getText(), /请先到设置中完成服务配置后重试/);

    await configureScriptModel(apiKey);
    await openVideoEditing();
    const configured = await workbench();
    await configured.$("div[role='tab']=剪辑项目").click();
    await configured.$("button=打开时间轴编辑").click();
    await configured.$("div[role='tab']=智能剪辑").click();
    await configured.$("textarea[aria-label='一句话描述成片']").setValue(PROMPT);
    assert.equal(
      await configured.$("textarea[aria-label='一句话描述成片']").getValue(),
      PROMPT,
    );

    const draft = await configured.$("button=生成草稿");
    assert.equal(await draft.isEnabled(), true, "返回后没有重新打开现有剪辑项目");
    await draft.click();
    await browser.waitUntil(
      async () => {
        const text = await configured.getText();
        return (
          /正在(?:准备|理解素材|整理文案|生成旁白|匹配画面|选择片段|保存结果)/.test(
            text,
          ) || text.includes("当前修订：第 1 版")
        );
      },
      { timeout: 30_000, timeoutMsg: "生成草稿按钮没有启动真实智能剪辑" },
    );
    await waitForSmartResult(configured, 1, true);
    await configured.$("div[role='tab']=智能剪辑").click();
    await expect(configured).toHaveText(
      expect.stringContaining("草稿已生成并放入时间轴，可以继续调整和保存。"),
    );

    await configured.$("button=一键直出片").click();
    await waitForSmartResult(configured, 2, false);
    await configured.$("div[role='tab']=智能剪辑").click();
    await expect(configured).toHaveText(
      expect.stringContaining("草稿已生成，成片任务正在排队。"),
    );
    await configured.$("div[role='tab']=提交与任务").click();
    await browser.waitUntil(
      async () => {
        await configured.$("button=刷新任务").click();
        const text = await configured.getText();
        if (text.includes("剪辑失败")) {
          throw new Error("一键直出片提交的正式剪辑任务失败");
        }
        return text.includes("已完成") && text.includes("成片已入库");
      },
      {
        timeout: 240_000,
        interval: 1_000,
        timeoutMsg: "一键直出片没有发布真实成片",
      },
    );
  });
});
