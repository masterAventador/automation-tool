import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * 正式装配必须把每个真实网关都传下去。
 *
 * PB-07 交付了 `TauriPublishWorkspaceGateway`，也在真实 Tauri App 上验收过——但那次
 * 验收走的是 `core.invoke("get_publish_workspace")`，**直接调 Tauri Command，绕过了
 * 界面装配**。Command 通了，`main.tsx` 却从没把这个网关传给工作台，于是
 * `WorkbenchShell` 回落到那个四个方法全部 throw 的占位网关：正式 App 里点开「作品发布」
 * 必然显示"暂时读不到发布状态"。
 *
 * 单元测试和 UI Harness 都发现不了这一层——它们各自注入自己的替身，替身当然是通的。
 * 所以这里直接读 `main.tsx` 的源码，核对每个已交付的 Tauri 网关都被装配进去了。
 * 这是个粗糙的判据，但它管的正是"两边各自都对、接缝上漏了"这类问题。
 */

// jsdom 环境下 `import.meta.url` 不是 file: scheme，只能从 vitest 的工作目录解析。
const MAIN = resolve("src/main.tsx");

/** 已交付、且工作台确实需要的 Tauri 网关。 */
const REQUIRED_GATEWAYS = [
  "TauriTaskCreationGateway",
  "TauriTaskRunControlGateway",
  "TauriTaskDiscoveryGateway",
  "TauriWorkbenchGateway",
  "TauriPlatformSessionGateway",
  "TauriStartupEnvironmentGateway",
  "TauriAppUpdateGateway",
  "TauriModelServiceGateway",
  "TauriVideoEditingServiceGateway",
  "TauriMaterialVideoStudioGateway",
  "TauriPublishWorkspaceGateway",
] as const;

describe("production wiring", () => {
  const source = readFileSync(MAIN, "utf8");

  it.each(REQUIRED_GATEWAYS)("%s is constructed in main.tsx", (gateway) => {
    expect(source).toContain(`new ${gateway}(`);
  });

  it("hands the publish workspace gateway to the workbench", () => {
    // 光构造出来不算数——不传下去，工作台照样回落到会抛错的占位网关。
    expect(source).toMatch(/publishWorkspaceGateway=\{publishWorkspaceGateway\}/);
  });
});
