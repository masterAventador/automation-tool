# FIX 开源软件许可页履行三份许可证的实际义务

> 状态：🔍 待验收（页面、投影、门禁与许可证全文已落地并全部通过；缺一次正式包用户路径验收，
> 且仍有三类分发组件未逐项公示，见「遗留项」）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：同日把「第三方软件声明」从左侧主导航降权为「设置与诊断」页脚入口（提交 `4b6824b`）后复核发现，
> 这个页面本身根本没有满足它被保留下来的理由。

## 缺陷

页面被保留的理由是「产品分发了开源组件，许可证强制要求随分发物提供声明」。复核当时页面的实际内容：

```text
grep -c "GPL\|ffmpeg\|FFmpeg" frontend/src/features/legal/third-party-software/ThirdPartySoftwareNotice.tsx
0
```

页面只有：组件名、功能描述、版本、许可证标识（字符串 `MIT` / `Apache-2.0`）、固定提交、仓库地址。

| 组件 | 许可证强制要求 | 当时页面 |
| --- | --- | --- |
| MoneyPrinterTurbo | 保留版权声明 + 随分发物提供许可证正文 | 只有 `MIT` 三个字母 |
| hyperframes | 随分发物提供 LICENSE + 保留署名 | 只有 `Apache-2.0` |
| FFmpeg 8.1.2 | 许可证正文 + **对应完整源码的获取方式** | **完全没有条目** |
| x264 | 同上 | **完全没有条目** |

「公示了名字」不等于「履行了许可证」。三份许可证要求的两件核心东西——**版权行**和**许可证正文**——
页面一件都没有；GPL 还多要求一件**源码获取方式**，页面连组件本身都没提。

讽刺的是缺的不是源码：`scripts/build_video_media_toolchain.sh` 早就把
`source/ffmpeg-8.1.2.tar.xz`、`source/x264-b35605a….tar.gz`、`COPYING.GPLv3` 和 `NOTICE.txt`
一并放进 `media-toolchain/`，而该目录从提交 `2e9a3a6` 起随正式包分发。缺的是**告诉用户源码在哪**。

## 事实核查结果

按 `contracts/`、`scripts/release_assembly.py` 与各构建脚本逐条核对，安装包实际分发的第三方组件是
**7 项**，不是 3 项。任务交代里没列出的是第 4～7 项。

| # | 组件 | 版本 | 许可证 | 分发位置（`Contents/Resources/` 下） | 正文/声明位置 | 源码 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | MoneyPrinterTurbo | v1.3.2 | MIT | `material-video-worker/package/_internal/upstream/` | App 内全文 + 包内 `_internal/upstream/LICENSE` | GitHub |
| 2 | hyperframes | v0.7.68 | Apache-2.0 | 派生的 134 个动效零件元数据编进 App 包 | App 内全文 | GitHub |
| 3 | FFmpeg | 8.1.2 | GPL-3.0-or-later | `media-toolchain/bin/` | App 内全文 + 包内 `COPYING.GPLv3` | 包内 `source/ffmpeg-8.1.2.tar.xz` |
| 4 | x264 | `b35605a…` | GPL-2.0-or-later | 静态编入上面那个可执行文件 | 同上（整体按 GPL-3.0 分发） | 包内 `source/x264-b35605a….tar.gz` |
| 5 | **Google Chrome for Testing** | 149.0.7827.55 | BSD-3-Clause + Google 专有条款 | `embedded-browser/` | 浏览器自带 `chrome://credits` | 不适用 |
| 6 | **Node.js** | 22.23.1 | MIT（聚合声明） | `motion-video-worker/package/runtime/` | 包内 `NODE-LICENSE` | 上游 |
| 7 | **CPython + 112～115 个 Python 依赖** | 3.11.15 | PSF-2.0 + 混合 | `material-video-worker/package/` | 包内 `_internal/licenses/material-video-worker-dependencies.json` | 上游 |

核查依据：

- 第 5 项：`contracts/browser/embedded-chromium-staging.v1.json` 的 `chromium.title` 是
  `Chrome for Testing`、`browser_version` 是 `149.0.7827.55`；`scripts/release_assembly.py`
  的 `install_and_seal` 把它装进包并重新签名。**注意**：
  `scripts/build_embedded_browser_distribution.py` 生成的分发清单里写着
  `"redistribution_review": "pending"`——即这个组件的再分发合规审查至今没做完，见「遗留项」。
