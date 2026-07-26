# FIX 用开源字体替换随包分发的专有中文字体

> 状态：🔍 待验收（契约、门禁、法务页与冻结包真实成片已通过；缺一次完整正式包重建与
> Windows 侧验收）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：`FIX-material-worker-package-size.md` 第 5 节第 2 条登记的遗留缺陷——
> 智能素材成片 Worker 随商业安装包再分发 141.8 MiB 的 Apple / 微软专有字体。

## 缺陷

`vendor/moneyprinterturbo/resource/fonts` 里有四个专有系统字体，随正式安装包一起分发：

```text
STHeitiLight.ttc          55,754,164 B   Apple 华文黑体（随 macOS 分发的系统字体）
STHeitiMedium.ttc         55,783,456 B   同上
MicrosoftYaHeiNormal.ttc  19,701,556 B   微软雅黑（随 Windows 分发）
MicrosoftYaHeiBold.ttc    16,880,832 B   微软雅黑（上游默认字幕字体）
                          148,120,008 B  合计 141.3 MiB
```

macOS 与 Windows 的系统字体授权都不允许第三方把字体文件抽出来放进自己的商业安装包再分发。
这同时直接违反项目 CLAUDE.md 第 6 节的资产权利要求：
`contracts/quality/asset-rights-policy.v1.json` 的 `defaultDecision` 是 `deny`，
而这四个文件从未登记过。

**关键约束**：`MicrosoftYaHeiBold.ttc` 不是摆设，它是产品实际在用的默认字幕字体
（`FIX-material-worker-audio-assets.md` 的成片日志：`⑤ font: ./resource/fonts/MicrosoftYaHeiBold.ttc`）。
先删后补会让字幕直接失去可用字体，所以本次的顺序是**先接上替代字体，再删专有字体，
最后由门禁保证两件事只能同时成立**。

## 选型：为什么是 Noto Sans CJK SC，以及为什么不是 Google Fonts 上的那一份

目标是「Google 系开源中文黑体 + 完整字符集 + SIL OFL」。候选逐个实测，判据是
**上游实际的调用方式**：`webui/Main.py:1119 get_all_fonts()` 只列出文件名以 `.ttf`/`.ttc`
结尾的字体，`app/services/video.py:1032` 用 `ImageFont.truetype(font_path, size)` 加载，
**不传 index，也不设可变字体轴**。

| 候选 | 结论 |
| --- | --- |
| `google/fonts` 的 `ofl/notosanssc/NotoSansSC[wght].ttf` | ❌ 可变字体，`fvar` 默认轴值 **wght=100（Thin）**。FreeType 不设轴时就渲染默认实例，上游又不会调 `set_variation_by_name`，实测字幕是发丝般的细体 |
| `ofl/notoserifsc/NotoSerifSC[wght].ttf` | ❌ 同样问题，默认 wght=200 |
| `noto-cjk` 的 `Sans/OTC/NotoSansCJK-Bold.ttc` | ❌ 后缀合规，但 OTC 里 **index 0 是日文（Noto Sans CJK JP）**，PIL 默认取 index 0，中文会拿到日文字形变体 |
| `noto-cjk` 的 `Sans/OTF/SimplifiedChinese/NotoSansCJKsc-{Regular,Bold}.otf` | ✅ 静态字体、简体中文字形、完整 CJK 字符集、OFL-1.1。唯一问题是后缀 `.otf` 上游列不出来 |

选定：**`Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf` 与 `-Bold.otf`，
锁定在 `notofonts/noto-cjk` 的 `Sans2.004` 发行标签**。默认字幕字体是 Bold，与被替换的
`MicrosoftYaHeiBold.ttc` 同一字重。

按用户要求**不做字形裁剪**：用的是完整的 language-specific OTF（16.4 MB / 17.0 MB），
不是 8.5 MB 的 `SubsetOTF`，也没有做任何子集化。

### 后缀这件事说清楚

上游只列 `.ttf`/`.ttc`，而 Noto CJK 官方只以 `.otf`（OpenType/CFF）发布单语言静态字体。
本次的处理是：**字节一个不改，只改文件名后缀**，并且把这件事写进契约、留下可核对的证据：

- `contracts/quality/asset-rights-policy.v1.json` 里每条字体登记同时记着
  `sourceUrl`（指向官方 `.otf`）、`upstreamFileName`、`packagedName` 和 `fileNameNote`；
