# 功能完整度盘点 · 2026-07-27

按**产品负责人口述的业务链路**逐条核对代码，不看台账、不看文档声称、不信代码里的枚举与映射表——那些都可能落后于现实。判据一律是**实现痕迹**：平台自动化跑不掉域名和页面选择器，读取消息跑不掉收件箱与未读，定时执行跑不掉调度器。

盘点基准（用户 2026-07-27 口述）：运营者每天要做的是——找当下分类下的热点视频 → 据此生成自己的视频 → 发布到各平台 → 回复自己账号收到的私信 → 去别人视频下评论 → 回复自己视频下粉丝的评论 → 在微信上自动通过好友申请、打招呼、回复聊天。目标是把这些用 AI 全自动化。

---

## 一、一句话结论

**能跑通的是一条"抖音单平台、人工触发"的链路。** 产品设想里的八件事，两件完整、三件残缺、三件完全没有。

| # | 业务链路 | 判定 | 一句话 |
|---|---|---|---|
| 1 | 抓当下热点 | 🟡 有能力，非自动 | 抖音的发现/候选/搜索曝光都真实存在，但**没有任何调度器**，每次都要人点 |
| 2 | 生成视频 | 🟢 完整 | 两条链路都有真实产物落盘，一句话成片在真实 App 上跑通过 |
| 3 | 发布到各平台 | 🟡 只有两个平台 | 抖音（浏览器自动化）+ B 站（官方 API）；**快手/小红书/视频号零代码** |
| 4 | 回复收到的私信 | 🔴 方向反了 | 只能**主动**从对方主页发起私信；**读不了收件箱，也没有未读概念** |
| 5 | 去别人视频下评论 | 🟢 完整 | 对候选目标评论，有任务入口、有执行器实现、有失败矩阵 |
| 6 | 回复粉丝的评论 | 🔴 没有 | 抖音执行器里没有任何"读自己视频下评论并回复"的实现 |
| 7 | 微信自动化 | 🔴 完全没有 | 零痕迹 |
| 8 | AI 入口 | 🔴 没有通用入口 | 只有"动效创作 agent"这一处受限用法 |
| + | 视频剪辑 | 🔴 有壳无芯 | 842 行 UI，数据存 localStorage，提交永远返回不可用 |

---

## 二、逐条证据

### 1. 抓热点 —— 能力真实，但"自动"不成立

**有的**：`douyin.discovery.v1`、`douyin.candidate.v1`、`douyin.search_exposure.v1` 三个任务模板真实存在；执行器侧 `rpa/douyin/` 下有 `search.py`、`search_page.py`、`candidate_extraction.py`、`bounded_scroll.py`、`browse.py`，是一整套。

**缺的**：**控制面里没有任何调度器**。搜遍 `control_plane/`，`BackgroundTasks`/`apscheduler`/`celery`/循环任务一个都没有；唯一命中"schedule"关键词的 `action_risk_policy.py:63` 是**风控最小间隔**（两次动作之间至少隔多久），不是定时触发。

**所以**：产品设想的"每天自动抓热点"目前是"人打开 App，手动创建一个发现任务"。这不是缺陷，是**首期本来就没做**——但它是"自动化运营"这个卖点的地基，缺了它，用户每天仍然要坐在电脑前点。

### 2. 生成视频 —— 唯一完整的一块

两条链路都有**真实落盘产物**（本机实测）：

```
material-video-worker  357 MB   ← moneyprinterturbo，"智能素材成片"
motion-video-worker    108 MB   ← hyperframes，"品牌动效成片"
media-toolchain         42 MB   ← 自编译 ffmpeg
subtitle-fonts          32 MB
```

上游按锁定 tag 以只读 submodule 引入，有 `check_third_party_sources.py` 守着不许改源码。一句话成片链路 07-27 04:06 在真实 App 上跑通过并产出真片（h264 640×360 / 12 秒 / 360 帧）。

**关于你对两个上游的理解，代码侧的实际情况**：

- **moneyprinterturbo** 确实是"给一句话 → 自动出片"：它自己完成脚本、配音、字幕、素材匹配。契约 `material-video-script-model-adapter.v1.json` 把模型调用抽象出来了。
- **hyperframes** 你的理解准确——它是**给 AI 用的工具箱**：提供模板与内置动效，由模型决定用哪个模板、每处放什么动效。代码里那个 `authoring agent`（`VideoStudio.tsx:1375` "Hand a one-sentence brief to the authoring agent"）就是干这个的，用户也能自己指定模板与动效（`motion-parts-catalog.ts`、`motion-style-authoring.ts`）。

### 3. 发布 —— 两个平台真实，三个平台零代码

用域名反查（有自动化实现必然出现平台域名）：

```
douyin.com               12 个文件
bilibili.com              5 个文件
xiaohongshu.com           0
kuaishou.com              0
channels.weixin.qq.com    0
```

执行器侧文件数：抖音 22 个，其余平台 0 个。

`PublishPlatform` 枚举里五个平台齐全，`PublishMechanism` 映射表把快手/小红书/视频号标成 `DEFERRED`，注释写着 "deferred means no entry point exists"。**这次核对下来，映射表与实现是一致的**——但请注意映射表本身不是证据，上面的域名与文件数才是。

发布闭环的**设计**是通的：`PublishWorkspace.tsx` 接 `artifactId`，界面文案写着"到「视频制作」的成片里挑一条，再回到这里发布"。

### 4. 回复私信 —— 方向反了，这是最容易被误判成"已完成"的一条

抖音有 `direct_message_action.py` 和 `direct_message_page.py`，任务创建页也有"私信"选项，看起来是做完的。但读实现：

