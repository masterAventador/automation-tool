# FIX：搬走浏览器之后留下的两处「安静跳过」

用户可操作：否

证据类型：分层实现

> 日期：2026-08-05
>
> 提交：本文件所在提交
>
> 类型：独立缺陷修复（不改任何 roadmap 任务状态）

## 缺陷

`FIX-browser-archives-into-machine-cache.md` 与 `FIX-shared-embedded-browser-staging-cache.md`
把归档和解包树都收进机器级缓存，这是对的。但两处消费方没跟着走，而**两处的失败形态都是
「看起来通过」**：

### 一、`test_embedded_browser_staging_cache.py` 写死了 `macos-arm64`

四条测试直接写 `ensure_staged_browser(target_id="macos-arm64")`。在 Windows 宿主上——
也就是出 Windows 包的那台——那个归档永远不会存在，于是它们不是 skip，是 **error**：

```text
EmbeddedBrowserStagingUnavailable: the locked Chromium archive is not downloaded yet:
  C:\Users\Aventador\AppData\Local\automation-tool-build\embedded-browser-archives\chrome-mac-arm64.zip
Ran 6 tests — FAILED (errors=4)
```

于是「这套测试在这台机器上不适用」和「共享缓存坏了」是同一个红。而缓存本身按构造就是
跨平台的（一个 `ensure_staged_browser`、一份契约、一个 target id）。

### 二、`motion-render-static-frames.test.mjs` 指着一个已被删掉的目录

它的三条候选路径全是 `.local/` 相对且全是 macOS 目标，其中一条正是
`FIX-shared-embedded-browser-staging-cache.md` 清理掉的
`.local/desktop-e2e/embedded-browser/...`。找不到时 `locateRenderBrowser()` 返回 `null`，
然后 7 处 `t.skip("no staged embedded Chromium on this machine")`。

**它不会报错，它会安静地全部跳过。** 实测：

```text
ℹ tests 8   ℹ pass 0   ℹ fail 0   ℹ skipped 7
```

七条驱动真实 Worker + 真实 Chromium 的渲染测试，一条都没在跑，而输出没有任何红色。

## RED

**①** 新增 `HostTargetTests`，先失败于常量不存在：

```text
NameError: name 'TARGET_ID' is not defined
```

**②** 新增「本机缓存里有的那个浏览器，就是这套测试该找到的那个」：

```text
ReferenceError: stagedBrowserFromMachineCache is not defined
```

## GREEN

### ① 按宿主原生目标取，缺归档则 skip 而非 error

`TARGET_ID` 取自既有的 `release_target_id()`；新增 `stage_for_this_host()`，归档不在时
`skipTest` 并说明原因——**归档缺失是这台机器的事实，`ensure_staged_browser` 内部失败才是
缓存的缺陷，此前两者是同一个红**。

只有 `TARGET_ID` 一条守护是不够的：有人在调用处重新写死，它照样绿。所以补了
`test_no_staging_call_names_a_target_literally`，读 AST（不是正则，改名和重排都躲不掉）
拒绝任何 `ensure_staged_browser` / `copy_staged_browser` / `verify_distribution` /
`locked_archive` 的字面量 target。

**变异确认**（用编辑器改，不用 PowerShell——`Set-Content -Encoding utf8` 会加 BOM，
第一次变异因此红在 `SyntaxError: invalid non-printable character U+FEFF` 上，红对了但
理由是错的，那次不算数）：

```text
把一处改回 ensure_staged_browser(target_id="macos-arm64")
→ AssertionError: a staging call names a target literally, so this suite errors on
  every other host: ["line 104: ensure_staged_browser(target_id='macos-arm64')"]
还原 → Ran 8 tests, OK
```

### ② 问 Python 要缓存路径，不在 JS 里重写一份

JS 侧没有 `cache_root()` 的实现（`p9-02-windows-executor.test.mjs` 里那处
`automation-tool-build` 命中是入口点名 `automation-tool-build-windows-executor`，不是缓存根），
所以走既有的 `PYTHON` 常量向 `desktop_e2e_prerequisites` 要路径——**位置会再变一次，
JS 里多一份就是多一处要改错的地方**。可执行文件名从 `distribution-manifest.v1.json` 的
`executable` 字段读，不猜（各 target 不同，且发版本身就是这么解析的）。