- 登记的 `sha256` **就是官方 `.otf` 的摘要**，任何人下载官方文件一算就能验证字节一致。

没有选的两条路及理由：

- **接受 Thin 可变字体**——字幕观感明显变弱，是可见的质量倒退；
- **用 fontTools 把可变字体实例化成静态 Bold**——那属于 OFL 定义的 Modified Version，
  要承担改名与派生声明义务，还要引入字体加工构建步骤。为了一个后缀过滤器去改字体二进制，
  代价和风险都比改个文件名大。

OFL 对「改格式」才判定为 Modified Version；改文件名不改字节不构成格式变更。

## 许可证义务如何履行

SIL OFL 1.1 只要求三件事，本次逐条落实：

| 义务 | 落实方式 |
| --- | --- |
| 随字体附带版权声明 | 字体 `name` 表 nameID 0 自带 `© 2014-2021 Adobe (http://www.adobe.com/).`；法务页另外单独公示这一行 |
| 随字体附带许可证正文 | 包内 `_internal/upstream/resource/fonts/NotoSansCJK-LICENSE.txt`（noto-cjk 仓库 `LICENSE` 原字节）；App 内「开源软件许可」页可直接展开全文 |
| 不得单独出售字体本身 | 不适用 |
| 修改后须改名并仍用 OFL | 不适用（未修改） |

OFL **不要求署名**，所以页面上没有加任何「字体由某某提供」式的宣传语。

值得单独记一笔：OFL 正文是**模板**，它自己不写版权人（MIT 与 Apache-2.0 的正文里写了）。
因此「只展示许可证正文」只满足了 OFL 第 1 条的一半，版权行必须另外公示。
这就是本次给 `distributedComponents` 增加 `copyright` 字段的原因，也是新增门禁规则的依据。

## RED

三段，每段都实际跑到看见红。

```text
# RED 1 —— 打包与权利模块不存在
$ python3 scripts/test_material_video_worker.py
ModuleNotFoundError: No module named 'subtitle_font_assets'

# RED 2 —— 模块与契约就位后，构建审计与运行时默认值仍然缺失
$ python3 scripts/test_material_video_worker.py
AttributeError: module 'build_material_video_worker_candidate' has no attribute
'excluded_upstream_resource_files'
AttributeError: module 'build_material_video_worker_candidate' has no attribute
'assert_excluded_upstream_resource_files_absent'
AssertionError: 'excludedUpstreamResourceFiles' not found in '# -*- mode: python ...'
Ran 34 tests — FAILED (failures=1, errors=8, skipped=1)

# RED 3 —— 法务页：投影漂移，前端连模块都加载不起来
$ python3 scripts/check_third_party_notice_ui_projection.py
third-party notice ui projection check failed: candidate projection drifted from the
locked source, asset rights and motion rights contracts

$ cd frontend && npx vitest run src/features/legal
Error: third-party notice: subtitle-fonts points at a licence text the App does not ship
 Test Files  2 failed (2)      Tests  no tests

# RED 4 —— 字体改为构建期获取后，仓库路径模型整体转红
$ python3 scripts/test_material_video_worker.py
subtitle_font_assets.SubtitleFontRightsError: font-noto-sans-cjk-sc-bold: path must be
a non-empty repository-relative path
Ran 45 tests — FAILED (failures=3, errors=19, skipped=1)

# RED 5 —— 体积上限与新的声明负载脱节
$ python3 scripts/test_embedded_browser_package.py
FAIL: test_release_size_bounds_admit_the_declared_production_payload
AssertionError: 1331691520 not less than or equal to 1206491545
Ran 35 tests — FAILED (failures=1)
```

用例条数逐次核对：`test_material_video_worker` **14 → 34 → 45**（先 +20 覆盖替换与排除，改成构建期获取后再 +11 覆盖获取、校验与「字体不得进仓库」）；
`frontend/src/features/legal` **43 → 47**（+4，3 条模型 + 1 条页面）；
`test_embedded_browser_package` 35 条不变（原有那条按声明负载双向夹住的用例直接转红）；
`test_third_party_notice_ui_projection` 是单文件断言脚本，新增了字体组件、
OFL 正文与「OFL 组件缺版权行」三类断言。

### RED 自检：新规则不是摆设

把 `check_third_party_notice_ui_projection.py` 的 OFL 规则临时改成恒不触发后重跑，
`obligation_failure("OFL component with no copyright notice")` 立刻报
`obligation check missed a ...`，改回即复绿。

