# T88 界面与屏效普查

> 日期：2026-07-26
>
> 分支：`ui-density`
>
> 范围：`frontend/src/` 下除 `src-tauri/`、`features/app-updates/`、`test-tauri-main.tsx`
> 之外的全部界面（这三处由并行的其它工作线占用）。
>
> 目的：这个产品此前只修过一个界面缺陷（整页一起滚动），从没有人系统看过一遍界面。
> 本文是第一次全量普查的结果，按"客户在演示当天会不会看见"排序。

## 观察方式与前提

- 工具：`agent-browser`（无头），端口 1523 起 UI Harness（1420 留给 Playwright），观察完即关停；
- 入口：`http://127.0.0.1:1523/harness.html?health=available&scenario=task-lifecycle`，
  并通过左侧菜单真实点击逐页进入，其中工作台与任务运行详情是**先用页面表单真建 3 个任务**后再看的；
- 视窗尺寸：**1280×800**（`src-tauri/tauri.conf.json` 里生产窗口的 `width/height`，
  也就是客户双击图标后的实际尺寸）、1440×900、1920×1080，以及 960×640
  （`minWidth/minHeight`，用户能拖到的最小尺寸）；
- 所有像素值来自 `getBoundingClientRect()` / `getComputedStyle()` 实测，不是目测。

**下文每条都标注了「实测」或「读代码推断」。**

---

## P0 — 演示当天客户一定会看见

### 1. 视频剪辑：整页内容只占列宽的一半（实测）✅ 本次已修

| 视窗 | 内容列宽 | `.video-editing-projects` 实测宽 | 占比 |
| --- | --- | --- | --- |
| 1280×800（生产默认） | 992px | 568px | **57.3%** |
| 1440×900 | 1152px | 568px | **49.3%** |
| 1920×1080 | 1180px | 568px | **48.1%** |

「提交与任务」页签同样：1280 下 582/992 = 58.7%，1440 下 582/1152 = 50.5%。
右半边是整块空白，卡片孤零零挂在左边。

**根因**：`VideoEditingWorkbench.tsx` 里写了 16 个 `video-editing-*` 类名，
`src/styles/global.css` 里**一条规则都没有**（`grep -n "video-editing" src/styles/global.css`
无输出）。antd `Space` 默认 `display: inline-flex`，没有 `width: 100%` 就按内容收缩。
项目里其它每一个功能都声明了这条——`.settings-stack`、`.platform-session-stack`、
`.task-create-stack`、`.diagnostics-stack`、`.video-studio-new-form`、
`.model-service-settings-stack`、`.legal-notice-stack`、`.motion-script-editor`——
只有 video-editing 这一个功能整体漏了。

**改动量**：global.css 两条规则（外层页签容器 + 卡片内容器），纯 CSS，无 JSX 改动。
注意外层单独改是不够的——见下面「RED 第二轮」。
**客户会看见**：会。左侧菜单一级入口。

### 2. 视频剪辑 / 作品发布：首块内容与标题行零间距（实测）✅ 本次已修

八个页面「标题行底边 → 首块内容顶边」的实测间距：

| 页面 | 间距 |
| --- | --- |
| 工作台 / 新建任务 / 视频制作 / 平台状态 / 设置与诊断 | 20px |
| 任务记录 | 24px |
| **视频剪辑** | **0px** |
| **作品发布** | **0px** |

这两页右上角绿色标签的底边和首块内容的顶边都落在 y=156。而这两页的首块内容恰好
都是带彩色边框的 `Alert`，所以标签直接压在警示框上沿，肉眼可见。

**根因**：这 20px 在 global.css 里被复制了 7 份——`.video-studio`、`.workbench-content`、
`.task-create-card`、`.settings-stack`、`.app-update-card`、`.platform-session-content`、
`.legal-notice-stack` 各写一遍——`.video-editing` 和 `PublishWorkspace` 的根元素就是
漏写的那两个。

**改动量**：按现有 `.platform-session-content` 的写法补齐，CSS 2 条 + shell 里包一层 div。
**客户会看见**：会。两个一级入口。

**遗留**：这 20px 复制 7 份本身是隐患（第 9 个页面还会再漏一次）。彻底做法是让
`.desktop-content main` 统一拥有这个间距、删掉 7 处重复。但那会动到每一个页面的
间距，演示前夕又有三条工作线同时在改前端，本次**没有做**，登记在此。

### 3. 视频制作：第一步的「选择」按钮在折叠线以下 243px（实测）❌ 未修

1280×800 下「新建视频」页签的实测纵坐标：

