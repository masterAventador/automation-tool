# FIX：生产发布装配路径缺失，常规构建出的包没有浏览器

> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 类型：交付缺陷修复（闭合 EB-16 的 `⬜ 待补：尚无生产发布装配路径`；EB-16 仍因签名凭据保持 🔍 待验收）

## 缺陷

内置 Chromium 是**构建完成之后**由 `install_distribution` 装进 `.app` 的，而这个调用的唯一
非测试出现位置是 `scripts/run_eb_16_acceptance.py`。也就是说：装浏览器这一步只活在验收脚本里，
不在任何可复用的发布路径上。走 P9-03/P9-04 那条候选构建路径产出的包**不含内置浏览器**，
而且没有任何东西拒绝发它——用户装上、打开，才被启动门禁拦住。

## 为什么不能"直接在 tauri.conf.json 里声明资源"

这是最直觉的修法，也是**错的**，EB-16 已经实测过并把数据记在台账里：

| | 暂存树 | 声明 `bundle.resources` 后的包内树 |
| --- | ---: | --- |
| 文件数 | 331 | 334 |
| 符号链接 | 5 | **0** |
| Framework 结构 | `Resources`/`Libraries`/`Helpers` 相对链接完整 | 三个链接全丢，只剩一份 230,463,104 bytes 实体二进制 |
| `codesign --verify --strict` | 通过 | `code has no resources but signature indicates they must be present` |

Tauri bundler 复制资源时跟随符号链接，把 Chrome for Testing 的 macOS Framework 拆坏、
重复了 230MB 框架二进制、并使上游代码签名失效。这样的包在用户机上会被生产 Rust 解析器
（EB-06）判为"浏览器组件损坏"。所以**不声明是有意的**，
`write_release_configuration` 的注释里原本就写着理由。

真正缺的不是一行配置，是一条**发布装配路径**。

## RED

```text
python3 scripts/test_release_assembly.py
  ModuleNotFoundError: No module named 'release_assembly'
```

补上模块后，剩下的红是防漂移那一条——它要求验收脚本不能再自带一份装配实现：

```text
FAIL: test_the_acceptance_script_delegates_to_the_shared_assembler
AssertionError: 'from release_assembly import' not found in …
```

## GREEN

新增 `scripts/release_assembly.py`：

- `install_and_seal(...)`：装入 → **按 EB-05 Manifest 逐文件重新核验** → 才封章签名。
  顺序是硬的，签名早于装入就覆盖不到浏览器。任何一步失败都删掉半装的树且不签名，
  失败的装配留不下一个会被后续步骤误认成成品的包。
- `require_packaged_browser(...)`：发布关口。没有浏览器子树，或摘要与 Manifest 不符，
  直接拒绝。出 DMG 之前必过这一道。
- `seal` 可注入，正式签名身份接上来时替换这一个参数即可，不用改装配流程。

`scripts/run_eb_16_acceptance.py` 改为委托该模块，不再自己 `install_distribution`。

```text
python3 scripts/test_release_assembly.py                  5 tests OK
backend/.venv/bin/python scripts/run_eb_16_acceptance.py  EXIT=0
  [EB-16] Installing the embedded browser, verifying it, then re-sealing
  [EB-16] EB-16 acceptance passed: one ad-hoc signed macos-arm64 package with
  331 browser files (359441871 bytes), package 565700212 bytes, disk image 257617067 bytes
uv run ruff check --ignore RUF001 scripts/release_assembly.py scripts/test_release_assembly.py
python3 scripts/check_embedded_browser_video_roadmap.py
```

端到端重跑是真实构建：真编译、真暂存、真装配、真出 DMG、真挂载安装、真卸载查残留，
331 个浏览器文件与改造前一致。

## 真实产物上的双向验证

不只跑合成 fixture。拿本机刚构建出的真实正式包和一个常规候选形状的包各验一次：

| 输入 | 结果 |
| --- | --- |
| 真实正式包（走过装配） | 通过，返回 `…/Contents/Resources/embedded-browser` |
| 常规候选构建形状的包（没走装配） | 拒绝：`the bundle carries no embedded browser at … — it was built without the release assembly step` |

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 包内没有浏览器子树 | 拒绝，且说明是漏了装配步骤 | 合成 + 真实包 |
| 包内浏览器与 Manifest 摘要不符 | 拒绝 | 合成 |
| 暂存树被篡改 | 装配中止，**不签名**，不留半装的树 | 合成 |
| 已有浏览器时重复装配 | 拒绝，不覆盖 | 合成 |
| 验收脚本自带第二份装配实现 | 测试变红 | 静态 |
| 签名早于装入 | 断言封章发生在装入之后 | 合成 |

## 真实边界

1. **只在 macOS arm64 上验证**。Windows NSIS 那条发布路径（P9-04）没有对应改造，
   Windows 目标按 EB-03 契约不含符号链接，装配约束不同，需要在 Windows 上单独确认。
2. **签名仍是 ad-hoc**。`seal` 参数化了，但正式 Developer ID 签名、公证与装订仍缺凭据，
   本机只有 Apple Development 开发证书（不是 Developer ID Application），无法提交公证。
3. **发布关口只挡 DMG 这一个出口**。如果将来新增别的分发产物（如 pkg、zip），
   必须各自调用 `require_packaged_browser`，没有机制强制新出口接上这道关。
4. 内层浏览器重签与 EB-05 逐文件摘要的冲突仍未解（EB-16 已登记）：正式签名任务必须把
   Chromium 签名提前到暂存阶段（先签、后算摘要），否则 `codesign --deep` 后包内浏览器
   会被自身完整性门禁判为损坏。

## 清理

EB-16 端到端重跑自带安装/卸载与残留检查，退出码 0；构建产物留在被 gitignore 的
`.local/eb-16/`。未新增常驻服务。

## 文档

- `scripts/release_assembly.py`（新增）
- `scripts/test_release_assembly.py`（新增）
- `scripts/run_eb_16_acceptance.py`（改为委托共享装配器）
- `docs/development/EB-16.md`（该条遗留项转为已闭合）
- 本文件

## 遗留项

| 项 | 状态 | 归属 |
| --- | --- | --- |
| Windows NSIS 发布路径接入同一装配器 | 未做 | EB-16 Windows 侧 / P9-04 |
| 新增分发出口时强制接上发布关口的机制 | 未做 | CQ-05 |
| 正式 Developer ID 签名、公证、装订 | 未做，缺凭据 | EB-16 |
