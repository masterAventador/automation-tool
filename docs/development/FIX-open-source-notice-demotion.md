# FIX：法务页降权——从主导航移到「设置与诊断」页脚，改名「开源软件许可」

> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 类型：体验修复（闭合 `dogfood-findings-20260726.md` 第 2 条）
>
> 前序：`FIX-third-party-software-notice-page.md`（本页的建立）

## 任务

用户在 macOS 正式包上按真实路径逐页试用，看到左侧主导航里「第三方软件声明」与
「视频制作」「任务记录」平级，要求去掉。

**不能整页删。** 产品分发了三份开源组件，各自许可证都强制要求随分发物提供声明：

| 组件 | 许可证 | 强制要求 |
| --- | --- | --- |
| 智能素材成片上游 | MIT | 分发物必须带版权声明与许可证全文 |
| 品牌动效成片上游 | Apache-2.0 | 必须带 LICENSE、NOTICE 与变更说明 |
| ffmpeg | GPLv3 | 必须带许可证全文和完整对应源码 |

删掉继续分发构成许可证违规，GPLv3 一方有权要求停止分发。

**用户决策（2026-07-26，原话「按照你这个来」）**：降权而不是删除——

1. 从左侧主导航移除；
2. 挪到「设置与诊断」页最底部一行小字入口，标题改为「开源软件许可」；
3. 页面内容只留法定必需项，删除展示性技术描述。

## 交付

### 1. 入口

`navigationItems` 少了一项，`WorkbenchShell` 里多了三个常量
（`LEGAL_PAGE` / `LEGAL_PAGE_SECTION` / `LEGAL_PAGE_TITLE`），
「设置与诊断」页脚多了一个 `Button type="link"`。

`activePage === "legal"` 这条路由**没有动**——页面照样是页面，只是没人从侧栏进来。
菜单点击白名单里的 `"legal"` 删掉了：它已经不是菜单项，留着就是一条永远走不到的分支。

侧栏选中项做了一次映射：`selectedKeys={[showingLegal ? LEGAL_PAGE_SECTION : activePage]}`。
不做这件事的话，打开许可页时整个侧栏没有任何高亮，读起来像页面坏了而不是「设置的子页」。

### 2. 内容裁剪：哪些留、哪些删、依据是什么

判据只有一条：**它是许可证公示，还是内部工作台账？**

**留（每一条都是许可证义务或它的直接支撑）**

| 内容 | 为什么必须留 |
| --- | --- |
| 项目名称 | MIT / Apache-2.0 都要求保留原始名称 |
| 固定版本 + 固定提交 | 声明的对象必须可确定，否则声明的是哪份代码说不清 |
| 许可证标识 | 三份许可证的核心 |
| 源码获取地址（`sourceUrl`） | GPLv3 的「完整对应源码」义务的入口 |
| 「以只读方式固定在这个版本上使用，产品不修改它的代码」 | Apache-2.0 要求的变更说明 |
| 「上游代码许可证只覆盖代码，不授予肖像/字体/音频/商标/示例素材的再分发权利」 | 删掉会让声明读起来像比实际更宽的授权 |
| 「没有登记齐权利信息的素材一律不随安装包分发」 | 素材侧的实际结论 |
| 产品功能名（智能素材成片 / 品牌动效成片） | 声明不说清这段代码用在哪，等于没声明 |
| 「为什么这些名称只出现在本页」 | 正面回答用户「为什么这里列着这些名字」的疑问 |

**删（内部权利核查进度，不是许可证公示，而且谈的素材本构建一件都没分发）**

| 内容 | 为什么可以删 |
| --- | --- |
| 「已核查 134 个动效零件：7 个可直接使用，127 个必须先本地化或更换素材才能随产品分发」 | 工作台账。「必须先……才能分发」说明它们**没有**被分发 |
| 「其中 18 个网络字体家族与 28 个自带示例素材的权利尚未核实」 | 同上，且「尚未核实」是内部 TODO 泄漏到用户界面 |
| 「每一条素材必须先登记齐 12 项通用权利信息」+ 六个分类各自的「另需 N 项专门信息」 | 流程机制描述。登记册是空的，这套流程当前没有作用对象 |
| 「动效零件引用的外部程序包」9 条清单（d3 / gsap / three …），标注「以下许可证为初步判定，尚未逐项核实；随产品分发前必须改为本机文件并完成核实」 | 自己写明了是初步判定且未随产品分发，公示一份未核实的许可证判断对谁都没有好处 |

