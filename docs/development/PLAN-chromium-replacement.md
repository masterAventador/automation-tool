# PLAN 内置浏览器从 Chrome for Testing 换成 Chromium 开源构建的可行性验证

> 状态：📋 方案（本轮只做验证与取证，未修改任何契约、产品代码或台账）
>
> 日期：2026-07-26
>
> 触发：`scripts/build_embedded_browser_distribution.py:181` 的
> `"redistribution_review": "pending"` 从 EB-05 起一直没有结论——把带 Google 品牌的
> Chrome for Testing 打进商业安装包再分发是否被允许，从来没有人核实过。
> 决策方向是换成 Chromium 开源构建，本文件验证这条路是否走得通。
>
> 验证环境：macOS 26.4 / arm64；对照浏览器取自 `~/Library/Caches/ms-playwright/chromium-1228`
> （Playwright 1.61.0 自己下载的 CfT）；候选构建下载到 `.local/chromium-eval/`，验证结束已删除。
> 全程无头、全新一次性 Profile、只做导航不做任何操作，未接触用户的抖音登录态。

---

## 0. 一句话结论

**「换成 Playwright 提供的 Chromium 开源构建」这条路不存在**——Playwright 1.61.0 在
macOS 和 Windows 上根本不再提供 Chromium 构建，只剩 Chrome for Testing。
退而求其次的官方 Chromium 连续构建实测会让**抖音视频播放器彻底失效**（黑屏、
`networkState=3`、反复重试 CDN），因为所有 Chromium 开源构建都不含 H.264/AAC。

而这次真正查出来的**唯一一条白纸黑字的分发禁令**，只针对 CfT 里的**一个文件**：
macOS 版 CfT 内置的 `libwidevinecdm.dylib`（20.2 MiB），它自带的 LICENSE 写着
「未经与 Google 单独签署许可协议，不得使用、修改、销售或以其他方式分发本软件」。
实测把这个目录从 staged tree 删掉：**签名门禁不受影响、H.264/AAC/HEVC 全部保留、
抖音正常渲染**，Windows 版 CfT 里压根没有这个文件。

所以建议不是「换浏览器」，而是「**留在 CfT，剔除 Widevine CDM，把
`redistribution_review` 从 pending 换成有依据的结论**」。详见第 7 节。

---

## 1. 前提核对：Playwright 1.61.0 到底提供什么

任务假设「Playwright 1.61.0 的 driver 里除了 cftUrl 还有 chromium 的下载地址」。
读 driver 源码 + 实测 CDN，结论是这个假设对我们的三个目标平台不成立。

`backend/.venv/.../playwright/driver/package/lib/coreBundle.js` 的 `DOWNLOAD_PATHS.chromium`
里，只有 **linux-arm64** 走 `builds/chromium/%s/chromium-linux-arm64.zip`，
**mac10.13～mac26、mac*-arm64、win64 全部是 `cftUrl(...)`**：

```text
"mac15-arm64": cftUrl("mac-arm64/chrome-mac-arm64.zip"),
"win64":       cftUrl("win64/chrome-win64.zip"),
"ubuntu22.04-arm64": "builds/chromium/%s/chromium-linux-arm64.zip",   ← 唯一的非 CfT
```

实测 CDN（`curl -I`，锁定 revision 1228）：

| URL | 结果 |
| --- | --- |
| `builds/chromium/1228/chromium-mac-arm64.zip` | **404** |
| `builds/chromium/1228/chromium-mac-x64.zip` | **404** |
| `builds/chromium/1228/chromium-win64.zip` | **404** |
| `builds/chromium/1228/chromium-linux-arm64.zip` | 200，196,280,473 B |
| `builds/cft/149.0.7827.55/mac-arm64/chrome-mac-arm64.zip` | 307 → `storage.googleapis.com/chrome-for-testing-public/...` → 200，179,277,110 B |

再往回扫历史 revision，能看出 Playwright 是**主动停产**了 mac/win 的 Chromium 构建：

| revision | `chromium-mac-arm64.zip` |
| --- | --- |
| ≤ 1198 | 200（真的 Chromium 构建，rev 1198 = **Chromium 142.0.7444.53**） |
| 1200、1205 | **307 重定向到 CfT 桶**（`chrome-for-testing-public/143.0.7499.4/...`） |
| ≥ 1210 | 404 |

也就是说：Playwright 从 143 起先把 mac 的 chromium 路径别名到 CfT，再把老构建下线。
**Playwright 不是我们可选的 Chromium 来源。**

