# FIX 启动门禁的前端那一半没有任何门禁看着

> 状态：🔍 待验收（门禁已落地并通过两轮变异检验；**两个既有桩本次未拆除**，只是从隐形变成登记在册，
> 拆除条件见「遗留项」）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：`docs/development/T36-oneshot-video.md` 的一句话生成验收连撞四道门后，对整批桌面 E2E 入口
> 做去留调研时查出的。承接 `FIX-startup-gate-build-fork.md`（消除 Rust 侧七处分叉）与
> `FIX-control-plane-e2e-prerequisites.md`（补齐 control-plane 层启动前置）。

## 缺陷

启动门禁有两半，只有一半是 Rust 写的：

- **Rust 半**：`check_local_startup_environment` 回答「本机环境行不行」；
- **前端半**：入口模块挂载的那个 `StartupCheck` 决定**要不要问**。

`frontend/vite.config.ts` 按 build mode 替换 `/src/main.tsx`，所以**一个构建可以不写一行 `#[cfg]`
就把整道门禁绕过去**——`single_build_path.rs` 只读 Rust 源码，对此完全隐形。

这不是假设。`frontend/src/app/startup.ts:44` 至今是：

```ts
export const desktopShellStartupCheck: StartupCheck = {
  async check() {
    return { status: "ready" };
  },
};
```

`frontend/src/test-tauri-main.tsx` 挂的就是它。`frontend/src/test-browser-settings-main.tsx:15-19`
自带同型的第二份。**这与 `780abce` 花一个任务删掉的 Rust 桩是同一件事、同一个位置、同一个后果，
只是换了一门语言。**

### 影响面（调研实测，不是推断）

决定一个桌面 E2E 入口是否真的跑门禁的变量**不是 Cargo feature，是 Vite mode 换掉的前端入口**：

| 前端入口 | 谁在用 | 真启动门禁 | 真账号门禁 |
| --- | --- | --- | --- |
| `main.tsx`（真） | 生产 | ✅ | ✅ |
| `test-production-main.ts` → `main.tsx` | `model-service-e2e` mode：VF-05、以及 `video-studio-e2e.conf` | ✅ | ✅ |
| `test-control-plane-main.ts` → `main.tsx` | `control-plane-e2e` mode：32 个驱动 | ✅ | ✅ |
| **`test-tauri-main.tsx`（桩）** | `desktop-e2e` mode：`pnpm test:tauri`、update-policy、update-download、update-installation、diagnostic-export | ❌ | ❌ |
| **`test-browser-settings-main.tsx`（桩）** | B5-04 | ❌ | ❌ |

也就是说：`docs/development/desktop-e2e-run-20260726.md` 里 Batch A 那 5 个「通过」，
**是因为它们的前端根本没跑门禁**，不是因为它们健康。它们无法发现包内缺依赖——
而「验收全绿、用户打开正式包不可用」正是这条产品线已经出过的事故。

顺带纠正 `T36-oneshot-video.md` 的一处结论（由该任务自行修订）：`04fb3f7` 记的
「顺带解开的入口」列了 browser-settings / diagnostic-export / update-* / `pnpm test:tauri`。
**这四类从来没被账号门禁挡过**——`App.tsx:130-134` 在 `accountSessionGateway === undefined` 时
根本不挂载 `AccountSessionGate`，而桩入口不传这个网关。账号门禁只影响挂载真 `main.tsx` 的构建。

## RED

新增 `no_frontend_entrypoint_declares_the_environment_ready_without_probing`，
先把评审清单置空跑一次——**扫描器必须自己把桩找出来，不能靠清单提示**：

```text
cargo test --test single_build_path no_frontend_entrypoint

assertion `left == right` failed: the set of frontend entries whose startup check
reports ready without probing changed. ...
  left: {"test-browser-settings-main.tsx", "test-tauri-main.tsx"}
 right: {}
```

两个桩被逐字点名，其余三个入口（含两个只写 `import "./main"` 的转发入口）被正确判为「会探测」。

## GREEN

```text
cd frontend/src-tauri && cargo test --test single_build_path
  running 10 tests ... test result: ok. 10 passed; 0 failed        (原 9 条 → 10 条)
```

## 变异检验（两轮，其中第一轮当场打穿了门禁初版）

### 变异 1：把生产入口的启动检查改成无条件 ready

在 `createDesktopStartupCheck` 的 `check()` 顶部插一行 `return { status: "ready" };`。

**门禁初版没有拦住它。** 初版的判据是「函数体里 await 了什么就算探测过」，而提前 return 之后
那些 `await` 全变成不可达代码，文本上照样在。**这正是没有变异检验就发现不了的形态。**

判据据此改掉，改成——**能不能在第一次 await 之前就答出 ready**：

```rust
fn reports_ready_without_probing(check_body: &str) -> bool {
    let Some(ready) = check_body.find(FRONTEND_READY_ANSWER) else { return false; };
    check_body.find("await ").is_none_or(|awaited| ready < awaited)
}
```

改完同一个变异被拦下：

```text
main.tsx mounts a startup check that can answer ready before awaiting anything;
that is the production gate, and no review can excuse it
```

顺带调整了断言顺序：生产入口那条必须排在「防空转」那条**之前**。因为生产检查一旦被桩掉，
所有转发到它的入口同时变成桩，`probing` 集合清空，防空转断言会先开火并报成「扫描器坏了」——
把本文件覆盖的最坏情况误报成工具故障。

### 变异 2：让一个原本会探测的入口变成第三个桩

把 `test-control-plane-main.ts` 的 `import "./main"` 换成自带的无条件 ready 检查：