- 第 6 项：`scripts/build_motion_video_worker_candidate.py` 从 Node 官方压缩包中抽出
  `/LICENSE` 写为 `NODE-LICENSE`；`release_assembly.VIDEO_RUNTIME_RESOURCES` 把
  `motion-video-worker` 装到 `motion-video-worker/package`。
- 第 7 项：`workers/material_montage/material-video-worker.spec` 把
  `vendor/moneyprinterturbo/{app,webui,resource,LICENSE,config.example.toml}` 打进
  `_internal/upstream/`；`scripts/build_material_video_worker_candidate.py` 另外把
  `workers/material_montage/dependency_audit.py` 生成的依赖许可证清单写进
  `_internal/licenses/material-video-worker-dependencies.json`。
- 第 2 项确认「确实在分发」：`frontend/src/features/video-studio/motion-parts-catalog.ts` 直接
  `import contract from ".../contracts/video/motion-catalog-ui.v1.json"`，那 134 条零件元数据是
  从上游目录派生的，会编进 App 前端包。因此 Apache-2.0 第 4 条的义务此刻就已成立。
  （`.local/offline-motion-deps/` 下的 gsap / three / d3 等离线依赖目前**不**随安装包分发，
  `VIDEO_RUNTIME_RESOURCES` 只有 media-toolchain 与两个 Worker 三项。）
- x264 授权是「GPL-2.0 或更高版本」，被静态链接进唯一那个 FFmpeg 可执行文件，因此用户实际收到的
  是一份 GPL-3.0 作品，`NOTICE.txt` 本来就是这么写的；GPL-3.0 正文对两者都适用，x264 自己的
  COPYING 在随包源码压缩包里。

## 许可证全文如何呈现：选择与理由

**选择：把 MIT、Apache-2.0、GPL-3.0 三份正文以 `.txt` 原文形式静态编进 App 包，页面上折叠展示，
同时把安装包内那份文件的路径也写出来。**

不选另外两条路的理由：

- **只写包内路径，不带全文**——`Contents/Resources/media-toolchain/COPYING.GPLv3` 藏在 `.app`
  内部，普通用户要右键「显示包内容」才能进去。这在法律上勉强算「随分发物提供」，但对用户等于拿不到。
  本可以加一个「打开许可证文件」按钮，但那需要新增 Tauri Command，而 `frontend/src-tauri/src/lib.rs`
  本次不允许改动（另有子代理在改）。
- **动态 `import()` 懒加载 35 KB 的 GPL 文本**——省下的是首屏几十 KB，代价是给「保证用户能读到全文」
  这件事引入一个新的失败模式（chunk 加载失败）。在一个已经带 230 MB 浏览器的安装包里，
  47 KB 的法律文本占 0.02%，为这点体积换一个可能读不到全文的风险不划算。

因此：正文静态内联，Ant Design `Collapse` 默认收起（已验证 antd v6 的面板内容是懒渲染的，
展开前 DOM 里没有那 35 KB，页面不会因此变卡），点开即读，离线可用，不依赖文件系统导航。
界面文案中文，法律正文保持英文原文不翻译。

正文的来源与防伪造：

- `mit.txt` = `vendor/moneyprinterturbo/LICENSE` 原字节（含 `Copyright (c) 2024 Harry`）；
- `apache-2.0.txt` = `vendor/hyperframes/LICENSE` 原字节（含 `Copyright 2026 HeyGen, Inc.`）；
  两者的 SHA-256 必须等于 `contracts/quality/third-party-sources.v1.json` 里已锁定的
  `license.sha256`，也就是 `check_third_party_sources.py` 绑定到锁定 commit 的那个 blob 摘要；
- `gpl-3.0.txt` = FFmpeg 8.1.2 自带的 `COPYING.GPLv3`（取自本机 `ffmpeg-full 8.1.2_1`，
  与 `build_video_media_toolchain.sh` 从 `ffmpeg-8.1.2.tar.xz` 里 `cp` 出来的是同一个文件），
  摘要 `8ceb4b9e…b903` 钉死在生成器常量里。

三份文本都不是手抄的，页面上不可能出现一份「差不多的许可证」。

## 架构

页面数据仍然只走投影，不直接读源契约（这是既有的防泄漏设计，
`_scan_frontend_imports` 会拒绝任何绕过投影的前端 import）。本次给投影加了三块：