```
direct_message_action.py:331   wait_for_profile_ready(...)      ← 先等对方主页就绪
direct_message_action.py:340   enter_conversation()             ← 从主页进入会话
```

**它做的是"打开某个人的主页 → 进入会话 → 发一条消息"**，即主动触达。而你要的是"看自己账号收到的私信并回复"。

更直接的证据：全仓库搜 `unread` / `inbox` / `未读` / `收到`，在 `executor/rpa/` 与 `control_plane/domain/` 下**零匹配**——产品目前**没有任何"读取收到内容"的能力**。

**这条的缺口不是补一个动作，而是缺一整类能力**：感知侧（读收件箱、识别未读、抽取对话上下文）目前一行都没有。

### 5. 主动评论 —— 完整

`comment_action.py` 以 `target_summary: DouyinCandidateSummary` + `target_id` 为输入，对候选目标（别人的视频）发表评论，有 `comment_page.py` 做页面定位，任务创建页有"评论"选项。这条与第 4 条共享同一套"目标 → 动作 → 回执"骨架，是完整的。

### 6. 回复粉丝评论 —— 没有

与第 4 条同源：没有"读自己视频下的评论"这一步，自然也没有回复。`rpa/douyin/` 下搜 `notification` / `我的评论` / `reply_to` / `粉丝`，零匹配。

### 7. 微信自动化 —— 完全没有

搜索范围覆盖 Python、TypeScript、Rust 三侧：

- `weixin.qq.com` / `channels.weixin.qq.com`：0 个文件
- `WeChat.exe`、`微信.app`、`uiautomation`、`pywinauto`、`AXUIElement`（桌面 UI 自动化必需的东西）：**零匹配**

代码里唯一与"微信"沾边的是 `PublishPlatform.WECHAT_CHANNELS`，那是**微信视频号**这个发布目标的枚举值，与"控制用户电脑上的微信客户端"没有任何关系。

**这条要注意架构影响**：现有 RPA 全部建立在 Playwright + 内置 Chromium 之上，而微信客户端自动化是**桌面 UI 自动化**（Windows 上 UIAutomation、macOS 上 Accessibility API），是另一套技术栈、另一套权限模型、另一套失败矩阵。它不是"再加一个平台 Adapter"，是**新开一条腿**。

### 8. AI 入口 —— 只有一处受限用法

前端 `features/` 下没有任何对话/助手页面。唯一的模型调用入口是动效创作：`VideoStudio.tsx` 把一句话简报交给 authoring agent 生成动效脚本（`motion-model-call.ts`、`motion-one-sentence.ts`）。

也就是说：**AI 目前只能"把一句话变成一条视频"，不能"帮你决定该做什么"**。产品设想里的"AI 员工"（读热点决定选题、读私信决定怎么回、编排整天的工作）还没有落点。

### 9. 视频剪辑 —— 有壳无芯（你点名的那条）

代码自己写着，不用我推断（`local-video-editing-gateway.ts` 文件头注释）：

> The cloud editing provider chain (VE-04+) is not connected yet, so projects and timeline revisions live in an App-local draft store; **there are no editing jobs and submission always fails closed as unavailable.**

具体形态：

- 前端 `VideoEditingWorkbench.tsx` **842 行**，界面完整；
- 数据存在 **localStorage**（`automation-tool.video-editing.local-draft.v1`）；
- 后端 `api/` 目录下**没有任何 video_editing 端点**——领域层（`domain/video_editing.py`、`aliyun_ims_editing_*.py`）写了不少，但**没有一条 HTTP 路由把它暴露出去**；
- 因此提交剪辑任务**必然失败**，返回"不可用"。

这是全项目"投入产出比"最需要留意的一处：领域层 + Provider 抽象 + 一致性契约 + 842 行 UI 都做了，唯独中间那段接线没有，于是用户视角就是"点了没反应"。

---

## 三、按业务价值排的缺口优先级

这不是工程排期，是"离你描述的那个产品还差什么"，从最挡路的往下排：

1. **感知能力整体缺失**（第 4、6 条）——读收件箱、读未读、读自己视频下的评论。缺了它，"自动回复"这半个产品不存在，而这恰好是运营者每天耗时最多的部分。目前的实现全是"主动发出"，没有一处"读进来"。
2. **没有调度器**（第 1 条）——所有"自动"目前都要人点。这是把"工具"变成"员工"的分界线。
3. **微信是另一条腿**（第 7 条）——技术栈与现有 RPA 完全不同，越早确认它的技术路线越好，不要等到最后当成"再加个平台"。
4. **平台横向扩展**（第 3 条）——快手/小红书/视频号零代码。好消息是抖音那套（页面定位 / 动作编排 / 频控 / 结果解析分层）是按可复制的结构写的，第二个平台的成本应该显著低于第一个。
5. **视频剪辑接线**（第 9 条）——已投入很多，离可用只差 API 端点 + 网关切换，是性价比最高的一块。
6. **AI 编排入口**（第 8 条）——需要产品先想清楚"AI 替人做决定"的边界在哪，再谈实现。

---

## 四、这份盘点的边界

- **我核实的是"代码是否存在、能不能被用户走到"**，不是"跑起来质量如何"。凡标 🟢 的，只代表实现完整且有入口，不代表在真实平台上的成功率。
- **没有逐个真跑**。视频生成那条引用的是 07-27 04:06 的真实 App 记录；其余判定基于代码结构与实现痕迹。
- **台账与代码里的枚举/映射表都没有作为判据**，只在结论一致时作为旁证提及。