资产权利门禁同样做了真实篡改验证（改完立即还原，`git diff` 只剩本次预期新增）：

```text
$ python3 scripts/check_third_party_sources.py     # attribution 改成别人
third-party source check failed: font-noto-sans-cjk-sc-bold: the registered attribution
is not the copyright notice the font itself carries

$ python3 scripts/check_third_party_sources.py     # sha256 改成全 0
third-party source check failed: font-noto-sans-cjk-sc-bold: the font bytes drifted
from the registered digest
```

## GREEN

```text
python3 scripts/test_material_video_worker.py            Ran 45 tests  OK (skipped=1)
python3 scripts/test_embedded_browser_package.py         Ran 35 tests  OK
    （收尾复跑时该文件出现 1 条失败：`ProductionPackageAuditTests
      .test_release_binary_without_test_markers_passes`，报
      `Production assets cannot reach a required capability:
       restore_product_account_session`。与本次无关——另一个会话在 11:39 改了
      `frontend/scripts/audit-production-package.mjs` 新增了这项必需能力，而对应的
      Python 夹具尚未同步。本次改动所属的 `EmbeddedBrowserPackageTests` 21 条全绿。）
python3 scripts/test_third_party_notice_ui_projection.py third-party notice ui
                                                         projection tests passed
python3 scripts/check_third_party_notice_ui_projection.py
    2 upstream projects (MoneyPrinterTurbo, hyperframes), 6 distributed components
    (2 copyleft: ffmpeg, x264), 4 licence texts shipped in the App, 9 borrowed packages,
    no review detail or address leakage
python3 scripts/check_third_party_sources.py
    third-party source locks, licenses, rights policy and SBOM are valid
    (2 redistributable fonts registered)
python3 scripts/test_video_runtime_cache.py              Ran 12 tests  OK
python3 scripts/check_user_facing_branding.py
    user-facing branding and plain-language scan passed (51 frontend, 250 native files)
python3 scripts/test_user_facing_branding.py             CQ-01 gate tests passed
python3 scripts/check_embedded_browser_video_roadmap.py  specialized roadmap status and
                                                         per-task evidence are valid
cd frontend && npx tsc -b                                rc=0
cd frontend && npx eslint .                              rc=0
cd frontend && npx vitest run src/features/legal          47 passed (47)
cd frontend && npx vitest run（全量回归）
    Test Files  59 passed (59)    Tests  478 passed | 2 expected fail (480)
```

## 交付

### 字体不进 Git：构建期按摘要获取（第四份缓存运行时资产）

**仓库里没有任何字体二进制。** 33.4 MB 的二进制一旦进 Git 历史就不可逆——想删要重写历史；
而本产品所有大体积锁定资产早就避开了这条路：内置 Chromium 是构建期按摘要下载、
ffmpeg 是构建期按锁定源码编译。字体走同一条路，而不是成为唯一的例外。

复用现成机制，没有新写一套：`scripts/video_runtime_cache.py` 的 `ensure_cached()`
提供「按锁定契约摘要索引、变则重建、不变则复用」，字体成为它的**第四份**资产
（前三份是 `media-toolchain`、`motion-video-worker`、`material-video-worker`），
缓存目录 `subtitle-fonts` 落在 `cache_root()`——仓库之外，不会被仓库级清理扫掉。

| 缓存内文件 | 字节 | SHA-256 | 锁定下载地址 |
| --- | ---: | --- | --- |
| `NotoSansCJKsc-Bold.ttf` | 17,002,248 | `b5f0d1a1…9b4c0a` | `Sans2.004` 的 `Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Bold.otf` |
| `NotoSansCJKsc-Regular.ttf` | 16,437,364 | `2c76254f…65b74b` | 同目录 `-Regular.otf` |
| `NotoSansCJK-LICENSE.txt` | 4,301 | `6a73f954…fe2bf2` | `Sans2.004` 的仓库根 `LICENSE` |

实测：冷启动首次获取 **27.1 秒**，缓存复用 **0.051 秒**。

**校验必须在使用前，且没有任何回退**：

- 下载地址必须以 `FONT_SOURCE_URL_PREFIX`
  （`https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/`）开头。
  这个前缀写在代码里而不是只写在契约里——改写契约里的 `sourceUrl` 无法把构建悄悄指向别处；