剩下的候选来源只有三个：

1. **Google 官方 Chromium 连续构建**
   `commondatastorage.googleapis.com/chromium-browser-snapshots/{Mac_Arm,Mac,Win_x64}/<position>/chrome-*.zip`。
   没有稳定版本号，只有 trunk commit position；Google 明确说明这些构建不面向终端用户，
   没有 PGO/官方优化，也没有 Google 签名。
2. **Playwright 已停产的 rev ≤1198 构建**（Chromium 142.0.7444.53）。
3. **自建 Chromium**（见第 8 节 Option D）。

本文的实测用 (1) 作主要候选，用 (2) 做交叉验证。

---

## 2. 两个构建的逐项对比

### 2.1 macOS arm64（实际解包实测）

| 项 | Chrome for Testing（现状） | Chromium 官方快照 | Playwright Chromium（停产） |
| --- | --- | --- | --- |
| 版本 | 149.0.7827.55 | 152.0.7973.0（position 1668319） | 142.0.7444.53（rev 1198） |
| 版本性质 | 与 Playwright 1.61.0 锁定的版本**逐位一致** | trunk 连续构建，非稳定分支 | 比锁定版本落后 7 个大版本 |
| 压缩包 | 179,277,110 B | 167,982,942 B | 137,616,492 B |
| 解包字节 | **359,441,871 B（342.8 MiB）** | **365,588,820 B（348.7 MiB）** | 315,575,827 B（300.9 MiB） |
| 文件 / 符号链接 | 331 / 5 | 331 / 5 | 325 / 5 |
| 根目录 | `chrome-mac-arm64` | `chrome-mac` | `chrome-mac` |
| 可执行文件 | `Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing` | `Chromium.app/Contents/MacOS/Chromium` | `Chromium.app/Contents/MacOS/Chromium` |
| 代码签名 | adhoc / linker-signed，`Identifier=Google Chrome for Testing` | adhoc / linker-signed，`Identifier=Chromium` | 同左 |
| Framework 名 | `Google Chrome for Testing Framework.framework` | `Chromium Framework.framework` | `Chromium Framework.framework` |
| Widevine CDM | **有**（20,183,440 B + 专有 LICENSE） | 无 | 无 |
| H.264 / AAC | **有** | **无** | **无** |
| 许可 | Chromium 部分 BSD-3-Clause，整包含 Google 组件 + Google 品牌 | BSD-3-Clause 家族（无 Google 专有组件） | 同左 |

体积不是换的理由：**Chromium 快照比 CfT 还大 6.1 MB**（官方快照没开 official build 优化）。

### 2.2 三个目标平台的体积（关键否决点在 Windows）

| 目标 | 构建 | 压缩包 | 解包 | 备注 |
| --- | --- | --- | --- | --- |
| macos-arm64 | CfT 149 | 179,277,110 | 359,441,871（342.8 MiB） | — |
| macos-arm64 | Chromium 152 快照 | 167,982,942 | 365,588,820（348.7 MiB） | +5.9 MiB |
| macos-x86_64 | CfT 149 | 189,658,978 | 372,048,328（354.8 MiB） | Widevine 22,565,792 B |
| macos-x86_64 | Chromium 152 快照 | 186,334,400 | 397,226,535（378.8 MiB） | +24 MiB |
| windows-x86_64 | CfT 149 | 192,511,857 | 435,574,347（415.4 MiB） | **无 Widevine**，无额外 LICENSE |
| windows-x86_64 | Chromium 152 快照 | 342,404,067 | **811,159,342（773.6 MiB）** | 含 `interactive_ui_tests.exe` 344,082,944 B |

Windows 的 Chromium 快照包里塞了一个 **328 MiB 的测试二进制 `interactive_ui_tests.exe`**，
产品包里绝不能带。剔掉它还剩 467,076,398 B（445.4 MiB），仍然**超过**
`scripts/check_embedded_browser_package.py` 里 `max_browser_bytes = 420 MiB` 的上限；
而且 `chrome.dll` 从 CfT 的 285 MB 涨到 317 MB（无 official-build 优化）。

更要命的是：剔文件这件事本身会**推翻 EB-03 的核心不变量**——现在的设计是
「原样解包 + 逐文件摘要锁定 + 除唯一根目录外不允许任何额外内容」，
一旦引入「先按白名单裁剪再锁定」，`staging` 的可复现性论证要整个重写。

---

## 3. 编解码器实测（这是决定性的一项）