| 投影字段 | 来源 | 说明 |
| --- | --- | --- |
| `upstreamProjects[].copyright` | 从锁定的 `vendor/*/LICENSE` 正文里正则提取首个 `Copyright` 行 | 派生而非重抄，改不了也不会过期 |
| `upstreamProjects[].licenseTextId` / `packagedNoticePath` | SPDX 映射 + 生成器常量 | 指向 App 内全文与包内文件 |
| `distributedComponents[]` | `ffmpeg-toolchain.v1.json`、`embedded-chromium-staging.v1.json`、`motion-video-worker-package.v1.json`、`material-video-worker-package.v1.json` | 版本、许可证、包内路径全部从各自的锁定契约推导 |
| `licenseTexts[]` | 三份 `.txt` 的实际字节 | id / spdx / sha256 / bytes |

投影体积 2888 → 5553 字节，仍在既有 8192 上限内，上限未动。

## RED

三次，每次都实际跑到看见红。

```text
# RED 1 —— 门禁根本没有「许可证义务」这条规则
$ python3 scripts/test_third_party_notice_ui_projection.py
ImportError: cannot import name '_require_license_obligations' from
'check_third_party_notice_ui_projection'

# RED 2 —— 加了空实现后，投影里没有任何被分发组件与许可证全文
$ python3 scripts/test_third_party_notice_ui_projection.py
  File ".../test_third_party_notice_ui_projection.py", line 157, in main
    assert set(projection) == {
AssertionError: projection has a closed top-level key set

# RED 3 —— 前端模型：三个新函数/常量都不存在
$ npx vitest run src/features/legal
AssertionError: expected [Function] to throw error matching /no distributed component/u
+ Received: "(0 , __vite_ssr_import_2__.buildDistributedComponentNotices) is not a function"
 Test Files  1 failed | 1 passed (2)
      Tests  14 failed | 22 passed (36)

# RED 4 —— 页面：7 个新用例全红（缺「随安装包分发的第三方组件」「许可证全文」等区域）
$ npx vitest run src/features/legal/third-party-software/ThirdPartySoftwareNotice.test.tsx
Unable to find an element with the text: 尚未逐项公示的部分
 Test Files  1 failed (1)
      Tests  7 failed | 7 passed (14)
```

### RED 的两项自检（本项目当天踩过的坑）

1. **测试条数确实变了**：`npx vitest run src/features/legal src/app`
   由 `57 passed | 1 expected fail (58)` 变为 `78 passed | 1 expected fail (79)`，
   新增 21 条；legal 目录单独由 23 条变为 43 条。新测试确实被收集、确实执行了。
2. **失败原因正是要修的那件事**，并且规则本身不是摆设：把
   `_require_license_obligations` 里的 `if component.get("copyleft"):` 临时改成 `if False:` 后重跑，

   ```text
   AssertionError: obligation check missed a GPL component with no corresponding source
   ```

   证明「GPL 组件没给源码就必须红」这条断言真的在跑，而不是恰好对旧代码也通过。改回后立即复绿。

## GREEN

```text
$ python3 scripts/check_third_party_notice_ui_projection.py
third-party notice ui projection check passed: 2 upstream projects (MoneyPrinterTurbo,
hyperframes), 5 distributed components (2 copyleft: ffmpeg, x264), 3 licence texts shipped
in the App, 9 borrowed packages, no review detail or address leakage
rc=0

$ python3 scripts/test_third_party_notice_ui_projection.py
third-party notice ui projection tests passed
rc=0

$ python3 scripts/check_user_facing_branding.py
user-facing branding and plain-language scan passed (51 frontend, 247 native files)
rc=0

$ python3 scripts/check_third_party_sources.py
third-party source locks, licenses, rights policy and SBOM are valid
rc=0

$ cd frontend && npx vitest run src/features/legal src/app
 Test Files  6 passed (6)
      Tests  78 passed | 1 expected fail (79)

$ cd frontend && npx tsc -b --force
rc=0

$ cd frontend && npx eslint .
rc=0

$ cd frontend && npx vitest run          # 全量回归
 Test Files  58 passed (58)
      Tests  439 passed | 1 expected fail (440)
```

## 门禁新增的规则

`scripts/check_third_party_notice_ui_projection.py::_require_license_obligations`，
每条都是许可证条款而不是风格偏好：

1. **每个被分发组件必须公示一条能真正读到许可证的途径**——App 内全文、包内正文文件、
   或组件自身的声明页三选一；一条都没有直接红。
2. **每个 copyleft 组件必须公示对应源码位置**：包内路径非空 **且** 上游源码地址非空。
   这是任务要求的那条「声明了 GPL 组件却没给源码获取方式 → 拒绝」。
