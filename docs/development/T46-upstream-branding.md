# T46 内置 WebUI 的上游品牌名：核实与门禁补齐

> 状态：✅ 已完成（现象判定 + 门禁补齐；**未改产品表现，因为产品路径上不复现**）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：另一条工作线在正式包上实跑素材成片链路时，看到内置 WebUI 顶部整幅显示
> `MoneyPrinterTurbo v1.3.2`，判定违反项目规则第 6 节，要求修表现并补门禁。

## 0. 结论先说

**产品窗口里不复现。** 用户从「打开完整制作界面」得到的那个窗口，上游品牌名已经被
`frontend/src-tauri/src/material_video_studio_init.js` 全面改写，标题、正文、可访问性
属性、外链一处不剩，而且带 fail-closed 审计。**看到 `MoneyPrinterTurbo v1.3.2` 的前提是
绕开产品窗口、直接用浏览器打开 Streamlit 地址**——我本次也复现了这条路径，确实会看到。

所以本任务**没有改产品表现**：在 Worker 层再加一层品牌改写会与既有机制重复，还会打掉
现有桌面 E2E 的断言（见 §3）。真正缺的是那半边——**门禁**，本任务把它补上了。

## 1. 核实过程

### 1.1 上游把品牌名渲染在哪

`vendor/moneyprinterturbo/webui/` 全量扫描，10 处：

| 位置 | 性质 |
| --- | --- |
| `Main.py:45` `page_title="MoneyPrinterTurbo"` | 浏览器/窗口标题 |
| `Main.py:50,51,54` `menu_items` 的 Report a bug / About | 只在 Streamlit 顶栏菜单里，上游 `styles.css` 已把顶栏整块隐藏 |
| `Main.py:1034` `<span class="mpt-brand__name">MoneyPrinterTurbo</span>` | 页面 h1，**就是「整幅显示」的那一处** |
| `Main.py:1036` `href="https://github.com/harry0703/MoneyPrinterTurbo"` | 版本号外链 |
| `Main.py:1039` `aria-label="Open MoneyPrinterTurbo on GitHub"` | 可访问性名称 |
| `Main.py:60` | Python 注释，不渲染 |
| `i18n/en.json:248`、`i18n/zh.json:248` | Kimi 模型列表链接里的 `?aff=MoneyPrinterTurbo` 参数，只在 href 里 |

### 1.2 产品窗口做了什么

`frontend/src-tauri/src/material_video_studio.rs:199-204` 建窗口时挂了
`material_video_studio_init.js` 作为初始化脚本。该脚本在文档开始阶段就先把 `body` 藏起来
（`html[...studio-state="booting"]`），然后：强制 `document.title`、遍历所有文本节点替换品牌、
替换 5 类可访问性属性、剥掉全部外链 `href`、把 `.mpt-brand__name` 文本换成「智能素材成片」、
隐藏版本号，最后跑一遍 `audit()`，**只要还剩一处品牌就 fail closed** 显示「制作界面暂时不可用」。

### 1.3 实测（本次证据）

按锁定版本起真实 WebUI（产品同一条适配路径），无头浏览器打开。

**不注入产品脚本**（等价于直接用浏览器打开 Streamlit 地址）：

```json
{"title": "MoneyPrinterTurbo",
 "hits": [{"tag": "TITLE", "text": "MoneyPrinterTurbo"},
          {"tag": "SPAN", "cls": "mpt-brand__name", "text": "MoneyPrinterTurbo"}]}
```

**把产品真实的 `material_video_studio_init.js` 原样注入同一页面**（不改一个字符）：

```json
{"title": "智能素材成片", "state": "ready", "failure": null,
 "brandRendered": "智能素材成片", "forbiddenHits": []}
```

**再验有没有「一闪而过」**：装一个 MutationObserver 专门记录 `.mpt-brand__name` 何时
出现品牌文本，然后连续触发两次 Streamlit rerun（勾选/取消字幕）：

```json
{"flashes": 0, "brandNow": "智能素材成片", "title": "智能素材成片"}
```

0 次闪现。也就是说 rerun 不会把品牌名放回来。

### 1.4 既有验收

`docs/embedded-browser-video-studio-roadmap.md` 的 IM-06「主题统一与上游名称隐藏」已
`✅ 已完成`；`frontend/e2e-tauri/material-video-webui.spec.ts` 从「视频制作 → 打开完整制作界面」
这条正常用户路径打开真实窗口，断言 `getTitle() === "智能素材成片"`、正文不匹配
`/moneyprinterturbo|hyperframes/i`、外链数为 0。

## 2. 真正的缺口：门禁

原话「`check_user_facing_branding.py` 只扫我们自己的文件，扫不到内嵌的上游 WebUI」是**对的**。
补充两点：

1. 覆盖并非为零——`material-video-webui.spec.ts` 是真实运行时门禁，覆盖的正是渲染结果；
2. 但它要整包桌面构建才能跑，只在跑桌面 E2E 时才会碰到，日常常规门禁不会触发它。