删掉的数据仍然留在投影契约和 `third-party-software-notice.ts` 的构建函数里，
构建期校验（缺许可证、缺版本、权利策略从 deny 改成 allow 一律抛错）一条没少。

## RED

先写测试、实际运行、看到红，再动生产代码。

改动前基线：

```text
cd frontend && npx vitest run src/app src/features/legal
 Test Files  6 passed (6)
      Tests  52 passed (52)
```

加完测试、未动生产代码：

```text
 × ThirdPartySoftwareNotice.test.tsx > drops the internal rights-review progress the notice never owed anyone
   → expect(element).not.toBeInTheDocument()
 × WorkbenchShell.test.tsx > keeps the open source licence notice out of the main navigation
   → expect(element).not.toBeInTheDocument()
 × WorkbenchShell.test.tsx > reaches the open source licence notice from the foot of settings and diagnostics
   → Unable to find an accessible element with the role "button" and name "开源软件许可"
 × WorkbenchShell.test.tsx > keeps 设置与诊断 marked as the section the licence notice belongs to
   → Unable to find an accessible element with the role "button" and name "开源软件许可"
 × WorkbenchShell.test.tsx > returns to settings and diagnostics through the sidebar
   → Unable to find an accessible element with the role "button" and name "开源软件许可"
 × WorkbenchShell.test.tsx > keeps the upstream names off every other page in the navigation
   → Unable to find an accessible element with the role "button" and name "开源软件许可"
 × App.test.tsx > keeps the open source licence notice out of the assembled main navigation
   → expect(element).not.toBeInTheDocument()
 × App.test.tsx > opens the open source licence notice from the foot of settings and diagnostics
   → Unable to find role="button" and name "开源软件许可"

 Test Files  3 failed | 3 passed (6)
      Tests  8 failed | 50 passed (58)
```

**条数核对**：52 → 58。本轮净增 6 条（新增 8 条、删除 2 条被新断言替代的旧用例），
与 RED 输出的 58 一致——没有出现「写进去了但没被收集」。

## GREEN

```text
cd frontend && npx vitest run src/app src/features/legal
 Test Files  6 passed (6)
      Tests  58 passed (58)
```

条数与 RED 相同（58），不是靠少收集用例转的绿。

其余门禁（本次逐条实跑，非引用历史结果）：

```text
cd frontend && npx eslint .                                    退出码 0，无输出
python3 scripts/check_third_party_notice_ui_projection.py
  third-party notice ui projection check passed: 2 upstream projects
  (MoneyPrinterTurbo, hyperframes), 9 borrowed packages,
  no review detail or address leakage                          退出码 0
python3 scripts/check_user_facing_branding.py
  user-facing branding and plain-language scan passed
  (50 frontend, 247 native files)                              退出码 0
```

`tsc` 需要单独说明。本次改动完成时实跑：

```text
cd frontend && npx tsc -b --force                              退出码 0
```

**收尾复跑时它变红了，但不是本次改动引入的。** 同一工作树上有并行任务在推进
dogfood 第 2b 条（品牌动效时长写死），收尾期间新落地了
`frontend/src/features/video-studio/motion-duration.test.ts`（文件时间 02:24，
本任务开始时 `git status` 里还不存在），它 import 的
`contracts/video/motion-storyboard-duration.v1.json` 与 `./motion-duration` 尚未创建——
那是那条任务正处于 RED 的正常状态。复跑输出里的报错文件只有这一个：

```text
cd frontend && npx tsc -b --force 2>&1 | sed 's/(.*//' | sort -u
src/features/video-studio/motion-duration.test.ts
```

本次改动的 5 个 TypeScript/TSX 文件（`WorkbenchShell.tsx`、`WorkbenchShell.test.tsx`、
`App.test.tsx`、`ThirdPartySoftwareNotice.tsx`、`ThirdPartySoftwareNotice.test.tsx`）
一条报错都没有。合并前需要在一个只含本次改动的工作树上把 `tsc -b --force` 重跑一次到退出码 0。