```text
  left: {"test-browser-settings-main.tsx", "test-control-plane-main.ts", "test-tauri-main.tsx"}
 right: {"test-browser-settings-main.tsx", "test-tauri-main.tsx"}
```

两轮变异全部还原，`git status` 复核过：本次只改了 `tests/single_build_path.rs`。

## 交付

- **入口清单从 `vite.config.ts` 派生，不手工维护**：扫描 `"/src/…"` 字面量，新增一个 build mode
  会自动进入门禁覆盖范围。**这一条是有意的**——本仓库刚刚才因为「守卫的输入集合看不见目标」
  出过事（见「遗留项」第 2 条），清单手写就会重演。
- **跟随一层间接**：入口自带的常量、从 `app/startup.ts` 导入的常量、在那里被调用的工厂、
  以及 `import "./main"` 的转发，四种形态都能解析到真正的 `check()` 体。
  **任何解析不了的形态一律 panic，不是「判为不是桩」**——一个悄悄放弃的扫描器
  产出的结果和干净代码库一模一样，那正是本文件要消灭的东西。
- **防空转断言**：必须至少有一个入口被判为「会探测」，否则门禁自己报「本次什么都没证明」。
- **生产入口永不可评审**：`main.tsx` 既不允许出现在桩集合里，也不允许出现在评审清单里，
  两条分别断言。
- **集合相等**：增一个失败、删一个也失败。既有两个桩带着「为什么还在 + 谁负责拆」进清单。

## 本次**没有**做的事（诚实划线）

**两个桩一个都没拆。** 拆 `test-tauri-main.tsx` 意味着把 `pnpm test:tauri` 与 update-policy /
update-download / update-installation / diagnostic-export 五个入口切到真门禁上，
它们会立刻需要**可达的 Control Plane + 装配好的内置浏览器 + 编译期动作信任三元组**
（`StartupGate` 只在 `status === "ready"` 时挂载 children）。那是接线工作，Demo 前一周不做。

**本次交付的是「让它不再隐形」，不是「让它消失」。** 不把门禁落地说成缺陷已修。

## 失败矩阵

| 场景 | 行为 |
| --- | --- |
| 生产入口的启动检查被改成无条件 ready | 点名 `main.tsx`，明说没有评审能豁免（变异 1 实测） |
| 某个测试入口新增无条件 ready 桩 | 集合相等失败并逐字点名新增项（变异 2 实测） |
| 提前 return 绕过后面的 await | 判据按「第一次 await 之前能否答出 ready」，拦得住（变异 1 实测） |
| 评审清单里删掉一个仍然存在的桩 | 集合相等失败（RED 段实测，空清单点名两个） |
| 有人把 `main.tsx` 加进评审清单 | 独立断言拒绝 |
| 新增 build mode 及其入口 | 从 `vite.config.ts` 派生，自动纳入；不需要改门禁 |
| 入口挂的检查解析不出来 | panic 并说明是哪个模块的哪个绑定，不静默判为安全 |
| 扫描器读错文件 / 解析退化 | 入口数 < 5、找不到 `main.tsx`、解析不到 `check()`、无任何探测入口，四条各自 panic |

## 清理

未启动 App、Control Plane、Docker、浏览器或任何常驻服务。未新增临时文件（变异用的备份放在
会话 scratchpad，未进仓库）。未触碰 `~/Library/Application Support/com.aventador.automationtool/`、
`.local/`、`automation-tool-t36-*` 的容器与进程。构建产物落在 `frontend/src-tauri/target/`（已被忽略）。

两轮变异临时改过 `frontend/src/app/startup.ts` 与 `frontend/src/test-control-plane-main.ts`，
均以 `git checkout --` 还原并复核；本次提交只含 `tests/single_build_path.rs` 与本文件。
未触碰其他工作线占用的 `src/lib.rs`、`motion_video_studio.rs`、`features/video-studio/`、
`tauri*.conf.json` 与 `scripts/` 下被占用的驱动。

## 文档

- `frontend/src-tauri/tests/single_build_path.rs`（新增 1 条断言 + 解析器，9 → 10 条）
- 本文件

## 遗留项（下一条工作线的输入）

| 项 | 状态 |
| --- | --- |
| 拆掉 `test-tauri-main.tsx` 的桩：5 个入口切到真门禁，需接 `desktop_e2e_prerequisites` | 未做，Demo 后 |
| 拆掉 `test-browser-settings-main.tsx`：B5-04 的用户路径已被 EB-10 按产品规则删除，应随该验收一起退役 | 未做，属 B5-04 的退役决定 |
| **`video-studio-e2e` 这个 feature 在产品源码里已经不门控任何一行**（`grep -rn 'video-studio-e2e' frontend/src-tauri/src/` 只剩一句注释），是 `desktop-e2e` 的纯别名，而且它的 tauri conf 用的是 `model-service-e2e` 的 vite mode。留着会让人以为存在第三种构建 | 未做，属那批入口的并/废决定 |
| **`scripts/test_desktop_e2e_prerequisites.py` 的驱动清单派生规则是「`package.json` 里命令含 `control-plane-e2e`」**（`:219`）。视频线的构建 feature 叫 `video-studio-e2e`，**字符串对不上，于是整批被排除在门禁的输入集合之外**。不是有人跳过，是清单派生规则天然看不见它们——与本文件修的是同一类病：守卫的输入集合看不见目标 | 未做，随视频线接线一并处理 |
| 前端还有没有别的「按 build mode 换掉生产装配」的地方（`vite.config.ts` 只查了 entry 替换，其余 mode 行为未审） | 未查 |