所以补一条**静态、快速、每次常规门禁都会跑**的规则，与运行时门禁互补：
运行时门禁证明「渲染结果干净」，静态门禁证明「上游没有新增我们没复核过的暴露点」，
后者是前者给不了的。

### 2.1 补法

`scripts/check_user_facing_branding.py` 新增第 0 条规则 `scan_embedded_web_ui`：
把内置 WebUI 里所有出现上游项目名的行收集成清单，排序后取 sha256，与
`contracts/quality/user-facing-terminology.v1.json` 的 `embeddedWebUiScan` 声明比对
（当前 10 处，`bb416858…`）。任何新增、移动或改写都会变红，并要求人重新核对窗口守卫。

**为什么是「钉住暴露面」而不是「扫出违规」**：内置 WebUI 是上游代码，它在自己源码里写自己的
项目名是正当的，用第 1 条规则去扫它每次发行都会红。产品的保证不在上游源码里，而在窗口守卫
把它们改写掉——那件事只有桌面 E2E 能证明。钉住暴露面等于：**上游一升级就强制人复核一次守卫**，
这正是「下次上游一升级又会漏出来」要防的东西。契约里同时记了这 10 处各自属于什么表面，
复核时不用重新考古。

## 3. 为什么没在 Worker 层再改一遍表现

- 会与 `material_video_studio_init.js` 重复，形成两个事实源；
- 而且会打掉现有断言：`material-video-webui.spec.ts` 要求正文包含「智能素材成片」，
  这句话在页面上只有品牌 h1 一处。如果我在私有样式里把 `.mpt-brand__name` 隐藏、
  改用 CSS 伪元素显示产品名，WebdriverIO 的 `getText()` 既不返回 `display:none` 元素、
  也不返回伪元素内容，那条断言会直接变红——正好撞上「别在别人验收到一半时把行为换掉」。

## 4. TDD 证据

### RED

先加 4 条用例（暴露面变大、暴露面改写、声明缺失、扫描被清空禁用），再写实现。
RED 是断言失败，不是导入或编译失败：

```text
$ python3 scripts/test_user_facing_branding.py
AssertionError: embedded WebUI brand surface grew: tampered input must fail
```

### GREEN

```text
$ python3 scripts/test_user_facing_branding.py
CQ-01 real App plain-language check passed (1 pages)
CQ-01 user-facing plain-language gate tests passed

$ python3 scripts/check_user_facing_branding.py
user-facing branding and plain-language scan passed (52 frontend, 250 native files)

$ python3 scripts/run_av_03_acceptance.py
AV-03 threat, terminology and branding acceptance passed
```

真实仓库上把声明的数量从 10 改成 9，门禁确实咬住：

```text
returncode 1
embedded WebUI brand surface changed: found 10 occurrences, sha256 bb416858…;
contract declares 9, sha256 bb416858…. Re-check that the studio window guard
still rewrites every one of them, then update embeddedWebUiScan in the
terminology contract.
```

## 5. 失败矩阵

| 情况 | 期望行为 | 证据 |
| --- | --- | --- |
| 上游升级新增一处品牌 | 静态门禁变红并给出新旧摘要 | 用例「暴露面变大」 |
| 上游把品牌换个写法 | 同上 | 用例「暴露面改写」 |
| 契约里删掉这段声明 | `scan_policy` 直接失败 | 用例「声明缺失」 |
| 有人把 `roots` 清空静默关掉门禁 | 显式失败，不允许空 roots | 用例「扫描被清空禁用」 |
| 窗口守卫本身回归 | 页面出现品牌 → 守卫 `audit()` fail closed → 桌面 E2E 变红 | 既有机制，本次未改 |

## 6. 遗留

- ~~桌面 E2E 当前在 `main` 上构建不起来（T34 §4），品牌的运行时门禁事实上没在跑~~
  **——已过时，本条作废。** 写下这条时我采信的是 T34 的快照结论，但那次编译失败
  （`c0cc760` 改 `PublishRequest` 类型漏同步两个消费方）已由 `7b776fd` 修掉，
  在干净 worktree 上独立验过 `tsc -b` exit 0。**桌面 E2E 构建得起来，
  运行时门禁在跑**，本次补的静态门禁是多的一层，不是唯一一层；
- 本次没有在重新构建的正式 `.app` 里再跑一遍窗口验收，理由同 T32：另一条工作线正在
  同一份正式包上跑素材成片链路验收。既有 IM-06 验收 + 本次注入真实守卫脚本的实测
  共同支撑「产品路径干净」这个结论；
- 若将来确认用户看到的确实是产品窗口而非直连地址，请把当时的窗口截图或
  `data-automation-tool-studio-state` 取值提供出来，那会是完全不同的一类问题
  （守卫没跑起来），需要另立任务。