- 每个文件**先下载到内存 → 校验长度 → 校验 SHA-256 → 校验字体自带版权行 → 才写入缓存**；
- 任何一步失败都抛 `SubtitleFontUnavailable`，`ensure_cached()` 随即删除半成品目录并
  不写 stamp，下次重新获取。**不回退到系统字体，也不跳过字幕**——那两条路都会产出
  「看起来完成、中文全是方框」的视频，正是要防的静默失败。

### 资产权利登记（`contracts/quality/asset-rights-policy.v1.json`）

`entries` 从空数组变成两条 `font` 记录，字段覆盖 `distributionRequiredFields` 的 12 项
与 `requiredCategories.font` 的 6 项，另加 `bytes` / `packagedName` / `upstreamFileName` /
`fileNameNote` / `licenseTextUrl` / `licenseTextSha256` / `licenseTextBytes` /
`packagedLicenseName` / `bundledIn`。你已经登记的 `sha256`、`upstreamFileName`、
`fileNameNote` 正好就是下载校验需要的全部信息，结构没有为下载改动。

**这份登记同时是打包清单**：`defaultDecision: deny` 的字面含义就是「没登记的资产不许进包」，
所以打包、构建审计、法务页投影和权利门禁全部从这一处读，不维护第二份名单。

### 共用模块（`scripts/subtitle_font_assets.py`，新增）

分成「声明层」与「字节层」两级，声明层完全离线，字节层负责获取与校验：

- `bundled_subtitle_fonts()` / `packaged_license_notice()`（**离线，不碰网络也不读字体**）：
  校验权利字段齐全、三项许可（再分发 / 商用 / 嵌入）必须为真、许可证必须是 OFL-1.1、
  `packagedName` 必须是不含路径的 `.ttf`/`.ttc` 文件名、`sourceUrl` 必须在锁定前缀内、
  摘要必须是 64 位小写十六进制。权利门禁和法务页投影只用这一层，因此都不需要网络；
- `font_copyright_notice(payload)`：解析字体 sfnt `name` 表的 nameID 0；
- `verify_font_payload()` / `verify_license_payload()`：长度、摘要、**以及登记里的
  `attribution` 必须与字体自带版权行逐字相等**——版权行始终是从二进制里读出来的，不是手抄的；
- `ensure_subtitle_fonts()`：走 `ensure_cached`，`contracts=[asset-rights-policy.v1.json]`，
  改一条字体登记就重新获取；
- `default_subtitle_font_name()`：读 Worker 契约的 `build.defaultSubtitleFontName`，
  并要求它是已登记字体之一。

### 构建期装配（`scripts/prepare_video_runtime.py`）

新增第四步 `ensure_subtitle_fonts(root=staging)`，排在 `material-video-worker` **之前**——
Worker 的 PyInstaller spec 要打包这些字体。同时把 `asset-rights-policy.v1.json` 加进
`material-video-worker` 的缓存键，于是**换一份字体也会重建 Worker**，不会复用旧产物。

`build_material_video_worker_candidate.build_candidate()` 也在开头调用一次：直接构建候选
（不经 `prepare_video_runtime`）时，字体不可用要在二十分钟的 PyInstaller 之前就报出原因，
而不是跑完才失败。

### 打包（`workers/material_montage/material-video-worker.spec`）

- `resource` 目录从「按顶层条目整目录打包」改成**逐文件打包**，跳过
  `build.excludedUpstreamResourceFiles` 列出的四个专有字体（原来的 `excludedUpstreamResources`
  只能整目录排除，而 `fonts` 目录本身必须留下）；
- 从权利登记读入两个开源字体与 OFL 正文，装进 `upstream/resource/fonts/`；
- 把 `material-video-worker-package.v1.json` 装进 `contracts/`，供运行时读默认字幕字体。

### 构建审计（`scripts/build_material_video_worker_candidate.py`）

`audit_candidate()` 新增两道失败关闭检查：

- `assert_excluded_upstream_resource_files_absent()`：候选里只要还有那四个专有字体就拒绝并点名；
- `assert_bundled_subtitle_fonts_present()`：两个开源字体必须在位且 SHA-256 与登记一致，
  OFL 正文必须在位，默认字幕字体必须在位。**这一条是防「删了旧的没接上新的」的那道闸**——
  少了它，产物照样能启动、照样能探针通过、照样能出片，只是每个中文字幕都是方框。