死掉的 `.local/desktop-e2e/embedded-browser/...` 候选直接删除，不留注释版本。

**一个自己制造又修掉的缺陷值得记下来。** `await` 最初写在文件靠后处，而
`node:test` 在模块求值结束前就开始跑已注册的测试：

```text
ℹ pass 6   ℹ skipped 2      ← 声明在 await 之前的两条仍然看到 null
```

**这正是本次要修的同一个症状，只是换了一半的测试。** 把 `await` 移到第一个 `test(...)`
之前才成立，代码里已写明这个位置是承重的。

### 结果

```text
node --test tests/motion-render-static-frames.test.mjs
  修复前   pass 0   skipped 7
  await 位置错   pass 6   skipped 2
  修复后   pass 8   skipped 0        真实渲染 1.5s / 1.4s / 1.8s / 18.3s / 2.2s / 1.0s / 2.5s

backend\.venv\Scripts\python.exe scripts\test_embedded_browser_staging_cache.py   Ran 8, OK
backend\.venv\Scripts\python.exe scripts\test_desktop_e2e_prerequisites.py        20 checks passed
backend\.venv\Scripts\python.exe scripts\test_gate_prerequisites.py               Ran 16, OK
backend\.venv\Scripts\python.exe scripts\check_acceptance_evidence_depth.py       83 checks passed
ruff（改动的 py）                                                                  All checks passed
npx eslint tests/motion-render-static-frames.test.mjs                             exit 0
```

## 本机顺带做掉的两件事

**归档迁移**——`FIX-browser-archives-into-machine-cache.md` 的遗留项表写着「Windows 机迁移
`chrome-win64.zip`｜待办」，就是这台。迁移前核对：

```text
契约 archive_sha256 = ebc0c2b75e2ea98151a7f18ff47037bfcbab44a8660e79b9ffa6520f9b7607ab
实测文件 sha256     = ebc0c2b75e2ea98151a7f18ff47037bfcbab44a8660e79b9ffa6520f9b7607ab
```

逐位一致，按该文档做法用移动、不留第二份，192,511,857 字节落到
`%LOCALAPPDATA%\automation-tool-build\embedded-browser-archives\`，空掉的
`.local\eb-04-windows\` 已删。**在这之前 Windows 上 EB-04 / EB-16 / LE-23 / PC-16 全都会报
「归档还没下载」。** 该遗留项已在其文档中标记完成。

**删除重复副本**——`.local\desktop-e2e\embedded-browser\windows-x86_64\chrome-win64`
（310 文件 / 416 MiB）与机器缓存里那棵是同一棵，`.local/` 本就未被 Git 跟踪。删除后复跑
仍是 `pass 8 / skipped 0`，**这一步本身就是「确实在走机器缓存」的证明**。

## 真实边界

- `FIX-shared-embedded-browser-staging-cache.md` 声明「只在 macOS arm64 验证；Windows 走
  同一个函数」。本次在 Windows 实跑闭上了这条：

  ```text
  ensure_staged_browser("windows-x86_64")   首次 5.0s → 310 文件 / 435,703,601 字节 / 0 符号链接
                                            二次 0.00s（命中）
  copy_staged_browser → 私有拷贝             0.2s，文件数 310 未变
  verify_distribution 在私有拷贝上            PASS
  ```

- **`windows-x86_64` 没有符号链接**，所以 `copy_staged_browser` 的 `symlinks=True`
  在 Windows 上是空操作，`CopyTests` 的符号链接那一半在本机**平凡成立**——这一点已写进该
  测试的 docstring，并新增断言记录本次实际比对的链接数，免得它读起来像证明了没证明的事；
- macOS 侧本次未跑（这台是 Windows 机）。两处改动都不含平台分支，但 macOS 上的复跑仍待补。

## 清理

- 删除死候选 `.local/desktop-e2e/embedded-browser/...` 一行，无注释残留；
- 删除本机重复副本 416 MiB 与空目录 `.local\eb-04-windows\`；
- 变异验证已还原，`git diff` 中不含变异痕迹；探路脚本在仓库外的 scratchpad。

## 文档变化

- 本文件新增；
- `FIX-browser-archives-into-machine-cache.md` 的遗留项由「待办」改为已完成并附证据。