3. **每个上游项目必须复现版权行并携带许可证正文**——`copyright` 必须以 `Copyright` 开头，
   `licenseTextId` 必须指向一份 App 真的带着的文本。
4. **每个公示的包内路径必须是真路径**：
   - 必须落在 `release_assembly.VIDEO_RUNTIME_RESOURCES` 的实际安装前缀下
     （`media-toolchain/`、`motion-video-worker/package/`、`material-video-worker/package/`），
     直接从生产装配代码导入，不重抄；
   - 必须规范化（不许 `..`、绝对路径、空段）；
   - 要么由契约声明（`ffmpeg-toolchain.v1.json` 的 `package_layout`），要么其文件名必须能在
     声明的构建脚本源码里找到——写这个文件的脚本改了名，本页的指针就会红，
     而不是把一条死路径发给用户。
5. **每份 App 内许可证正文的字节必须与投影记录的摘要和长度一致**（换行归一化后），
   且不得存在没人引用的孤儿文本。

`_scan_for_leakage` 相应放行了 `distributedComponents[].upstreamSourceUrl`——GPL 要求公示源码地址，
这一个字段和仓库 URL 一样属于「必须出现的地址」；其余字段仍受原来的禁地址规则约束。

## 失败矩阵

| 情形 | 期望 | 验证方式 |
| --- | --- | --- |
| GPL 组件的包内源码路径被清空 | 门禁红 | `obligation_failure("GPL component with no corresponding source")` |
| GPL 组件的上游源码地址被清空 | 门禁红 | `obligation_failure("GPL component with no source address")` |
| 某组件三条声明途径全空 | 门禁红 | `obligation_failure("component with no readable licence")` |
| 指向一份 App 没带的许可证文本 | 门禁红 | `obligation_failure("licence text the App does not ship")` |
| 包内路径含 `..` 逃逸 | 门禁红 | `obligation_failure("in-package path escaping the resource root")` |
| 包内路径落在任何已安装资源之外 | 门禁红 | `obligation_failure("in-package path outside every shipped resource")` |
| 上游项目版权行丢失 | 门禁红 | `obligation_failure("upstream project with no copyright line")` |
| App 内许可证正文被改了字节 | 门禁红 | `obligation_failure("shipped licence text whose bytes drifted")` |
| 投影漏掉 ffmpeg 条目 | 门禁红 | `expect_check_failure("undisclosed GPL component")` |
| 投影不再带 GPL 全文 | 门禁红 | `expect_check_failure("GPL licence text no longer shipped")` |
| 上游 LICENSE blob 漂移 | 生成器直接拒绝出投影 | `_require_shipped_license_texts` |
| 写 `NODE-LICENSE` 的构建脚本改名 | 门禁红 | `_require_packaged_path` 反查生产脚本 |
| 前端模型层收到不合规数据 | 抛错而不是渲染 | `buildDistributedComponentNotices` 的 6 条前端用例 |
| 许可证正文在 Windows 检出成 CRLF | 摘要不变 | 生成器、门禁、前端三处都做 `\r\n → \n` 归一 |

## 真实边界

- **已验证**：投影与页面的每个事实都能追到锁定契约或构建脚本；三份许可证正文与
  `vendor/*/LICENSE`、FFmpeg 自带 `COPYING.GPLv3` 字节一致；页面在 jsdom 中确实渲染出全部
  7 项组件、版权行、包内路径、源码路径与三份完整正文（`expect(shown.textContent).toBe(text)`）。
- **未验证**：没有在正式 macOS/Windows 安装包里打开「设置与诊断 → 开源软件许可」实际点一遍
  （本次任务禁止启动 App、浏览器与 Tauri 构建）。
- **未验证**：没有在真实安装包里逐个确认那五条包内路径的文件确实存在。门禁只能证明
  「路径落在生产装配真的会安装的资源目录下」和「写它的脚本里还有这个文件名」，
  证明不了「这一版包里那个文件真的躺在那儿」。补验收方式见「遗留项」。
- 本次未改 `scripts/release_assembly.py`、`prepare_video_runtime.py`、`video_runtime_cache.py`、
  `check_embedded_browser_package.py`、`run_eb_16_*.py`、`frontend/src-tauri/`、
  `frontend/src/features/video-studio/`、`vendor/`。`release_assembly.py` 只被只读导入。

## 清理

- 用于确认 antd Collapse 是否懒渲染的临时探针测试 `frontend/src/test/probe/` 已删除，
  `frontend/src/test/` 下只剩原有的 `setup.ts`；