### 3.1 能力矩阵

同一段脚本，两个浏览器各起一次一次性无头 Profile，`video.canPlayType` + `MediaSource.isTypeSupported`：

| 类型 | CfT 149 | Chromium 152 快照 | Playwright Chromium 142 |
| --- | --- | --- | --- |
| `video/mp4; codecs="avc1.42E01E"`（H.264 Baseline） | `probably` / MSE `true` | **`no` / MSE `false`** | **`no` / MSE `false`** |
| `video/mp4; codecs="avc1.640028"`（H.264 High） | `probably` / `true` | **`no` / `false`** | **`no` / `false`** |
| `audio/mp4; codecs="mp4a.40.2"`（AAC-LC） | `probably` / `true` | **`no` / `false`** | **`no` / `false`** |
| `audio/aac` | `probably` / `true` | **`no` / `false`** | **`no` / `false`** |
| `video/mp4; codecs="hev1..."`（HEVC） | `probably` / `true` | **`no` / `false`** | **`no` / `false`** |
| `audio/mpeg`（MP3） | `probably` | `probably` | — |
| `video/webm; codecs="vp9,opus"` | `probably` | `probably` | `probably` |
| `video/mp4; codecs="av01..."`（AV1） | `probably` | `probably` | `probably` |

两个不同来源的 Chromium 构建结论一致：**没有任何一个 Chromium 开源构建带 H.264/AAC**。
这不是版本差异，是编译开关（`proprietary_codecs` / `ffmpeg_branding`）的差异。

### 3.2 抖音真实页面的后果

在 `https://www.douyin.com/video/7666102769060711680`（公开视频详情页，未登录、未做任何点击）
连续采样 4 次 × 5s：

| | CfT 149 | Chromium 152 |
| --- | --- | --- |
| `video.currentSrc` | `blob:https://www.douyin.com/...`（MSE） | `https://www.douyin.com/aweme/v1/play/?file_id=...`（**回退到直链**） |
| `readyState` | **4**（HAVE_ENOUGH_DATA） | **0**（HAVE_NOTHING） |
| `networkState` | 2（LOADING） | **3（NETWORK_NO_SOURCE）** |
| `duration` | 403.76 | `null` |
| `videoWidth × videoHeight` | 2560 × 1440 | 0 × 0 |
| `currentTime`（4 次采样） | 3.52 → 8.52 → 13.52 → 18.53（**在播**） | 0 → 0 → 0 → 0（**没动**） |
| 播放器区域截图 | 有画面，进度 `00:18 / 06:44` | **全黑，`00:00 / 00:00`** |
| CDN 请求 | 1 条 | 10 条以上，`v11-webc` / `v26-webf` / `v26-web-prime` **反复重试** |
| Console | 无 | `Failed to parse video contentType: video/mp4; codecs=hev1.1.6.L120.90`（×3） |

链路是清楚的：抖音先走 MSE + H.264 → Chromium 的 `isTypeSupported` 返回 false →
播放器降级到 `<video src>` 直链 → 仍然是 H.264 → 解码不了 → `NETWORK_NO_SOURCE` →
换 CDN 重试 → 无限循环。

### 3.3 对 DOM 与 RPA 定位的影响（这部分是好消息）

同一个视频详情页的 `[data-e2e]` 集合：

| | CfT | Chromium |
| --- | --- | --- |
| 唯一 `data-e2e` 数量 | 33 | 32 |
| 差异 | 多 `danmaku-container` | 少 `danmaku-container` |
| 元素总数 | 3114 | 2873（约 −8%） |

RPA 真正依赖的锚点**两边都在**：`video-detail`、`player-container`、`video-player-digg`、
`video-player-collect`、`video-player-share`、`comment-list`、`comment-item`、
`video-comment-more`、`user-info`、`searchbar-input`、`video-switch-next-arrow`。

首页 `/jingxuan` 两边完全一致（同 8 个 `data-e2e`，元素数 2609 vs 2573，标题一致）。

复核了 `backend/src/automation_tool/executor/rpa/douyin/` 全部适配器：
登录、搜索、浏览、候选抽取、评论、私信、主页都是纯锚点/选择器驱动，
**没有任何一处读取 `<video>` 的 `readyState`、`duration` 或播放状态**，
发布链路走的是 `input[type=file][accept*=video]`。所以「点得到」这一层不会因为
缺编解码器而失效——失效的是「页面上真的有画面」和依赖播放状态才挂载的图层（弹幕）。

---