### 运行时默认值（`workers/material_montage/webui_runtime.py`）

上游 `webui/Main.py` 把 `MicrosoftYaHeiBold.ttc` 写死成默认值，取不到时退回目录里
**字母序第一个**字体（清理后是 `BeVietnamPro-Bold.ttf`，纯拉丁字体，中文全方框）。
`webui/Main.py` 是只读 Submodule，所以走上游自己支持的入口：

- `_private_config_document()`：把 `font_name = "<契约里的默认字体>"` 插到上游
  `config.example.toml` 的 `[ui]` 表头下一行，其余内容、注释和顺序逐字保留
  （不走 TOML 序列化往返，避免把上游新增选项吞掉）；
- `_preload_private_config()` 除了照旧写 `config.example.toml`，还直接写出 `config.toml`。
  上游 `load_config()` 只在 `config.toml` 不存在时才复制样例，所以这一步就是让新默认值生效；
- 字体名做了注入防御：必须是不含路径分隔符、引号、反斜杠和换行的裸文件名。

### 法务页（`contracts/quality/third-party-notice-ui.v1.json` 与 `frontend/src/features/legal/`）

按 `FIX-open-source-license-obligations.md` 已有的结构扩展，没有另起一套：

- 新增第 6 个 `distributedComponents` 条目 `subtitle-fonts`（Noto Sans CJK SC / Sans2.004 /
  OFL-1.1），`packagedNoticePath` 指向包内 OFL 正文；
- 新增第 4 份随 App 分发的许可证正文 `ofl-1.1.txt`，与包内那份逐字节相同；
- `distributedComponents` 新增可空的 `copyright` 字段，只有 OFL 组件非空，值由投影构建器
  从字体二进制的 `name` 表派生；
- 门禁新增第 4 条义务规则：**OFL 组件必须同时公示版权行**，因为 OFL 正文本身不写版权人；
- `_scan_for_leakage` 把 `copyright` 与既有的 `upstreamSourceUrl` 一并豁免地址扫描——
  字体厂商习惯把自己的网址写进版权行，为了过扫描去截断它等于破坏许可证义务本身；
- `_contract_declared_paths()` 把包内 OFL 正文路径纳入「契约声明的路径」，
  于是页面上写的路径和安装器真正写出的文件来自同一处事实。

`assetRights.registeredEntryCount` 随之从 0 变成 2，页面文案也从「尚未随安装包分发任何
第三方素材」改成「已登记 2 条」，并保留「没有登记齐权利信息的素材一律不随安装包分发」这句。

### 权利门禁（`scripts/check_third_party_sources.py`）

`validate_asset_rights()` 原来只校验结构，登记内容一条都不看。现在它重新推导每一条字体登记，
权限缺失、字节漂移、版权行与字体不符都会失败关闭，并在通过时打印登记数量。

### 体积上限（`scripts/check_embedded_browser_package.py`）

`RELEASE_PAYLOAD_PARTS_MIB["material-video-worker"]` 由 465 → **353**（实测 369,452,859 B）。
上限按声明负载重新推导：

```text
343 (Chromium) + 177 (执行器) + 353 (素材成片 Worker) + 108 (动效 Worker)
  + 43 (媒体工具链) + 22 (App 壳与前端资源)  =  1046 MiB
max_package_bytes = 1146 MiB  ≈ 声明负载 + 9.56%（测试夹在 +10% 以内）
```

## 替换前后对比

| | 替换前 | 替换后 | 差 |
| --- | ---: | ---: | ---: |
| Worker 文件数 | 2108 | 2108 | 0（去 4 个字体，进 2 字体 + 1 许可证 + 1 契约） |
| Worker 体积 | 484,123,149 B（461.7 MiB） | 369,452,875 B（352.3 MiB） | **−114,670,274 B（−109.4 MiB，−23.7%）** |
| 声明生产负载 | 1158 MiB | 1046 MiB | −112 MiB |
| 包体上限 | 1270 MiB | 1146 MiB | −124 MiB |
| 随包分发的专有字体 | 4 个，148,120,008 B | 0 | 全部移除 |
| 已登记可分发资产 | 0 条 | 2 条 | +2 |

构建器审计原文（字体走构建期获取后的重建）：

```text
智能素材成片本机服务候选已通过：2108 files, 369452875 bytes, startup 13.647s,
Python 3.11.15, 112 dependencies
```

包内 `_internal/upstream/resource/fonts` 复查：

