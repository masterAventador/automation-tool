import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

test("LE-22 package journey uses one speech material and the formal editing UI", async () => {
  const spec = await readFile(
    new URL("e2e-tauri/smart-edit-package.spec.ts", frontendRoot),
    "utf8",
  );

  for (const required of [
    "check_local_startup_environment",
    "prepare_material_library_for_acceptance",
    "AUTOMATION_TOOL_LE22_MODEL_KEY",
    "导入本机素材",
    "有语音",
    "QUILTER",
    "APOSTLE",
    "生成草稿",
    "当前修订：第 1 版",
    "原声轨道",
    "提交剪辑任务",
    "刷新任务",
    "成片已入库",
  ]) {
    assert.ok(spec.includes(required), `LE-22 package spec is missing ${required}`);
  }
  assert.doesNotMatch(spec, /一键直出片/u);
  assert.doesNotMatch(spec, /sessionStorage|localStorage|mock|stub/iu);
  assert.match(spec, /assert\.doesNotMatch\([^;]+旁白轨道/su);
});