## 4. 平台识别实测

### 4.1 纯无头（不改任何东西）

两个浏览器打开 `https://www.douyin.com/`，**结果完全一样**：
`title = "验证码中间页"`，`document.querySelectorAll('*').length = 18`，正文 4 个字符。
抖音在无头 + `navigator.webdriver=true` 下对两者一视同仁地拦截。
**这条差异不存在，也说明无头本身才是拦截原因，与构建来源无关。**

### 4.2 归一化后（对两者施加完全相同的处理）

为了能比较页面结构，对两边**同样地**去掉 headless 标记
（`user_agent` 只把 `HeadlessChrome` 换成 `Chrome`、保留各自真实版本号，
加 `--disable-blink-features=AutomationControlled`，去掉 `--enable-automation`）。
两边都顺利进入 `/jingxuan`。这不是生产配置，只是为了做同条件对比，结论如下：

| 特征 | CfT 149 | Chromium 152 |
| --- | --- | --- |
| `navigator.userAgent` | `... Chrome/149.0.0.0 Safari/537.36` | `... Chrome/152.0.0.0 Safari/537.36` |
| **`sec-ch-ua` 请求头** | `"Chromium";v="149", "Not)A;Brand";v="24"` | `"Not?A_Brand";v="24", "Chromium";v="152"` |
| `navigator.userAgentData.brands` | Chromium + GREASE | Chromium + GREASE |
| `uaFullVersion` | 149.0.7827.55 | 152.0.7973.0 |
| `navigator.vendor` | `Google Inc.` | `Google Inc.` |
| `navigator.platform` | `MacIntel` | `MacIntel` |
| `navigator.plugins`（5 个 PDF） | 完全相同 | 完全相同 |
| `window.chrome` keys | `loadTimes, csi, app` | `loadTimes, csi, app` |
| Widevine EME | **`NotSupportedError`** | `NotSupportedError` |
| 抖音的 `bitbrowser://cc/` 反指纹探测 | 两边都触发，行为一致 | 同左 |

**关键发现：Chrome for Testing 在品牌标识上已经就是 "Chromium"**——
`sec-ch-ua` 和 `userAgentData.brands` 里**没有 "Google Chrome"**。
也就是说，「换成 Chromium 会不会被抖音区别对待」这个担心，
在**品牌字段这一层不成立**：抖音现在看到的就已经是 Chromium。

抖音能观察到的真实差异只有两项：**版本号**，以及**编解码器能力集**。
后者既是很强的指纹特征，更重要的是它**直接把播放打挂了**（第 3 节）。

Widevine EME 在**两边都不可用**（都抛 `NotSupportedError`），
即 CfT 虽然把 CDM 文件打包进来了，在我们的启动方式下它压根没被启用——
**这个 20 MiB 的专有组件对产品零价值，纯负债**。

---

## 5. 真正的法律事实：Widevine CDM

这是本轮唯一查到的、白纸黑字的分发禁令，而且它**只是 CfT 里的一个目录**：

```text
chrome-mac-arm64/Google Chrome for Testing.app/Contents/Frameworks/
  Google Chrome for Testing Framework.framework/Versions/149.0.7827.55/
  Libraries/WidevineCdm/
    LICENSE                                        (473 B)
    _platform_specific/mac_arm64/libwidevinecdm.dylib  (20,183,440 B)
```

`LICENSE` 全文（473 B）：

> Google LLC and its affiliates ("Google") own all legal right, title and interest in and to
> the content decryption module software ("Software") ... **You may not use, modify, sell, or
> otherwise distribute the Software without a separate license agreement with Google.
> The Software is not open source software.**

现状是：这个文件在 `distribution-manifest.v1.json` 里被逐文件摘要锁定，
随 macOS 安装包分发给用户。**这就是 `redistribution_review: "pending"` 背后最实的那条风险**，
而且不需要任何法务解读——那句话就是字面意思。

分布情况（实测三个 CfT 归档）：

| 目标 | Widevine CDM |
| --- | --- |
| macos-arm64 | 有，20,183,440 B |
| macos-x86_64 | 有，22,565,792 B |
| **windows-x86_64** | **没有**（整个归档里没有任何 widevine 文件，也没有任何 LICENSE 文件） |

### 5.1 实测：删掉它会不会破坏任何东西

把缓存里的 CfT 树整份复制到 `.local/chromium-eval/cft-pruned/`，删掉 `Libraries/WidevineCdm/`，
然后跑 EB-16 实际使用的那套校验和一次真实抖音访问：