| 元素 | y | 高 |
| --- | --- | --- |
| 页面标题「视频制作」 | 92 | 38 |
| 页签栏 | 176 | 46 |
| 卡片头「新建视频」 | 239 | 56 |
| 「选择制作方式」标题行 | 364 | 50 |
| 两张制作方式卡 | 430 | 691 |
| 说明表（10 行 `<dl>`） | 557 | 466 / 489 |
| **「选择」按钮** | **1043 / 1065** | 32 |

视窗底是 800——**两个按钮分别在折叠线下 243px 和 265px，要往下滚约 340px 才够得着**。
整页 `scrollHeight` 1240 / `clientHeight` 736。1440×900 下按钮在 y=1021，仍在折叠线下
121px；只有 1920×1080 才勉强露出。

客户点开「视频制作」看到的第一屏是一面文字墙，没有任何可点的动作。

另外：「新建视频」这四个字在同一屏里出现两次——页签标签 y=176 和卡片头 y=239，
中间只隔 17px，卡片头这 56px 是纯重复。

**为什么没修**：把按钮提上来要么折叠说明表、要么把按钮挪到卡头，两者都是版式重设计，
不属于"小而安全"。删掉重复的卡片头只省 56px，按钮仍在折叠线下 187px，解决不了问题。
**请你决定。**

### 4. 工作台：8 个指标卡吃掉首屏 243px，其中 6 个是 0（实测）❌ 未修

- 卡片 114px 高 × 2 行 + 16px 行距 = **243px**；卡片体内边距 24px，数值字号 24px，
  每张卡只承载「一个标签 + 一个数字」；
- 真建了 3 个任务后实测值是 3/2/0/0 和 0/2/0/0——8 个格子里 6 个是 0；
- 连带后果：1280×800 下工作台整页 881px vs 可视 736px，**要滚 145px 才能看到
  「最近任务」列表**（空数据时更差，要滚 245px）。

**为什么没修**：8 张 Card 改 `size="small"`（体内边距 24→12）约省 48px，消不掉滚动；
真要消掉得减少卡片数量或改成单行密排——那是版式决定，不是缺陷修复。**请你决定。**

### 5. 工作台「当前任务」把调试字段放在最显眼位置（实测）❌ 未修

首页最大的那张卡片，正文实测是：

```
Task ID：a8ed7b07-0f26-4337-b4c8-f38fde35b63d    状态：运行中
Revision：3                                       事件水位：2
```

「最近任务」列表里每一行的可点标题也是**裸 UUID**，没有任务名、关键词或时间：

```
f092df2c-ef86-4c29-becc-b2caba61c98a        已成功
f94921d1-658c-48bb-a480-32b29365449f        已成功
a8ed7b07-0f26-4337-b4c8-f38fde35b63d        运行中
```

这正是简报里点名的"次要／调试信息占据主视觉位置"。

**为什么没修**：
- `Revision` 和 `事件水位` 是内部协议计数器，从工作台移走（任务运行详情里仍保留）
  是小改动——但它们可能是有意放在那里佐证"任务快照是权威来源"这条架构主张，
  属于产品判断，我不擅自删；
- UUID 换成人话**做不到小改**：`TaskSnapshot` 只有
  `taskId/status/revision/lastEventSequence/createdAt/updatedAt`，**没有标题字段**，
  要显示关键词得先动后端契约。退而求其次可以显示 `createdAt`。

**这是我认为演示当天风险最高的一条**：它在首页、在最大的卡片里、客户第一眼就看到。

### 6. 任务运行详情：232px 的壳与标题之后才出现第一条任务事实（实测）❌ 未修

1280×800 实测：顶栏 64px + shell 的 h2「任务记录」与副标题（y=92–156）
+ `TaskRunDetails` 自己的 h3「任务运行详情」与 UUID（y=190–260）
→ **首屏 800px 里前 232px（29%）全是壳和标题**，第一张「运行概览」卡片从 y=277 才开始。
整页 962px，需滚 282px。

同页「任务控制」卡：56px 卡头 + 90px 卡体，装一排 4 个 32px 按钮 = 146px。

**为什么没修**：两层标题里去掉哪一层是版式决定。shell 对所有页面统一渲染 h2，
`TaskRunDetails` 的 h3 旁边还挂着有用的「返回工作台」和状态标签。**请你决定。**

### 7. 「任务记录」是个死胡同（实测）❌ 未修

从左侧菜单点「任务记录」，只会看到一个虚线框：

> **请选择一个任务**
> 从工作台当前任务或最近任务进入运行详情。

