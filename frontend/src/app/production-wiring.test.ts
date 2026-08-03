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
 * 所以这里直接读 `main.tsx` 的源码，核对装配。
 *
 * 第一版只检查「有没有 new 出来」，随后的全量装配审计指出它三个洞：漏了六个已构造的
 * 网关、只有 publish 一项检查了「传下去」、以及最要命的——**完全不检查传下去的是不是
 * 真网关**。VE-01～VE-08 八项标记已完成，`videoEditingGateway` 传的却是
 * `createLocalVideoEditingGateway(window.sessionStorage)`，一个关掉 App 就清空、提交
 * 永远失败的浏览器草稿壳，而这个测试当时全绿。
 *
 * 所以现在的判据是：**每个网关 prop 传下去的那个变量，必须绑定到 `new Tauri…()`**。
 */

// jsdom 环境下 `import.meta.url` 不是 file: scheme，只能从 vitest 的工作目录解析。
const MAIN = resolve("src/main.tsx");
const source = readFileSync(MAIN, "utf8");
const app = readFileSync(resolve("src/app/App.tsx"), "utf8");
const shell = readFileSync(resolve("src/app/WorkbenchShell.tsx"), "utf8");
const operations = readFileSync(
  resolve("src/features/operations/OperationsWorkspace.tsx"),
  "utf8",
);
const harness = readFileSync(resolve("src/test-harness/main.tsx"), "utf8");

/** `const x = new TauriFoo(...)` → x ↦ TauriFoo */
function tauriBindings(text: string): ReadonlyMap<string, string> {
  const bindings = new Map<string, string>();
  const pattern = /\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*(?:[^;]*?\b)?new\s+(Tauri[\w$]*)\s*\(/gu;
  for (const match of text.matchAll(pattern)) {
    bindings.set(match[1]!, match[2]!);
  }
  return bindings;
}

/**
 * JSX 里传下去的装配位 → 它绑定的变量名。
 *
 * 三种写法都算传下去了：`foo={bar}`、简写 `{foo}`（出现在条件展开里），以及
 * `startupCheck={startupCheck}` 这种不以 Gateway 结尾的。所以这里不按名字后缀过滤，
 * 由调用方决定关心哪些位置。
 */
function wiredProps(text: string): ReadonlyMap<string, string> {
  const wired = new Map<string, string>();
  const body = text.slice(text.indexOf("createRoot("));
  for (const match of body.matchAll(/\b([A-Za-z_$][\w$]*)=\{([A-Za-z_$][\w$]*)\}/gu)) {
    wired.set(match[1]!, match[2]!);
  }
  // `{...(x === undefined ? {} : { x })}`：条件装配的简写形式，同样是传下去了。
  for (const match of body.matchAll(/\{\s*([A-Za-z_$][\w$]*)\s*\}\s*\)\s*\}/gu)) {
    wired.set(match[1]!, match[1]!);
  }
  return wired;
}

const BINDINGS = tauriBindings(source);
const WIRED = wiredProps(source);

/**
 * 必须由真实 Tauri 网关驱动的装配位。
 *
 * 这张表不是手抄的清单——每加一个 Tauri 网关就该加一行，遗漏会被下面
 * 「每个 new 出来的网关都得用上」那条兜住。
 */
const REQUIRED_TAURI_PROPS = [
  "taskCreationGateway",
  "taskRunControlGateway",
  "taskDiscoveryGateway",
  "workbenchGateway",
  "platformSessionGateway",
  "appUpdateGateway",
  "modelServiceGateway",
  "bilibiliServiceGateway",
  "materialVideoStudioGateway",
  "publishWorkspaceGateway",
  "videoEditingGateway",
  "smartEditGateway",
] as const;

function requireRealTauriGateway(prop: string): void {
  const binding = WIRED.get(prop);
  expect(binding, `${prop} is never handed to the workbench`).toBeDefined();
  // 光传下去不够：传的必须是 `new Tauri…()` 的产物，而不是本地壳子或占位实现。
  expect(
    BINDINGS.get(binding!),
    `${prop} is wired to \`${binding}\`, which is not constructed from a Tauri gateway`,
  ).toMatch(/^Tauri/u);
}

describe("production wiring", () => {
  it.each(REQUIRED_TAURI_PROPS)("%s is handed a real Tauri gateway", (prop) => {
    requireRealTauriGateway(prop);
  });

  it("carries the real smart-edit gateway through every production layer", () => {
    expect(app).toMatch(/<WorkbenchShell[\s\S]*smartEditGateway=\{smartEditGateway\}/u);
    expect(shell).toMatch(/<CreationHub[\s\S]*smartEditGateway=\{smartEditGateway\}/u);
    expect(operations).toMatch(
      /<VideoEditingWorkbench[\s\S]*smartEditGateway=\{smartEditGateway\}/u,
    );
    expect(harness).toMatch(
      /const smartEditGateway = new TestHarnessSmartEditGateway\(\)/u,
    );
  });

  /**
   * 发布页的成片来源必须真的有源头。
   *
   * `selectedVideo` 不是 `main.tsx` 传的网关，上面那些判据一条都覆盖不到它——它是
   * `WorkbenchShell` 自己的状态。PB-07 的病根就是这一类：prop 通道从 `main.tsx` 一路
   * 通到 `PublishWorkspace`，每一层都把它原样透传，而**没有任何东西往里灌值**，于是正式
   * App 里「发布到抖音」这个按钮的渲染条件永远不成立。单元测试和 UI Harness 各自塞一个
   * 测试替身进去，两边都绿。
   */
  it("lets the operator get back to choosing a video", () => {
    // 选错了要能换。没有这条，选中的成片就是个死结。
    expect(shell).toMatch(/onChangeSelection=\{[A-Za-z_$][\w$]*\}/u);
    expect(shell, "the shell must own the selection, not just forward one").toMatch(
      /useState<SelectedVideo \| undefined>/u,
    );
  });

  /**
   * 成片页必须有「去发布」，而且它得真的把成片写进选择状态。
   *
   * 交接的接收端（`WorkbenchShell` 持有选中成片、`PublishWorkspace` 渲染它、Rust 按
   * `artifactId` 取件）早就就位，唯独发起端那一个按钮长期缺席：`WorkbenchShell` 渲染
   * `<VideoStudio>` 时不传 `onPublishArtifact`，于是做完一条视频就没有下一步。这和 PB-07
   * 是同一类病——通道每层都在，只是没有任何东西往里灌值。
   */
  it("hands the finished-videos page a way to send one on to publishing", () => {
    const handoff = /<CreationHub[^>]*onPublishArtifact=\{([A-Za-z_$][\w$]*)\}/u.exec(shell);
    expect(handoff, "CreationHub is never given a publish handoff").not.toBeNull();
    expect(
      operations,
      "CreationHub does not forward its publish handoff to VideoStudio",
    ).toMatch(/<VideoStudio[^>]*onPublishArtifact=\{onPublishArtifact\}/u);
    // 而且那个回调必须真的把成片写进选择状态，不能只是切页。
    expect(shell).toMatch(new RegExp(`const ${handoff![1]!}[^;]*setSelectedVideo\\(`, "u"));
  });

  it("hands every constructed Tauri gateway to something", () => {
    // 反方向：构造了却没人用，等于没接。PB-07 是连构造都没有，这条防的是它的近亲。
    const consumed = new Set(WIRED.values());
    const orphans = [...BINDINGS.entries()]
      .filter(([name]) => !consumed.has(name))
      .map(([name, type]) => `${name} (${type})`);
    expect(orphans, "constructed but never wired").toEqual([]);
  });
});