```text
BeVietnamPro-Bold.ttf       139,012
BeVietnamPro-Medium.ttf     134,668
Charm-Bold.ttf              135,332
Charm-Regular.ttf           134,560
NotoSansCJK-LICENSE.txt       4,301
NotoSansCJKsc-Bold.ttf   17,002,248   sha256 b5f0d1a1…4c0a（与登记一致）
NotoSansCJKsc-Regular.ttf 16,437,364  sha256 2c76254f…5b74b（与登记一致）
UTM Kabel KT.ttf             35,456
```

四个 `.ttc` 专有字体全部消失。

## 真实成片验收（裁剪后的冻结包，正常用户路径）

按生产方式启动 2108 文件的**冻结候选**：向可执行文件 stdin 写一条与
`local_video_orchestrator.rs::write_bootstrap` 完全同构的 bootstrap
（`enableWebUi: true`、64 位十六进制会话令牌、`renderBrowser: null`、
`assetRoot` 指向 `video-workspaces-v1/jobs/<uuid>/work`），读回 ready 事件：

```text
READY_EVENT={"authenticationProof":"atvwp1.s8n_lJBGS4zn5H1mDxvbQnNFr1Nsk-qakgvU98xdNiI",
"event":"worker.ready","port":62160,"protocolVersion":"1.0","scriptModelId":null,
"webUiAuthenticationProof":"atvwp1.5q-_rQ-GyI6Q7Myj7FVpGajyK8kwu-slrNIQsc2giTA",
"webUiPath":"studio-bTyWi4Ko25LIY3tOo_8ru0E0ZLhcaq3SFzrcIfNNRLE","webUiPort":62132,
"workerKind":"python","workerVersion":"1.3.2"}
```

这份候选的字体是**构建期从锁定地址获取、校验摘要后打进包的**，仓库里没有字体二进制。
包内三个文件的 SHA-256 与权利登记逐字节一致：

```text
NotoSansCJKsc-Bold.ttf     17,002,248  b5f0d1a190a7f9b43c310a8850630af12553df32c4c050543f9059732d9b4c0a
NotoSansCJKsc-Regular.ttf  16,437,364  2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b
NotoSansCJK-LICENSE.txt         4,301  6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2
```

任务私有运行目录里的配置与字体目录，证明契约里的默认值一路传导到了运行时：

```text
config.toml
  [ui]
  font_name = "NotoSansCJKsc-Bold.ttf"
  # Hide generation logs in the WebUI.
  hide_log = false

resource/fonts/
  BeVietnamPro-Bold.ttf   BeVietnamPro-Medium.ttf   Charm-Bold.ttf   Charm-Regular.ttf
  NotoSansCJK-LICENSE.txt NotoSansCJKsc-Bold.ttf    NotoSansCJKsc-Regular.ttf
  UTM Kabel KT.ttf
```

再用无头浏览器按真实用户路径操作这个 WebUI：关闭引导 → 填视频主题与文案（含生僻字
龘犇骉麤）→ 视频来源「本地文件」→ 上传三段用随包 ffmpeg 现造的 1080×1920 素材 →
点「生成视频」。**字幕设置整块保持默认不动**，专门验证新的默认字体。
Worker 内部链路原文（从 WebUI 日志面板逐字读回）：

```text
app.services.task:start:333 - start task: 1b2f8415-3ed1-47bd-aed2-46b88f9e30bd, stop_at: video
app.services.video:generate_video:962 - generating video: 1080 x 1920
app.services.video:generate_video:963 -   ① video: ./storage/tasks/1b2f8415-.../combined-1.mp4
app.services.video:generate_video:964 -   ② audio: ./storage/tasks/1b2f8415-.../audio.mp3
app.services.video:generate_video:965 -   ③ subtitle: ./storage/tasks/1b2f8415-.../subtitle.srt
app.services.video:generate_video:966 -   ④ output: ./storage/tasks/1b2f8415-.../final-1.mp4
app.services.video:generate_video:981 -   ⑤ font: ./resource/fonts/NotoSansCJKsc-Bold.ttf
```

`⑤ font: ./resource/fonts/NotoSansCJKsc-Bold.ttf` 就是本次的核心证据：
渲染器真正打开的是开源字体，而不是任何专有字体，也没有退回到拉丁字体。

任务私有运行目录里的 `config.toml` 与 `resource/fonts` 已在上面列出，
用户全程没有碰过字幕设置。