这个一级入口本身**不列任何任务**。1280×800 下 132px 的提示框下面是 600px 空白。
任务列表只存在于工作台的「最近任务」卡里。

根因在 `WorkbenchShell.tsx`：
`showingTaskRun && selectedTaskId !== null ? <TaskRunDetails/> : <div className="task-run-empty">请选择一个任务</div>`。

**为什么没修**：让这个入口直接列任务是新功能（工作台已有 `taskSource.listTasks` 可复用），
不是小改动。**请你决定。**只要客户按顺序点一遍菜单就会撞上。

---

## P2 — 看得见，但属于版式取舍，我不擅自改

### 8. 顶栏 64px 只放两个不变的元素（实测）

`.desktop-header` 高 64px，内容是次要文字「抖音运营」+ 标签「本机桌面模式」，
八个页面完全一样、不随内容变。占 1280×800 的 8%。降到 44px 每页省 20px。

### 9. 每页标题重复左侧菜单项（实测）

八个页面里七个的 h2 是当前高亮菜单项的同义词，其中「平台状态」「视频制作」「视频剪辑」
「作品发布」「设置与诊断」五个**逐字相同**。标题行 64px + 20px 间距 = 84px，每页都付。

### 10. 大屏上内容列封顶 1180px（实测）

`.desktop-content main { max-width: 1180px; margin: 0 auto }`。1920×1080 下内容区 1688px，
实际只用 1180px，**左右各空 254px（合计 27%）**；侧栏是左对齐的，所以侧栏和内容之间
凭空出现一条 254px 的空带。同页底部还空 95px。

表单页这个封顶合理（行宽可读性），列表／表格页（最近任务、目标结果、事件时间线）浪费。

### 11. 视频制作「成片」页签的空状态定高（实测）

`.video-studio-panel { min-height: 330px }` + `.video-studio-panel .ant-empty { margin-block: 70px }`。
1440×900 实测：卡片 329px 高，里面只有 96px 内容，**上下各 70px 纯外边距**；
卡片下面还有 332px 空白。不确定这个定高是不是刻意的，列出来给你判断。

### 12. 设置与诊断整页 2290px（实测）

1280×800 下需滚 1576px（2.1 屏）。6 张卡片，其中 4 张各有一个 56px 的卡头band
（模型服务 / 视频剪辑服务 / 安全诊断记录 / 浏览器诊断采集），合计 224px 只用来写标题。

---

## P3 — 读代码推断，本次没能实地观察到

### 13. 目标预览列表每行 68px（读 CSS 推断）

`.task-target-preview-list li { min-height: 68px; grid-template-columns: auto minmax(180px,1fr) minmax(160px,auto) auto }`。
这是表格型数据，10 个目标就是 680px，一屏放不下。需要真实的目标发现结果才能实测，
本次 harness 走不到这条路径。

### 14. 视频剪辑的「时间轴编辑」「预览」两个页签没能实地看到（harness 限制）

harness 的 `shellVideoEditingGateway.createProject` 直接抛 `draft_storage_unavailable`，
建不出剪辑项目，这两个页签只能显示"请先创建或选择一个剪辑项目"。

它们用到的 `.video-editing-track`、`.video-editing-clip`、`.video-editing-duration-input`、
`.video-editing-transition-select`、`.video-editing-preview-clips` 同样**在 global.css 里
没有任何规则**。按第 1 条的同样机制，很可能也是塌成半宽、轨道没有任何视觉分隔。
**建议在真实 Tauri App 里补看一眼**——本次修复只覆盖了能实测的两个页签。

---

## 排除项（看过，不是缺陷）

- **最小窗口 960×640**（Tauri `minWidth/minHeight`）实测：无文档级横向或纵向滚动条，
  内容区只有纵向滚动，侧栏底部状态块（y=570）不与最后一个菜单项（底边 444）重叠。
  上一轮的壳布局修复在最小尺寸下依然成立。
- **作品发布页上的「暂时读不到发布状态」黄条**：harness 没有真实 Tauri 桥导致的，
  不是产品缺陷。
- **`account-login-screen` 类名没有 CSS 规则**：它和已有样式的 `startup-screen` 并列使用，
  实际样式由后者提供，只是个多余的钩子，不影响显示。

---

## 本次修复（TDD）

### 测试

新增 `frontend/e2e/screen-density.spec.ts`，视窗用 Playwright 默认的 1280×800，
即生产 Tauri 窗口的默认尺寸。共 12 条断言：