| 检查 | 结果 |
| --- | --- |
| 文件数 / 字节 | 331 / 359,441,871 → **328 / 339,257,128**（省 20,184,743 B ≈ 19.2 MiB） |
| `codesign -dv` 可执行文件 | `Identifier=Google Chrome for Testing`、`adhoc`、`linker-signed` **全部保留** |
| `codesign --verify --strict` | 失败，报 `code has no resources but signature indicates they must be present` |
| **对照组：未删改的原始缓存树** | **同样失败，同样的报错** |
| **对照组：只 `cp -R` 不删任何文件** | **同样失败，同样的报错** |
| 启动 + 打开抖音 | 正常，`/jingxuan` 渲染出与未删改版本相同的 8 个 `data-e2e` |
| `canPlayType` avc1/mp4a/hev1 | **全部仍为 `probably`** |

对照组证明这个 `codesign --verify --strict` 失败**与删文件无关**——CfT 上游就是
linker-signed、`Sealed Resources=none`，本来就过不了这条命令。
`scripts/run_eb_16_acceptance.py:398 verify_embedded_mach_o_signature()` 正是为此而写：
它把 Mach-O **复制出 bundle 再单独校验**，所以删掉一个同级 dylib 完全不影响它。

**结论：剔除 Widevine 是零功能损失、零门禁风险、省 19.2 MiB 的纯收益动作。**

### 5.2 剔除 Widevine 之后还剩什么问题

剩下的是**品牌与 ToS**：产物仍叫 `Google Chrome for Testing.app`，进程名、
Framework 名、`codesign Identifier` 都带 Google 商标，Google 对 CfT 的定位是
「测试专用产物」。**这一条我无法给出法律结论**，本文不假装能。
但它和 Widevine 是两个独立问题，Widevine 那条是可以现在就消掉的。

---

## 6. 完整改动清单（假设真的执行「换成 Chromium」）

以下按「必须改」分层列出，规模按契约字段数 / 代码行数 / 需重跑的门禁估。

### 6.1 契约（JSON）—— 事实源，必须最先改

| 文件 | 改什么 | 规模 |
| --- | --- | --- |
| `contracts/browser/embedded-chromium-staging.v1.json` | 3 个 target × 6 个字段全换：`download_url`、`redirect_host_allowlist`、`redirect_path_prefix`、`archive_sha256`、`root_entry`、`executable`；顶层 `browser_version`/`revision`/`title`；`sources.url_template_origin`/`redirect_target_origin` 重写 | ~20 处 |
| 同上 · **结构性问题** | Chromium 快照 mac-arm64 与 mac-x64 的根目录**都叫 `chrome-mac`**，与现在三个目标根目录互不相同的前提冲突；`_other_root_entries()`（`check_embedded_browser_package.py:160`）据此判断「包里混进了第二个架构」，该检查会失效，必须在 staging 阶段重命名根目录 | 需改 staging 语义 |
| `contracts/browser/embedded-chromium-compatibility.v1.json` | `production_runtime.chromium` 的 `title`/`browser_version`/`revision`；`driver_browsers_json_sha256` | ~4 处 |
| 同上 · **架构性问题** | 见 6.5 | **设计变更** |
| `contracts/browser/shared-chromium-validation.v1.json` | `chromium.browser_version`/`revision`、两个平台的 `evidence_sha256`（要重跑取证） | ~4 处 + 重取证 |
| `contracts/browser/fixtures/*.json`（6 份） | 每份 `title`/`browser_version`/`revision` | 18 处 |
| `contracts/quality/third-party-notice-ui.v1.json`（生成物） | `name`、`version`、`license`、`noticeChannelId`；若判定为「可获取源码」，`third-party-software-notice.ts:310-320` 会开始要求 `packagedSourcePaths` + `upstreamSourceUrl` | 重新生成 + 可能新增源码分发义务 |

### 6.2 Rust（编译期常量）

| 文件 | 改什么 |
| --- | --- |
| `frontend/src-tauri/src/embedded_browser_distribution.rs` | `EXPECTED_CHROMIUM_TITLE`/`_VERSION`/`_REVISION`（L28-30）；`expected_executable()` 三个分支（L332-341）；`FORBIDDEN_NAME_SUBSTRINGS` 需重新对着真实归档验证 |
| `frontend/src-tauri/src/lib.rs` | L379-383 从契约 `include_str!` 推导 `chromium_major`，跟着契约走，无需单独改逻辑但要重编 |