产品侧最终状态（Rust 桥实际读取的两处）：

```text
work/.automation-tool-webui/<capability>/material-render-job-observation.json
{"failureCode":null,"outputFile":"material-result.mp4","progressPercent":100,
 "renderJobId":"dcb6e4fa-3ca7-4692-836e-dff327bd05db","revision":9,"schemaVersion":1,
 "status":"succeeded",
 "subject":"换成开源字体后中文字幕依然正常渲染。生僻字龘犇骉麤也不会变成方框。",
 "workerTaskId":"1b2f8415-3ed1-47bd-aed2-46b88f9e30bd"}

ffprobe outputs/material-result.mp4
  index=0  codec_name=h264  codec_type=video  width=1080  height=1920
  index=1  codec_name=aac   codec_type=audio
  duration=8.000000  size=253187

subtitle.srt
  1
  00:00:00,100 --> 00:00:03,775
  换成开源字体后中文字幕依然正常渲染

  2
  00:00:04,350 --> 00:00:07,412
  生僻字龘犇骉麤也不会变成方框
```

### 中文字幕渲染核对（本次的验收重点）

从成片里抽第 1.5 秒和第 5.5 秒的画面逐帧看：

- 第 1 条字幕「换成开源字体后中文字幕依然正常渲染」全部正常成字，白字黑描边，
  字重与被替换的 `MicrosoftYaHeiBold.ttc` 一致；
- 第 2 条字幕「生僻字**龘犇骉麤**也不会变成方框」里四个生僻字**全部正常渲染出笔画**，
  没有出现任何 `□` 豆腐块——这正是「不裁字形子集」这条约束要保住的东西。

用上游自己的字形探测逻辑（`app/services/video.py:subtitle_font_supports_text`，
即 PIL `getmask` 与 `U+10FFFF` 缺字掩码比对）对三个字体跑同一段样本文本：

```text
MicrosoftYaHeiBold.ttc（被移除）      -> missing glyphs: none
NotoSansCJKsc-Bold.ttf（新默认）      -> missing glyphs: none
NotoSansCJKsc-Regular.ttf（新增可选） -> missing glyphs: none
```

样本包含常用汉字、生僻字（龘犇骉麤昶玥甯頔珺燚鑫淼焱垚）、中文标点、全角符号、
拉丁字母与数字。

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 契约没声明排除文件清单 | 构建拒绝「排除上游资源文件契约缺失」 | 合成 |
| 排除清单里混入非两段路径、`..` 或绝对路径 | 构建拒绝「排除上游资源文件契约无效」 | 合成 |
| 候选仍带任一专有字体 | 拒绝并点名 | 合成 |
| 候选缺少已登记的开源字体 | 拒绝并点名 | 合成 |
| 候选里的字体字节被替换 | 拒绝并点名（SHA-256） | 合成 |
| 候选缺少 OFL 正文 | 拒绝并点名 | 合成 |
| 候选缺少默认字幕字体 | 拒绝并点名 | 合成 |
| 权利登记缺任一必填字段 | 权利门禁拒绝并点名字段 | 合成 |
| 权利登记声明不允许再分发 | 权利门禁拒绝 | 合成 |
| 权利登记的 `packagedName` 试图逃出字体目录 | 权利门禁拒绝 | 合成 |
| 权利登记的下载地址不在锁定上游前缀内 | 声明层拒绝 | 合成 |
| 下载到的字节与登记摘要不符 | 获取失败关闭，缓存目录被删除 | 合成 + **真实网络篡改实测** |
| 下载到的字体自带版权行与登记不符 | 获取失败关闭 | 合成 |
| 上游不可达 / 网络中断 | 获取失败关闭，不回退系统字体、不跳过字幕 | 合成 |
| 半途失败留下残缺字体缓存 | `ensure_cached` 删除半成品且不写 stamp | 合成 + **真实网络篡改实测** |
| 包内字体自带版权行与登记不符 | 构建审计拒绝并点名 | 合成 |
| 换一份字体后复用旧 Worker 产物 | Worker 缓存键含权利登记摘要，自动重建 | 合成 |
| 默认字幕字体不在已登记字体中 | 拒绝 | 合成 |
| 上游配置里没有 `[ui]` 段 | 运行时 `WebUiRejected` | 合成 |
| 默认字体名含引号、换行或路径分隔符 | 运行时 `WebUiRejected` | 合成 |
| OFL 组件没公示版权行 | 法务页门禁拒绝 | 合成 + 规则自检 |
| 法务页指向 App 没带的许可证正文 | 门禁拒绝 | 既有规则 |
| 包内 OFL 正文路径落在已安装资源之外 | 门禁拒绝 | 既有规则 |
| 删了专有字体但没接上替代字体 | 构建审计拒绝；即使绕过，冻结包真实成片会暴露方框 | 合成 + **真实成片** |
| 声明负载与包体上限脱节 | 测试双向夹住（±10%） | 合成 |
| 契约变更后缓存复用旧产物 | 缓存键含契约摘要，自动重建 | 既有 `test_video_runtime_cache` |