1. 视频剪辑「剪辑项目」页签的内容填满内容列；
2. 视频剪辑「提交与任务」页签的内容填满内容列；
3. 两个页签里卡片内部的内容填满卡片（第二轮补的，见下）；
4. 八个页面的首块内容与标题行之间都留有间距（≥16px）。

### RED 第一轮

```
$ pnpm exec playwright test e2e/screen-density.spec.ts

    Error: 视频剪辑「剪辑项目」页签只用了内容列 992px 中的 568px
    expect(received).toBeGreaterThanOrEqual(expected)
    Expected: >= 991
    Received:    568

    Error: 视频剪辑「提交与任务」页签只用了内容列 992px 中的 582px
    expect(received).toBeGreaterThanOrEqual(expected)
    Expected: >= 991
    Received:    582

    Error: 视频剪辑：标题行与首块内容之间只有 0px
    expect(received).toBeGreaterThanOrEqual(expected)
    Expected: >= 16
    Received:    0

    Error: 作品发布：标题行与首块内容之间只有 0px
    expect(received).toBeGreaterThanOrEqual(expected)
    Expected: >= 16
    Received:    0

  4 failed
  6 passed (3.6s)
```

四条红全部落在被指认的那两个页面上，数字和 `agent-browser` 实测完全一致；
其余六个页面的间距断言直接通过——证明断言本身是对的，问题确实只在这两页。

### RED 第二轮（第一轮的 GREEN 不够，补断言）

第一轮改完 `width: 100%` 后 12 条全绿，但去页面上实看，**结果只对了一半**：
外层容器和卡片都撑到 992px 了，卡片**里面**的 `Space` 仍然收缩成 518px——
于是变成一张宽卡片右半边空着，比修之前更难看。测试绿了而用户看到的还是错的。

补上"卡片里的内容要填满卡片"这条断言后重跑：

```
$ pnpm exec playwright test e2e/screen-density.spec.ts

    Error: 视频剪辑「剪辑项目」卡片里的内容没有填满卡片：[{"card":942,"content":518}]
    Error: 视频剪辑「提交与任务」卡片里的内容没有填满卡片：[{"card":942,"content":532}]

  2 failed
  10 passed (3.9s)
```

### GREEN

```
$ pnpm exec playwright test e2e/screen-density.spec.ts

  Running 12 tests using 9 workers
  12 passed (3.7s)
```

### 改动

| 文件 | 改动 |
| --- | --- |
| `frontend/src/styles/global.css` | `.video-editing` 上边距；`.video-editing-projects`/`.video-editing-jobs` 宽度 100%；`.video-editing-panel` 卡片内 `Space` 宽度 100%；`.publish-workspace-content` 上边距 |
| `frontend/src/app/WorkbenchShell.tsx` | `<PublishWorkspace>` 外包一层 `.publish-workspace-content`，与既有的 `.platform-session-content` 写法一致 |
| `frontend/e2e/screen-density.spec.ts` | 新增 |

改动后 `agent-browser` 复看实测（1280×800）：

| 指标 | 修前 | 修后 |
| --- | --- | --- |
| 视频剪辑「剪辑项目」页签宽 | 568px | **992px**（= 内容列全宽） |
| 视频剪辑「提交与任务」页签宽 | 582px | **992px** |
| 剪辑项目卡片内表单宽 | 518px | **942px**（= 卡片内容盒全宽） |
| 视频剪辑标题行间距 | 0px | **20px** |
| 作品发布标题行间距 | 0px | **20px** |

修后与设置页的「模型服务」卡片观感一致（整卡宽、输入框满宽），不是新造的样式。

### 回归

```
$ pnpm exec tsc -b                        # exit 0
$ pnpm exec eslint . --max-warnings 0     # exit 0
$ pnpm exec vitest run                    # 61 files / 523 passed | 1 expected fail
$ pnpm exec playwright test               # 32 passed
```

`tsc -b` 抓到过一处 eslint 没抓到的问题（`NodeListOf` 在本仓库的 target 下不可迭代，
已改 `Array.from`）——本仓库 `tsc --noEmit` 是空转，只有 `-b` 有效。

### 清理

- UI Harness dev server（端口 1523，本项目专属、避开 Playwright 用的 1420）已停止，
  端口已释放；
- `agent-browser --session ui-density` 已 `close`，无残留自动化 Chrome 进程；
- 观察用截图每看完一轮即删，任务结束时临时目录内为空。

### 未做的事

- 上表 P0 第 3～7 条、P2 第 8～12 条、P3 第 13～14 条全部保留在清单里，等你判断；
- 第 2 条提到的"20px 复制 7 份"这个隐患没有一并收敛，理由见该条。