### 6.3 构建 / 装配 / 审计脚本

| 文件 | 改什么 | 风险 |
| --- | --- | --- |
| `scripts/build_embedded_browser_distribution.py` | L172-182 licence block（`component`、notice 文本、`redistribution_review`）；L185-194 SBOM（`name`、`purl`、`source_url`）；L206 macOS framework 注释 | 低 |
| `scripts/check_embedded_browser_package.py` | `_FORBIDDEN_EXECUTABLE_NAMES` 里 **`"chromium"` 和 `"chrome"` 现在是禁止名**，换过去以后它们是**合法的**打包可执行文件名（tree 内已豁免，但语义反了，必须重新从契约推导）；`RELEASE_PAYLOAD_PARTS_MIB["embedded-chromium"]` 与注释里的「333 files, 359,658,199 bytes」要重测；`RELEASE_SIZE_BOUNDS.max_browser_bytes = 420 MiB` **Windows 会顶穿** | **高** |
| `scripts/build_embedded_chromium_staging.py` | 文档串、`root_entry` 语义；若要剔除 Windows 的 `interactive_ui_tests.exe`，要新增「白名单裁剪」阶段 | **高（推翻不变量）** |
| `scripts/run_eb_16_acceptance.py` | L432/435-438 硬编码 `Identifier=Google Chrome for Testing` 与 `Google Chrome for Testing Framework.framework` 路径，换成 `Chromium` / `Chromium Framework.framework`；L86/L104 归档缓存路径 | 中 |
| `scripts/validate_shared_chromium.py` | L169-174 macOS/Windows 的 glob 全部换名 | 低 |
| `scripts/check_embedded_browser_compatibility.py` | L202-217 与安装的 Playwright `browsers.json` **逐字段相等**比对（含 `title`）——见 6.5 | **高** |
| `scripts/embedded_browser_archives.py`、`desktop_e2e_prerequisites.py`、`eb_17_clean_machine.py`、`release_assembly.py`、`verify_macos_chromium_archive.py` | 归档文件名、根目录名、注释 | 低 |
| `run_eb_01/02/03/04/05/17`、`run_bm_03/04/08/16`、`run_bu_02/07`、`run_pb_08` 各验收脚本 | 版本号、归档路径、major 版本断言 | 低但面广（约 15 个文件） |
| `scripts/build_third_party_notice_ui_projection.py` | `BROWSER_LICENSE`、`BROWSER_NOTICE_CHANNEL` 及 L87-91 注释 | 低 |

### 6.4 测试

| 层 | 文件 | 规模 |
| --- | --- | --- |
| Python | `test_embedded_chromium_staging.py`(33 处)、`test_embedded_browser_distribution.py`(16)、`test_embedded_browser_package.py`(17)、`test_release_assembly.py`、`test_p9_07_acceptance.py`、`test_eb_17_clean_machine.py`、`test_motion_video_render_adapter.py`(`LOCKED_MAJOR=149`)、`test_motion_video_render_sandbox.py`、`test_third_party_notice_ui_projection.py` | ~110 处断言 |
| Rust | `tests/embedded_browser_distribution.rs`(21)、`..._windows.rs`、`embedded_browser_authority.rs`、`..._windows.rs` | ~45 处 |
| Node/TS | `frontend/tests/embedded-browser-target-packaging.test.mjs`、`eb-04-windows-staging.test.mjs`、`eb-05-cross-platform-acceptance.test.mjs`、`third-party-software-notice.test.ts` | ~12 处 |
| 上游名泄漏门禁 | `frontend/e2e/upstream-name-leak.spec.ts` 等 6 个文件把 `"chromium"` 当作**禁止出现在用户文案里**的词——内置浏览器改叫 Chromium 后要重新界定这条规则 | 需重新设计 |

### 6.5 最重的一项：EB-01/EB-02 的架构前提被推翻

现在的设计是「**内置浏览器就是 Playwright 锁定的那一个**」，
`check_embedded_browser_compatibility.py:202-217` 把**安装的 Playwright 的 `browsers.json`
里 chromium 那条**（`name`/`title`/`browser_version`/`revision`/`installByDefault`）
与契约做**全等比较**。Playwright 1.61.0 说的就是 `title="Chrome for Testing"`,
`browser_version="149.0.7827.55"`, `revision="1228"`。