品牌扫描仍是 **50 frontend** 个文件，与降权之前一致——法务页依旧是靠
`allowedLegalDisclosurePaths` 白名单跳过的，词表和白名单一个字没动。

前端全量：

```text
cd frontend && npx vitest run
 Test Files  3 failed | 54 passed (57)
      Tests  10 failed | 402 passed (412)
```

10 条失败**全部**落在 `src/features/video-studio/`
（`MotionPartsCatalog.test.tsx` / `VideoStudio.test.tsx` / `motion-parts-catalog.test.ts`）。
这三个文件在本任务开始前就已是 `git status` 中的 modified 状态，属于同批 dogfood 条目
2c/2d/2e 的并行 RED，与本次改动无关（本次改动一行都没碰 video-studio）。排除后：

```text
cd frontend && npx vitest run --exclude "src/features/video-studio/**"
 Test Files  52 passed (52)
      Tests  374 passed (374)
```

## 失败矩阵

| 场景 | 结果 | 覆盖方式 |
| --- | --- | --- |
| 从主导航拿掉后页面彻底不可达 | 拒绝 | 两个装配入口各有一条「从设置页脚点进去并渲染出上游开源项目区域」的用例 |
| 入口只是一段死文字 | 拒绝 | 断言 `role="button"`，且必须点击后真的渲染出声明区域，不是只断言文字存在 |
| 一个装配入口接了、另一个没接 | 拒绝 | `App.test.tsx` 走 `StartupGate` 真实组合而不是直接渲染 `WorkbenchShell` |
| 侧栏无任何高亮，读起来像坏页 | 拒绝 | 断言 `设置与诊断` 带 `ant-menu-item-selected` |
| 进得去回不来 | 拒绝 | `returns to settings and diagnostics through the sidebar` |
| 上游名因本次改动泄漏到别的页面 | 拒绝 | `keeps the upstream names off every other page` + 品牌扫描仍 50 文件 + Playwright `upstream-name-leak.spec.ts` 的 `设置与诊断` 一页 |
| 内容裁剪把法定必需项一起删掉 | 拒绝 | `still carries every fact the three licences oblige it to publish` 逐项断言名称/许可证/固定提交/源码地址/只读声明；**已实测注入**：删掉「固定提交」一行 → `Unable to find an element with the text: /b1588e1fdc6c5e54358f66ca2ff323e1dddf1364/u`，退出码非 0，还原后 22 passed |
| 裁掉的内部台账偷偷长回来 | 拒绝 | `drops the internal rights-review progress` 断言两个小标题不存在、依赖名与分类名不可见、「尚未核实」「初步判定」不出现 |
| 素材登记册以后不再是空的，页面继续说「什么都没分发」 | 拒绝 | 保留 `registeredEntryCount === 0` 分支，两条分支都写了「没有登记齐权利信息的素材一律不随安装包分发」 |
| 上游名混进设置页那行入口文字 | 拒绝 | 入口文字是常量 `LEGAL_PAGE_TITLE = "开源软件许可"`，品牌扫描覆盖该文件 |
| 源契约漂移 / 前端绕过投影 | 拒绝 | `check_third_party_notice_ui_projection.py`（既有门禁，本次实跑通过） |

## 真实边界

1. **没有在正式 Tauri App 上走用户路径验收。** 本轮只有 Vitest + Testing Library。
   按项目规则，UI Harness 只能证明 React 业务交互，不能替代正式 App 验收。
   用户手上的那个 macOS 包是改动之前构建的，本次改动要在下一次正式包里才看得到。
2. **Playwright 没有运行。** `frontend/e2e/third-party-software-notice.spec.ts` 与
   `upstream-name-leak.spec.ts` 已按新路径改写（从「点菜单项」改成「进设置 → 点页脚入口」），
   但本任务约束不启动浏览器，**这两个 spec 本轮是未执行状态**，红绿未知。