## 真实边界

- **已验证**：macOS arm64 上从冻结包按生产方式启动、按真实用户路径成片、中文字幕正常渲染；
  权利门禁、法务页门禁、打包审计在真实篡改下确实拒绝；构建期获取在真实网络上跑通
  （冷 27.1 s / 热 0.051 s），摘要不符时真实失败关闭且不留缓存；
- **环境限制一次**：沙盒内 `urllib` 拉 17 MB 字体会报
  `SSL: WRONG_VERSION_NUMBER` / `EOF occurred in violation of protocol`，
  同一地址用 `curl` 和沙盒外 `urllib` 都正常，小文件在沙盒内也正常。判定为沙盒 TLS 拦截
  在大流量上的问题，不是产品缺陷；获取与构建改在沙盒外执行。首次获取后缓存即命中，
  后续构建不再需要网络；
- **未验证**：没有重建完整正式 `.app`/DMG 跑 EB-16 出厂门禁；没有在正式包里点开
  「设置与诊断 → 开源软件许可」实际看一遍新增的字体条目；Windows 侧完全未验证；
- **一处构建后的收尾改动**：验收跑完后给 spec 补了一行健壮性处理——`resource` 目录下如果
  直接放着文件（不在子目录里），目标路径不再拼出 `upstream/resource/.`。
  当前上游 `resource/` 下**没有任何顶层文件**（只有 `fonts`、`public`、被排除的 `songs`），
  所以这一行对本次验收过的产物是空操作，产物字节不受影响；
- 本次未改 `vendor/`（只读导入与只读读取）、`frontend/src/features/video-studio/`、
  `frontend/src/features/publish/`、`frontend/src-tauri/src/lib.rs`、`.github/`、
  `scripts/release_assembly.py`、`.local/`。

## 清理

- 未提交任何 Git 改动（按任务要求）；
- **仓库里没有字体二进制**：`assets/fonts/` 已删除，`git status` 与 `git ls-files` 均无字体文件，
  并有回归用例 `test_no_font_binary_is_checked_into_the_repository` 长期守住这一点；
- 验收用的临时工作区、素材、截图与冻结候选留在会话 scratchpad，未写入
  `~/Library/Caches/automation-tool-build/` 的 `media-toolchain` 与 `motion-video-worker`；
- `~/Library/Application Support/com.aventador.automationtool/` 未触碰；
- 验收结束后关闭了冻结 Worker 进程、Streamlit 子进程与本轮启动的无头浏览器，
  并确认本次创建的进程已退出。

## 遗留项

1. **`UTM Kabel KT.ttf` 仍在包内**（35,456 B）。UTM 系列是越南商业字库，权利状况与本次
   移除的四个字体同类，但它不在本任务交代的范围内，也需要单独的权利判定。
   本次只如实登记该风险，没有擅自扩大删除范围。上游另外三个拉丁字体
   （BeVietnam Pro、Charm）是 Google Fonts 的 OFL 字体，没有问题；
2. **中文字体只剩一个字族**。字幕字体下拉里现在只有 `NotoSansCJKsc-Bold.ttf` 与
   `-Regular.ttf` 两项能正确显示中文，选到任何拉丁字体仍会得到方框——这个陷阱在替换前
   就存在（上游同时提供中文与拉丁字体，却不按语言过滤），本次没有改变它，
   也没有能力在只读的上游 WebUI 里加过滤；
3. **首次构建需要一次网络获取**（33.4 MB，实测 27.1 秒），之后按缓存命中。离线机器
   第一次构建会失败关闭并说明原因，这是有意的——没有可以安全回退的字体；
4. **正式包与 Windows 验收**见「真实边界」。