一旦内置的是 Chromium 152（或 142），契约里就没法同时表达
「Playwright 期望什么」和「我们实际内置什么」——必须把这个契约拆成两段，
并且**放弃「内置浏览器 = Playwright 锁定浏览器」这条 CDP 兼容性保证**。
这不是改值，是改设计，会波及 EB-01、EB-02、EB-05、EB-16、BU-02 的完成定义。

### 6.6 需要重新验收的门禁

`EB-01`～`EB-05`、`EB-16`、`EB-17`、`BU-02`、`BU-07`、`BM-03/04/08/16`、`P9-07`、`CQ-04`，
其中 EB-16/EB-17/BU-07 需要 macOS 与 Windows **各一次正式包 + 干净机**验收。

### 6.7 工作量估计

| 方案 | 代码/契约改动 | 重新验收 | 估计 |
| --- | --- | --- | --- |
| A. 换 Chromium 快照 | 约 200 处断言 + 2 处设计变更（staging 裁剪、兼容性契约拆分） | 上述 14 个任务 × 双平台 | **10～15 人日，且带一个已知功能倒退（播放）和一个未定的 Windows 体积超限问题** |
| C. 剔除 Widevine（推荐） | staging 契约新增一个「分发前剔除路径」白名单 + 重算 3 个 `archive_sha256` 的下游摘要 + `RELEASE_PAYLOAD_PARTS_MIB` 343→324 + licence/SBOM 文案 + 对应测试 | EB-03/EB-05/EB-16（macOS 双架构，Windows 无此文件不受影响） | **1～2 人日** |

---

## 7. 可行性判断

### 判断：**不建议换成 Chromium 开源构建。**

理由按权重排序：

1. **任务假设的那条路（Playwright 的 Chromium 构建）在 macOS/Windows 上不存在**——
   实测 404，Playwright 已在 143 前后主动停产（第 1 节）。
2. **所有 Chromium 开源构建都没有 H.264/AAC**（两个独立来源交叉验证），
   实测导致**抖音播放器彻底失效**：黑屏、`NETWORK_NO_SOURCE`、CDN 无限重试（第 3 节）。
   RPA 的点击链路不受影响，但发布链路的封面/时长提取很可能受影响（未验证，见第 8 节）。
3. **体积不但没省，Windows 还会超限**：mac 多 6 MB，Windows 剔掉测试二进制后仍 445 MiB > 420 MiB 上限。
4. **Chromium 快照没有稳定版本号**，只有 trunk commit position，Google 明说不面向终端用户，
   会把「版本锁定 + 逐文件摘要 + 可复现分发」这套 EB 体系的可解释性显著削弱。
5. **换过去也解决不了根本担心**：实测表明 **CfT 在 `sec-ch-ua` 里报的就已经是 "Chromium"**，
   平台识别上本来就没有 "Google Chrome" 这个信号。换浏览器换掉的是**品牌文件名**，
   不是**被网站看到的身份**。
6. **代价 10～15 人日 + 14 个门禁双平台重验收**，换来一个功能倒退。

### 推荐：Option C —— 留在 Chrome for Testing，剔除 Widevine CDM

具体动作：

1. 在 `contracts/browser/embedded-chromium-staging.v1.json` 的每个 target 增加
   `excluded_entry_prefixes`（macOS 两个目标填 `<root>/Google Chrome for Testing.app/Contents/
   Frameworks/Google Chrome for Testing Framework.framework/Versions/149.0.7827.55/Libraries/WidevineCdm/`；
   Windows 为空数组）；`archive_sha256` 不变（仍然锁原始归档），
   staging 在解包后按该白名单**显式剔除**并把剔除动作写进 `staging-manifest.json`。
2. `build_embedded_browser_distribution.py` 的 licence block 从
   `"redistribution_review": "pending"` 改成有依据的记录：
   声明已剔除的专有组件清单 + 剩余组件的许可结论。
3. `RELEASE_PAYLOAD_PARTS_MIB["embedded-chromium"]` 343 → **324**（实测 339,257,128 B = 323.6 MiB），
   连带包上限重算。
4. 重跑 EB-03 / EB-05 / EB-16（macOS arm64 + x86_64）。Windows 无此文件，只需确认 staging 剔除列表为空时行为不变。

**这一步解决的是唯一一条有书面禁令的风险，代价 1～2 人日，零功能损失。**

### 仍未解决、需要用户决策的部分

品牌与 CfT 的 ToS 定位问题**不在本轮能力范围内**。剔除 Widevine 之后，剩下的问题是：
产物仍带 `Google Chrome for Testing` 商标名。这是一个法律判断，不是技术判断。
可选路径：