3. **页面仍然没有许可证全文、没有版权行、没有列出 ffmpeg。** 这是本次**之前就存在**的缺口，
   不是本次裁剪造成的：改动前的页面同样只有许可证标识（MIT / Apache-2.0）和仓库地址。
   dogfood 记录里 GPLv3 那一项（包内 `source/ffmpeg-8.1.2.tar.xz`）在页面上一个字都没有。
   本次严格没有降低现状，但**也没有把它补上**——见「遗留项」。
4. **裁剪范围是我按「许可证公示 vs 内部台账」判据划的，没有经过法务确认。** 判据和逐条依据
   写在上面「内容裁剪」一节，如果对某一条的归类有异议，改回来的成本是恢复几段 JSX，
   数据仍在投影契约里没删。
5. **`ant-menu-item-selected` 是 Ant Design 的内部 class。** 那条侧栏高亮用例耦合到了组件库实现，
   AntD 大版本升级时可能需要跟着改。选它是因为 rc-menu 的 inline 菜单项不写 `aria-selected`，
   没有语义化的替代断言。

## 清理

- 注入验证（删掉「固定提交」一行）已按备份原样还原，还原后复跑 `src/features/legal` 得到
  `2 passed / 22 passed`，临时备份文件已删除；
- `.legal-notice-tags` 的两段 CSS 随它渲染的那份清单一起删掉（`grep` 确认全仓库无其他引用），
  新增 `.settings-legal-entry`；
- 未启动 App、未启动浏览器、未运行 Playwright，无浏览器进程或端口需要回收；
- 未 `git add`、未 `git commit`；`.local/` 未触碰。

## 文档

- `frontend/src/app/WorkbenchShell.tsx`（导航移除、页脚入口、标题与侧栏选中映射）
- `frontend/src/app/WorkbenchShell.test.tsx`
- `frontend/src/app/App.test.tsx`（第二装配入口覆盖；`App.tsx` 本身无需改动，见下）
- `frontend/src/features/legal/third-party-software/ThirdPartySoftwareNotice.tsx`
- `frontend/src/features/legal/third-party-software/ThirdPartySoftwareNotice.test.tsx`
- `frontend/src/styles/global.css`
- `frontend/e2e/third-party-software-notice.spec.ts`（未执行）
- `frontend/e2e/upstream-name-leak.spec.ts`（未执行）
- `docs/user-help.md` 第 12 节改名并改写入口位置，第 3 节顺带改名
- `docs/troubleshooting.md` 改名
- `docs/development/dogfood-findings-20260726.md` 第 2 条登记已修复
- 本文件

**关于 `App.tsx` 没有产生 diff**：它把导航整个委托给 `WorkbenchShell`，自己不定义任何菜单项，
所以降权在它这一侧没有可改的东西。真正需要防的是「组合层把改动吃掉了」，
所以覆盖放在了 `App.test.tsx`——它经 `StartupGate` 渲染真实组合，断言主导航里没有这一项、
并且从设置页脚点得进去。`App.tsx` 里另一处 `settings-stack`（`repairTools`）**故意没有加入口**：
那是启动被阻断时的本机修复面板，工作台根本没挂载，没有 `activePage` 可切，
放一个点了没反应的链接比不放更糟。

## 遗留项

| 项 | 状态 | 归属 |
| --- | --- | --- |
| 正式 Tauri App 用户路径验收（设置页脚 → 许可页） | 未做 | 下一次正式包 / EB-17 / CQ-04 |
| Playwright 两个 spec 实跑 | 未做（已改写，红绿未知） | 下一轮前端门禁 |
| 页面缺 ffmpeg（GPLv3）条目 | 未做，本次之前即缺 | 需新立任务：ffmpeg 的许可证与「完整对应源码」入口必须进这一页 |
| 页面缺许可证全文与版权行 | 未做，本次之前即缺 | 同上；投影契约需要新增字段，不是改 JSX 能解决的 |
| 在只含本次改动的工作树上复跑 `tsc -b --force` 到退出码 0 | 未做（当前树上有并行 RED，见 GREEN 一节） | 合并前 |
| Windows 侧同一批文案与入口位置核对 | 未做 | Windows 验收队列 |
| `docs/frontend-architecture.md`、`docs/third-party-source-governance.md` 等治理文档仍写「第三方软件声明页」 | 未改 | 那里指的是这一页的概念身份而非界面标题，改名不影响其成立；如需统一术语另立任务 |
