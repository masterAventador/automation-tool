import assert from "node:assert/strict";

import { browser, expect } from "@wdio/globals";
import { openSettings, openVideoEditing, waitForStartup } from "./navigation";

interface MaterialLibraryPreparation {
  readonly installationId: string;
}

interface LocalStartupEnvironment {
  readonly appData: "ready" | "unavailable";
  readonly executor: "ready" | "configuration_required" | "unavailable";
  readonly embeddedBrowser:
    | "ready"
    | "component_missing"
    | "component_damaged"
    | "version_incompatible";
}

type BrowserElement = ReturnType<typeof browser.$>;

const UUID_V4_SOURCE =
  "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}";
const UUID_V4 = new RegExp(`^${UUID_V4_SOURCE}$`);
const UUID_V4_IN_TEXT = new RegExp(UUID_V4_SOURCE, "g");
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
  "智能剪辑当前不可用",
] as const;

async function workbench(): Promise<BrowserElement> {
  const value = await browser.$("section[aria-label='视频剪辑工作区']");
  await expect(value).toBeDisplayed();
  return value;
}

async function materialIds(value: BrowserElement): Promise<readonly string[]> {
  return Array.from(new Set((await value.getText()).match(UUID_V4_IN_TEXT) ?? []));
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
    { timeout: 30_000, timeoutMsg: "正式包没有保存文案模型配置" },
  );
  assert.equal(await input.getValue(), "");
  assert.equal((await browser.$("body").getText()).includes(apiKey), false);
}

async function importSpeechMaterial(value: BrowserElement): Promise<string> {
  await value.$("div[role='tab']=素材库").click();
  const before = new Set(await materialIds(value));
  await value.$("button=导入本机素材").click();
  await browser.waitUntil(
    async () =>
      (await value.getText()).includes("素材已导入本机素材库。") &&
      (await materialIds(value)).length > before.size,
    { timeout: 180_000, timeoutMsg: "正式包没有导入受控人声视频" },
  );
  const added = (await materialIds(value)).find((candidate) => !before.has(candidate));
  assert.ok(added, "正式包导入没有产生素材编号");
  assert.match(added, UUID_V4);
  return added;
}

async function createProject(value: BrowserElement): Promise<void> {
  await value.$("div[role='tab']=剪辑项目").click();
  await value.$("input[aria-label='剪辑项目标题']").setValue("LE22 正式包原声成片");
  await value.$("button=创建剪辑项目").click();
  await browser.waitUntil(
    async () => (await value.getText()).includes("已创建剪辑项目：LE22 正式包原声成片"),
    { timeout: 30_000, timeoutMsg: "正式包没有创建剪辑项目" },
  );
}

async function waitForDraft(value: BrowserElement): Promise<void> {
  let sawUnderstanding = false;
  await browser.waitUntil(
    async () => {
      const text = await value.getText();
      if (text.includes("正在理解素材")) {
        sawUnderstanding = true;
      }
      const failure = TERMINAL_FAILURES.find((copy) => text.includes(copy));
      if (failure !== undefined) {
        throw new Error(`正式包智能剪辑失败：${failure}`);
      }
      return text.includes("当前修订：第 1 版");
    },
    {
      timeout: 900_000,
      interval: 1_000,
      timeoutMsg: "正式包没有生成第 1 版原声时间轴",
    },
  );
  assert.equal(sawUnderstanding, true, "正式包没有展示素材理解进度");
}

async function assertSpeechWriteback(
  value: BrowserElement,
  materialId: string,
): Promise<void> {
  await value.$("div[role='tab']=素材库").click();
  await value.$("button[aria-label='刷新素材库']").click();
  const card = value.$(
    `//div[contains(@class,'material-library-card')][contains(.,'${materialId}')]`,
  );
  await browser.waitUntil(
    async () => {
      const text = await card.getText();
      return /有语音/.test(text) && /QUILTER/i.test(text) && /APOSTLE/i.test(text);
    },
    { timeout: 60_000, timeoutMsg: "正式包没有回写真实人声转写" },
  );
}

describe("LE-22 installed package smart-edit acceptance", () => {
  it("imports speech, drafts original audio, renders and publishes one Artifact", async function () {
    this.timeout(1_500_000);
    const apiKey = process.env.AUTOMATION_TOOL_LE22_MODEL_KEY;
    assert.ok(apiKey, "LE-22 package acceptance needs a real model key");

    const startup = (await browser.tauri.execute(({ core }) =>
      core.invoke("check_local_startup_environment"),
    )) as LocalStartupEnvironment;
    assert.deepEqual(startup, {
      appData: "ready",
      executor: "ready",
      embeddedBrowser: "ready",
    });
    await waitForStartup();
    const preparation = (await browser.tauri.execute(({ core }) =>
      core.invoke("prepare_material_library_for_acceptance"),
    )) as MaterialLibraryPreparation;
    assert.match(preparation.installationId, UUID_V4);

    await configureScriptModel(apiKey);
    await openVideoEditing();
    const editing = await workbench();
    const materialId = await importSpeechMaterial(editing);
    await createProject(editing);

    await editing.$("div[role='tab']=智能剪辑").click();
    await editing
      .$("textarea[aria-label='一句话描述成片']")
      .setValue("保留素材中人物的原声，制作一条简短视频。");
    await editing.$("button=生成草稿").click();
    await waitForDraft(editing);
    await assertSpeechWriteback(editing, materialId);

    await editing.$("div[role='tab']=时间轴编辑").click();
    await browser.waitUntil(
      async () => (await editing.getText()).includes("原声轨道"),
      { timeout: 30_000, timeoutMsg: "正式包时间轴没有原声轨道" },
    );
    assert.doesNotMatch(await editing.getText(), /旁白轨道/);

    await editing.$("div[role='tab']=提交与任务").click();
    await editing.$("button=提交剪辑任务").click();
    await browser.waitUntil(
      async () => (await editing.getText()).includes("已提交剪辑任务，正在排队。"),
      { timeout: 30_000, timeoutMsg: "正式包没有提交原声成片任务" },
    );
    await browser.waitUntil(
      async () => {
        await editing.$("button=刷新任务").click();
        const text = await editing.getText();
        if (text.includes("剪辑失败")) {
          throw new Error("正式包原声成片任务失败");
        }
        return text.includes("已完成") && text.includes("成片已入库");
      },
      {
        timeout: 300_000,
        interval: 1_000,
        timeoutMsg: "正式包没有发布原声成片",
      },
    );
  });
});