- 临时备份 `check.bak.py`（用于验证 RED 自检后回滚）已删除；
- 未启动任何 App、浏览器、模拟器或本地服务，无残留进程；
- 未触碰 `.local/`、`~/Library/Caches/automation-tool-build/` 与
  `~/Library/Application Support/com.aventador.automationtool/`。

## 文档

- 本文件；
- `ThirdPartySoftwareNotice.tsx` 顶部注释补记了「公示名字 ≠ 履行许可证」这次的病根；
- `third-party-software-notice.ts` 顶部注释写明了为什么许可证正文是静态内联而不是懒加载；
- `check_third_party_notice_ui_projection.py` 的 `_require_license_obligations` docstring
  逐条写明三条规则各自对应哪一款许可证条款。

## 遗留项

按法律风险排序。**本页现在不声称自己是一份完整清单**，页面上专门有一个「尚未逐项公示的部分」
如实说明下面第 1～3 项还没做——一份假装完整的声明比一份承认不全的声明更糟。

1. **本机执行器运行环境未公示**（`Resources/local-executor/package`）。它是 PyInstaller 打包的
   CPython 3.12 + Playwright 1.61.0（Apache-2.0）+ fastapi / uvicorn / cryptography / asyncpg /
   sqlalchemy / argon2-cffi / websockets / alembic / pydantic 及其传递依赖。
   与 material-video-worker 不同，它**没有**任何依赖许可证清单生成步骤——
   `backend/automation-tool-executor.spec` 里没有对应的 `dependency_audit.py` 调用。
   补法：给它加一份同样的清单生成，再把它作为第 8 个 `distributedComponent` 接进投影。
2. **PyInstaller 引导程序未公示**。两个 PyInstaller 包（executor 与 material worker）的可执行文件
   都含 PyInstaller 6.21.0 的 bootloader，其授权是「GPL-2.0-or-later 附打包例外」。
   例外条款允许用它打包闭源应用而无需公开应用源码，但**bootloader 自身仍是 GPL**，
   其源码获取方式仍须提供。本次没做，因为把它加进投影会立刻触发第 2 条门禁规则
   （copyleft 必须给源码）而卡住交付——这正是门禁该有的行为，但源码随包分发需要改构建脚本，
   超出本次范围。
3. **App 前端与 Rust 侧依赖未公示**。React、Ant Design、TanStack Query、Zod 等 JS 依赖与
   Tauri 侧 Rust crate 绝大多数是 MIT / Apache-2.0，都要求保留版权声明。目前既没有生成的
   NOTICE，也没有 SBOM 覆盖。补法：`pnpm licenses list` / `cargo-about` 生成清单后接进投影。
4. **Chrome for Testing 的再分发合规审查仍是 `pending`**。
   `scripts/build_embedded_browser_distribution.py` 生成的分发清单里明写
   `"redistribution_review": "pending"`。本次只做了公示（组件、版本、许可证构成、
   `chrome://credits` 的查看方式），**没有**、也无权替代那次审查。把一个 Google 品牌的
   Chrome 构建放进商业产品安装包再分发，是否被 Google Chrome 服务条款允许，需要法务确认；
   若不允许，正确解法是换成 Chromium 开源构建，而不是改这个页面。**这是本次之后剩下的最大法律风险。**
5. **包内路径的存在性没有出厂门禁**。第 4 条规则能挡住「路径不在已安装资源目录下」和
   「写它的脚本改了名」，但挡不住「这一版包里那个文件确实没写出来」。
   补法：在 `require_packaged_video_runtime` 里把本投影公示的 `packagedNoticePath` 与
   `packagedSourcePaths` 也纳入必需文件列表——那样「页面上写着的路径」和「出厂检查的路径」
   就是同一份事实。本次没做，因为 `release_assembly.py` 不在允许改动范围内。
6. **`material-video-worker-dependencies.json` 只有许可证名称与文件摘要，不含许可证正文**。
   PyInstaller 只对 14 个 `copy_metadata` 的发行版收了 dist-info，其余近百个 Python 包的
   LICENSE 文件正文并不在包里。严格说 MIT / BSD 类依赖的「随分发物提供许可证正文」义务
   对这部分尚未满足。
7. **正常用户路径验收未做**：需要在正式包里点开「设置与诊断 → 开源软件许可」，确认三份正文能展开、
   包内路径与实际文件对得上。等本次正式包重建完成后补。