- **C1**：接受现状，把结论写进 `redistribution_review`（需要用户拍板承担该风险）；
- **C2**：找一次外部法务意见（哪怕是一次性咨询），只问「CfT 二进制随商业桌面软件分发」这一个问题；
- **D**：自建 Chromium 并开启 `proprietary_codecs=true` / `ffmpeg_branding="Chrome"`——
  得到无 Google 品牌又有 H.264 的构建，但**把 AVC/AAC 的专利许可责任转移到我们自己头上**
  （Google 现在替我们扛着这部分），且三平台每个 Chromium 版本都要多小时构建 + ~100 GB 磁盘，
  对当前团队规模不现实；
- **E**：不内置浏览器、要求用户自装 Chrome——直接违反 CLAUDE.md 第 5 节和 EB-17 干净机验收，
  否决。

---

## 8. 我没能验证的部分

必须说清楚，以下都是**没实测**的，不要当结论用：

1. **发布链路（creator.douyin.com）在缺 H.264 时的行为**。需要真实账号登录才能到上传页，
   本轮明确不登录。风险推断（**未验证**）：创作者上传页通常用
   `URL.createObjectURL` + `<video>` 读取本地文件的 `duration` 并 canvas 抽封面帧，
   缺 H.264 解码时 `loadedmetadata` 不触发 → 时长为 `NaN`、封面抽不出来 → 可能阻塞发布。
   如果真要走 Option A，这一项必须先在受控账号上验证。
2. **有头（`headless=false`）模式下的抖音行为**。本轮按要求全程无头，
   而产品实际以可见窗口运行。无头下两个浏览器都被「验证码中间页」拦截，
   有头 + 持久 Profile 的真实链路没有对比数据。
3. **长会话风控评分**。Chromium 版本反复重试 CDN 会产生额外请求，
   是否抬高抖音的风险评分——无法在不登录、不长跑的前提下测量。
4. **版本偏差与构建差异的解耦**。对比的是 CfT 149 与 Chromium 152/142，
   没有同版本号的两个构建可对照，所以「元素数 3114 vs 2873」里有多少来自版本、
   多少来自编解码器，无法分离。编解码器矩阵本身是直接测量的，不受此影响。
5. **Windows 与 macOS x86_64 上的实际运行**。只在 macOS arm64 上真实启动过浏览器；
   Windows 与 mac x64 的数据全部来自归档远程清单（HTTP Range 读 zip 中央目录），
   没有解包也没有运行。
6. **`interactive_ui_tests.exe` 之外 Windows 快照是否还有其他不该分发的东西**。
   只看了体积前 12 名。
7. **CfT / Chrome ToS 的法律结论**。不做法律判断。本文只陈述查到的事实：
   Widevine 的 LICENSE 明文禁止无协议分发；其余部分我没有找到随包分发的 ToS 文件
   （macOS CfT 树里除 `WidevineCdm/LICENSE` 外没有任何 license/terms 文件，
   Windows CfT 树里一个都没有），完整第三方声明在 `chrome://credits`。

---

## 9. 实验方法与清理

| 实验 | 脚本（会话临时目录，未入库） | 产物 |
| --- | --- | --- |
| CDN 可用性 / revision 边界 | `curl -sIL` | 第 1 节表格 |
| 归档远程清单（不下载全量） | HTTP Range + `zipfile` 读中央目录 | 第 2.2 节体积表 |
| 能力与身份对比 | Playwright + 一次性 Profile，无头 | `report.json` / `report2.json` |
| 抖音播放行为 | 4 × 5s 采样 + 截图 | `report3.json`、`*3-detail.png` |
| Widevine 剔除 | 复制树 → 删目录 → codesign → 启动 → 抖音 | `prune-report.json` |

- 下载与解包全部位于 `.local/chromium-eval/`，**验证结束已整目录删除**；
- 每次浏览器都用 `tempfile.mkdtemp()` 建一次性 Profile，`finally` 里
  `context.close()` → `playwright.stop()` → `shutil.rmtree(profile)`，
  成功/失败/异常三条路径都清理；跑完 `pgrep` 复核无残留浏览器进程；
- **未接触** `~/Library/Application Support/com.aventador.automationtool/embedded-browser-profiles/`，
  未登录抖音，未在抖音页面做任何点击或输入；
- **未修改**任何契约、产品代码或台账，未 `git add`/`commit`；
- 未触碰 `.local/eb-16/` 与 `~/Library/Caches/automation-tool-build/`。
