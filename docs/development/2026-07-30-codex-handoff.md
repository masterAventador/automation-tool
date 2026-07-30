# 本地智能剪辑线交接文档(2026-07-30,交接给 codex)

> 写给接手者:本文档把剩余 17 个任务全部拆成可以逐条执行的子任务。**每个子任务都按
> "先写红测试 → 最小实现 → 绿 → 门禁"走**,拿不准的地方文档里标了【先定】,先把决策
> 写进对应 `docs/development/<任务ID>.md` 再动手。不要跳步,不要合并子任务。

## 0. 现状定格(接手第一件事:核对这一节)

- 工作分支 **`smart-edit`**,HEAD `27fa185`(LE-07 整线合并节点),已推送 origin;
- 台账唯一事实源:`docs/local-video-editing-roadmap.md`。当前 24 任务 = ✅4(LE-01~04)
  + 🔍3(LE-05/07/09)+ ⬜17。`scripts/check_local_editing_roadmap_counts.py` 守计数,
  **每次改台账后必须跑**;
- 每个任务的证据台账在 `docs/development/<任务ID>.md`(LE-05/07/09 的收口节里有给
  后续任务的交接,拆解里会点名引用);
- 工作树:`wt/smart-edit` 是主线;`wt/le-07-probe` 已合并、干净,可以
  `git worktree remove wt/le-07-probe` 清掉(先确认无人在里面工作);
- **合并回 main 的时机与全 App 全量测试须先与用户对齐**(2026-07-30 时点用户在另一
  会话修全 App 的既有 bug,明确指示各任务只跑波及范围)。

## 1. 工作方式(这是"精神",每个任务都照此执行)

1. **TDD 铁律**:没有失败的测试不写生产代码。每个子任务先写红,亲眼看到红再实现。
   发现 bug 先写暴露它的红测试再修;
2. **每个任务两轮独立审查**:实现完成后,(a) spec 符合性审查——逐条对拆解清单核
   "有没有做、有没有多做";(b) 代码质量审查——判据是"**把生产代码改坏一处,测试
   会不会红**",不是"测试绿不绿"。审查发现的问题修完要复审确认。没有审查工具就
   自己隔天用新鲜眼光做,或交用户安排;
3. **数字纪律**:文档里每个数字写之前先数一遍出处;同一个数字不许存在两处;
   "两处错互相抵消凑出对的总数"在本线发生过两次,逐个核出处是唯一解法;
4. **不可能失败的断言**:每写一条守卫测试,先把被守的东西弄坏一次,确认测试真的红。
   本线抓过的假测试全是这个形状("加不加修复都绿");`cmd > log; echo exit=$?` 的
   退出码永远是 0,命令后面不要拼东西;
5. **边界四件套**:引入任何区间/有界集合/跨字段比较,必须有 `a`、`a-1`、`b-1`、`b`
   四个端点用例;跨字段比较的夹具必须让两边有区分度(正方形画幅、等长区间这类
   无区分度夹具视为缺陷);
6. **§9.1 验收深度**:每份 `docs/development/<ID>.md` 开头声明 `用户可操作:是/否`;
   "是"的证据必须是**没真跑过写不出来**的终态读数(ffprobe 读数、文件尺寸+摘要、
   库里的行),"测试 passed/按钮可点/接口 200"都不算;
7. **状态纪律**:验收过了第一件事改台账,再写总结;只有正式 App 用户路径验收过的
   才能标 ✅,分层证据齐全但无用户路径的顶格 `🔍 待验收`;
8. **真实依赖**:数据库用真实 PostgreSQL(docker compose 的 postgres-test),媒体用
   随包 ffmpeg/ffprobe 现造素材(**时长刻意偏离整秒与 100ms 网格**,整秒素材曾放过
   真 bug),模型调用用真实百炼(用户已批准真实云调用费用,别为省钱缩水验收);
9. **失败矩阵**:每个跨进程/有外部副作用的任务,开工前把 CLAUDE.md §9 适用行列出来
   写进任务文档,逐条给测试或"不适用理由"。

## 2. 剩余任务拆解(按建议执行顺序;依赖以台账行为准)

> 通用约定:每个子任务的"红"指先写失败测试;门禁指 `ruff format --check + ruff
> check + mypy`(改动面)+ 相关包 pytest;后端集成测试**整目录跑**(`tests/
> integration` 不耐受任意子集组合,LE-05 实证);台账行在任务开工时改 `🚧`、
> 收口时按验收深度改状态并跑计数脚本。

### LE-06 剪辑 REST API(前置已满足,第一个开工)

输入:`docs/development/LE-05.md` 收口节「给 LE-06」。既有约定:`api/errors.py` 的
`AppError(status_code, code, message, retryable)` + `ErrorEnvelope`,路由注册看
`api/__init__.py`,样板看 `workbench.py`/`tasks.py`。

- **T1 错误映射层**:写一个单点转换(应用层持久化异常 → AppError)。词汇:四个应用
  模块(`application/editing_projects|materials|timelines|editing_jobs.py`)子类数
  **4/5/5/7**;`timelines` 的两个专有失败**类名不按前缀猜,打开文件抄真名**(LE-05
  交接明说第一版猜错过)。映射基线:AlreadyRegistered/Stale/RevisionAlreadyQueued/
  DescriptionProtected→409、NotFound→404、Unavailable→503(retryable=true)、
  DataRejected→500 族(库内的行域层拒收=服务端持久态损坏,**不是**调用方错误)、
  TimelineRevisionMissing→409。红:**词汇守卫**——动态发现四模块全部
  `_*PersistenceFailure` 子类,存在未映射的类即红(照 LE-05 的动态发现先例);
- **T2 项目路由**:CRUD + `OutputSpec`(画幅/帧率/字幕样式基线,LE-04 已在域层承
  载)。域校验拒绝→422。红:非法画幅、缺字段、不存在 project 404;
- **T3 素材路由**:登记与查询。**本机路径不过 API 边界**(§7;`Material` 域对象本
  就无路径字段,路径只活在执行器侧 `MaterialPathRegistry`)。AI 描述保护:
  `MaterialDescriptionProtected`→409。红:请求体里出现路径形状字段被拒、描述保护
  往返;
- **T4 时间轴路由**:保存与修订。revision 冲突→409 **且响应带当前 revision**(前端
  要靠它重拉)。仓储 `update(previous, changed)` 双参 CAS 约定照 LE-05.md 交接;
  Stale→重读重试、NotFound→404、DataRejected→500,三个答案动作各不同,别合并;
- **T5 任务路由 + 契约测试收口**:提交与查询;`RevisionAlreadyQueued`→409。
  **契约测试真实起服务**:uvicorn 起真端口(先查占用,用 automation-tool 专属端口
  段)+ 真实 PostgreSQL,走 HTTP 断言——进程内 TestClient 只能当分层证据(生产同
  路径原则)。查询分页:`updated_at` **不唯一**(CAS 的接受条件含相等),排序必须带
  tiebreaker(如 job_id)。收口:LE-06.md 写全线证据,台账 LE-06 行判状态(REST 面
  无正式 App 用户路径,顶格 `🔍 待验收`,补验收依赖 LE-17)。

### LE-08 自适应抽帧

输入:台账 LE-08 行的 LE-07 交接(**四条硬性质必须照抄**:输出落文件不走管道、
边写边量、超限即杀、**拒绝理由用返回值而不是抛异常**;`PackagedMediaTools` 公开
直接用;`_run_bounded` 私有**不得跨模块引**,照 `_require_tool` 先例在本模块重写
同款)。

- **T1 运行器**:本模块的 bounded ffmpeg 运行器(重写,含上限/超时/杀死回收)。红:
  超限恰好/多一字节两端、超时子进程被杀无残留、清理失败不改判(LE-07 C1 的教训,
  写一条"业务结论先于清理失败"的用例);
- **T2 场景检测抽帧**:`select='eq(n,0)+gt(scene,TH)'`。红:用随包 ffmpeg 现造
  多镜头素材(硬切拼接),断言抽出帧数与切点对应;单镜头素材只抽首帧;
- **T3 长镜头按时间补抽**:红:60s 单镜头素材,补抽间隔断言;
- **T4 分档封顶与保切点降采样**:【先定】各时长档的帧数上限表写进 LE-08.md。红:
  超限素材降采样后**切点帧保留**、总数不超限——上限表每档都要贴边界用例;
- **T5 产出与收口**:768px JPEG,断言帧数、文件存在、最长边 768。失败矩阵:坏素材、
  暂存不可写(沿用 `WORKSPACE_UNUSABLE` 语义:是我的暂存,不是你的文件)。变异抽查
  + LE-08.md 收口。

### LE-13 素材理解(依赖 LE-08)

- **T1 Adapter 形状**:百炼多模态经供应商无关 Adapter(§6 基线:AI 经 Adapter 接
  入)。`enable_thinking=false` 是**配置项**不是硬编码(设计 §4.5,LE-24 要统一控
  制它)。红:请求体断言该字段随配置变化;
- **T2 抽帧→描述/标签/镜头区间**:写回 Material(注意描述保护:AI 不覆盖用户改过
  的描述,域层已挡,测试从 API 面钉一遍);
- **T3 失败矩阵**:超时/拒答/空描述/token 超限,逐条注入;
- **T4 真实模型验收**:真实百炼调用,真素材,断言产出写回。证据:请求 id + 写回的
  行。收口 LE-13.md。

### LE-14 人声检测与转写(依赖 LE-07,可与 LE-13 并做但**不要并行开工**,串行)

- **T1 三级漏斗第一级**:`silencedetect` 判有无声音(LE-07 的音频事实可复用,先查
  `MaterialFacts.has_audio` 再决定是否进二级);
- **T2 Silero VAD**:本地 ONNX(约 2MB)。**onnxruntime 与模型必须进安装包**:
  【先定】装配路径(照 `scripts/subtitle_font_assets.py` 按 SHA-256 进构建缓存的
  先例)+ 出厂门禁核对。红:模型缺失时可操作报错不静默跳过;
- **T3 百炼 ASR 转写**:只传音轨不传视频(ffmpeg 抽音轨,复用 LE-08 的 bounded
  运行器形状)。产出 `has_speech`/`speech_segments_ms`/`speech_transcript` 写回;
- **T4 失败矩阵**:纯音乐误判、环境音、转写空、方言嘈杂、ASR 超时、背景路人说话;
- **T5 真实验收**:真人声素材 + 真 ASR 调用,收口 LE-14.md。

### LE-15 文案分句与旁白合成(依赖 LE-06、LE-14)

- **T1 分句**:一句话经百炼文本模型出脚本分句(`enable_thinking=false` 同 LE-13);
- **T2 TTS**:每句合成并取**真实音频时长**(ffprobe 读,不信 TTS 自报);
- **T3 用户录音替代**:上传录音→转写(复用 LE-14 ASR)→与句子对齐;
- **T4 失败矩阵 + 真实验收**:TTS 超时/空音频/时长为零;收口。

### LE-16 语义匹配与片段选择(依赖 LE-13、LE-15)

- **T1 匹配**:句子×素材描述(有转写的把转写并入匹配依据);
- **T2 片段选择**:选中素材的镜头区间内挑 in/out 点(锁死"取片时长=占位时长",
  LE-03 不变速约束);
- **T3 有人声素材编排**:自带旁白片段——时长由原声决定、独立占段、用原声与转写
  字幕、**不配 TTS**;文案句子只分配给无人声素材;
- **T4 边界处理**:素材不足、单条过短、匹配全低于阈值、全部素材均有人声,四种
  各自的产品行为【先定】写进 LE-16.md 再实现;
- **T5 产出 Timeline 草稿**:过域层全部不变式(轨道相接、时长对齐),红:草稿直接
  能被 `Timeline` 构造器接受。收口。

### LE-10 视频渲染管线(依赖 LE-03、LE-09;可在 LE-13~16 之前做,与 LE-06 无依赖)

开工前两个【先定】(台账行原文,LE-04 终审留下的):① 16:9 进 9:16 是加黑边还是
裁切,`OutputSpec` 现无字段表达,要加字段(域层改动走 LE-04 的测试形状);② 毫秒
时间轴×fps 帧栅格的对账:谁量化、允许多大偏差,**定死并写进 LE-10.md**——完成
定义是 ffprobe 断言帧数/时长,不定死就没法写断言。

- **T1 OutputSpec 增字段 + 对账规则**(域层,先红后绿);
- **T2 单 clip 渲染**:trim→scale/crop→fps 归一,ffprobe 断言取自 OutputSpec;
- **T3 concat + xfade 转场**(转场靠真实重叠,LE-03 语义);
- **T4 字幕 overlay**(接 LE-09 的 PNG;字幕位置/边距在此定);
- **T5 `ffmpeg-toolchain.v1.json` 补 `required_capabilities.filters`**(xfade/
  select/scdet 等,无需重建 ffmpeg,只是声明+校验);
- **T6 收口**:整条管线端到端,ffprobe 断言编码/分辨率/帧数/时长逐项对 OutputSpec。
  编码交接:`video_codec`/`audio_codec` 只在执行器侧 `MaterialFacts`,**Control
  Plane 拿不到**——要不要转码在持有文件的本机判;若确需跨层传,domain 加字段并
  同步缩小 `test_material_probe_media.py` 的 `FACTS_WITH_NO_FIELD_IN_THE_DOMAIN`
  (有结构测试守着,不会静默)。

### LE-11 音频管线(依赖 LE-10)

开工前【先定】(台账行原文):三态开关(自动闪避/固定音量/静音)的模型表达——加第六
种轨道、给 clip 加处理方式字段、还是收窄设计承诺。"固定音量"需要一条既不被旁白压
也不当 sidechain 源的通路,现五种轨道没有。定了写 LE-11.md 再动手。

- **T1 模型表达**(域层);**T2 sidechaincompress 闪避**(旁白为源);**T3 `has_audio`
  假不排 ambient**;**T4 采样率归一 + 输出音轨时长与成片一致断言**;**T5 三态开关
  端到端 + 收口**。

### LE-12 Worker 生命周期与任务控制(依赖 LE-11;Rust 侧)

LE-07 交接三条(台账行):① Rust 把**已校验的** ffmpeg/ffprobe 路径下发 Worker,
Python 接收端(`PackagedMediaTools`,不发现不查 PATH 不读环境变量)已交付;② 编码
事实只在执行器侧;③ 暂存卷满产出 `WORKSPACE_UNUSABLE`,Worker 失败上报要能表达
这一类(不是"文件坏"不是"重试")。

- **T1 启动与握手**:随机 loopback + 高熵会话令牌(令牌不进命令行/环境变量/日志,
  §4.3 基线);**T2 健康检查与进度上报**;**T3 取消与紧停**(协作式,执行器确认终态
  才宣称停止);**T4 崩溃恢复 + App 退出后任务恢复**(任务快照是权威,§4.4);
  **T5 `cargo test` 全覆盖 + 失败矩阵收口**(Sidecar 未启/超时/崩溃/挂起/版本不匹
  配,§9 全列)。

### LE-17 工作台接真实网关(依赖 LE-06;前端接线第一棒)

- **T1 重写 `video-editing-dto.ts`**:**按 `timeline.py` 整体重写,不逐点修补**。
  台账行列了七处分歧(strictObject 缺三字段、sourceArtifactId→MaterialId、重叠
  refine 与转场冲突、核心不变式缺失、transitionKind 含 "cut"、单一音频轨、
  MAX_TRACKS 32→5)。红:LE-03 终审的三个真实 zod 探针(带转场时间轴、三新字段
  clip、三种音频轨)全部要从"拒绝"翻成"接受";
- **T2 生产组合根**:`main.tsx` 从 sessionStorage 草稿网关换真实 Control Plane
  网关(经 PlatformAdapter,业务页面不碰 `@tauri-apps/*`);
- **T3 提交路径打通**(不再固定抛错),错误映射消费 LE-06 的 ErrorEnvelope
  (409 冲突→重拉 revision 重试的 UI 行为);
- **T4 重写 `e2e-tauri/video-editing.spec.ts`**:断言从"诚实展示不可用"改为真实
  出片路径(此时渲染未必全通,断言到"提交成功+任务状态推进"即可,出片断言等
  LE-10~12 齐);
- **T5 收口**:Playwright UI Harness + WebdriverIO 真实 Tauri 双层过;LE-05 的
  补验收在此闭环(真实 App 经正式 Rust 网络桥打到真实后端,四表落行核对)——
  **LE-05 行此时可升 ✅,升之前逐条核 LE-05.md 的完成定义**。

### LE-18 素材库界面(依赖 LE-17)

LE-07 交接硬约束(台账行 + `docs/development/LE-07.md`「交接给 LE-18」,**开工前
通读那一节**):

- **T1 先补 `MaterialPathRegistry.forget`**(现无删除接口,素材删除功能的前置;
  执行器侧,按 LE-07 的测试纪律:真实文件、变异、锚点唯一);
- **T2 导入链路**:四步公开配方 `approve_source` → `probe_material` → `register`
  → `require_source_unchanged`,**不要自己 `Path.stat()`**(裸 FileNotFoundError
  带操作者私有路径);状态目录**显式 0700**(普通 mkdir 在 umask 022 下 0755 会被
  判 `REGISTRY_UNREADABLE`,注册表有意不自建目录);
- **T3 素材列表与详情**:AI 描述/标签展示与编辑(描述保护语义)、有人声标注+试听+
  转写查看(消费 LE-14);
- **T4 去重与缺失提示**:摘要判重的**非对称**语义(摘要相同=内容相同;摘要不同
  **不能**说成两条素材——截断件与完整件摘要不同而元数据全同);缺失三种理由分开
  文案:`FILE_MISSING` 去找回、`FILE_UNREADABLE` 别去找(还在原地但读不了)、
  `FILE_CHANGED` 重新导入;
- **T5 拒绝码文案表**:全部 14 码给中文文案,硬约束两条——`UNDECODABLE` **不得
  写死"文件已损坏"**(默认布局 MP4 下载完成前一律报它,必须留"稍后重试");
  `WORKSPACE_UNUSABLE` 是"本机暂存空间不够/不可写"。文案过
  `check_user_facing_branding.py`;
- **T6 收口**:正式 App 用户路径验收(导入→看到时长/画幅/有无声音)——**LE-07 行
  此时升 ✅ 的验收点,升前逐条核 LE-07.md**。

### LE-19 智能剪辑入口(依赖 LE-16、LE-18)

- **T1 一句话→草稿落工作台**(消费 LE-16);**T2 一键直出片**(跳过审阅走同一生成
  器,不是第二条代码路径);**T3 进度与取消可见**;**T4 产品形态定稿**:更新
  `contracts/quality/user-facing-terminology.v1.json` 的 `video_editing_module`
  条目与实际界面一致,**`check_user_facing_branding.py` 转绿**(这是全仓已知红,
  归本任务);**T5 收口**:用户路径验收。

### LE-24 深度思考开关(依赖 LE-16、LE-19)

- **T1 总开关**:统一控制素材理解/文案分句/语义匹配三处 `enable_thinking`;红:
  三处请求体字段随开关变化的断言;**T2 偏好持久化**(下次进入保持);**T3 耗时
  实测**:固定素材集实测开/关端到端耗时差,**数字禁止编造**;线性则按条数动态展示,
  否则展示实测区间,数据与算法写 LE-24.md;**T4 文案 + 收口**(过 branding 脚本)。

### LE-20 中文字体扩充与装配(依赖 LE-09;独立于渲染线,可穿插)

- **T1 重估必要性**(台账行:设计 §6.1 的前提已被 LE-09 实测推翻——生产在册的是
  Noto Sans CJK SC,扩展 B 的 `𠮷` 有真实字形):**先量清楚缺哪些字**(拿 cmap 对
  常用字表+生僻字样本跑覆盖统计),量完可能结论是"不需要引入新字体";
- **T2(若确需引入)**:锁版本锁 SHA256、许可证+SBOM 登记、生产装配路径+出厂门禁
  (照 `subtitle_font_assets.py` 先例;**单一构建路径**,不允许只有测试路径);
- **T3 用户可选字体**(消费 LE-09 的 CaptionStyle);
- **T4 处理 `check_embedded_browser_package.py:106` 的 `local-executor: 177 MiB`
  上限**(LE-09 已引入 pillow+fonttools+brotli 约 32MB,会冲击;新上限数字要有
  实测出处);收口。LE-09 的补验收之一在此。

### LE-21 失败矩阵联合验收(依赖 LE-12、LE-19)

设计文档 §8 全部场景逐条:素材消失、磁盘满、权限拒绝、渲染超时、进程被杀、取消
竞争、App 退出恢复。每场景一条自动化用例,注入真实故障(kill 真进程、真填满卷—
—LE-07 修复轮的 ram disk 手法可抄)。收口 LE-21.md,逐场景给证据。

### LE-22 macOS 正式包纵向验收(依赖 LE-20、LE-21)

**全新安装的正式包**、正常用户入口:导入素材→一句话生成草稿→出片→成片入库可播放;
素材含至少一条有人声,**核对它走原声而非 TTS**;证据:ffprobe 读数+产物尺寸+摘要。
注意两条机器事实:出包用的 backend venv **必须 standalone Python**(Homebrew Python
会死在执行器签名且报错不指向解释器);出包凭证与 Chromium/视频运行时缓存本机已就位,
出包前先核缓存。**验收跑的产物必须就是用户拿到的那个包**(单一构建路径自检三问)。

### LE-23 Windows 正式包纵向验收(依赖 LE-22)

同 LE-22 在 Windows 独立走一遍;**核对随包 ffmpeg、onnxruntime+VAD 模型、字体在
Windows 包内真实存在**(打开包看,不是看构建脚本);平台专属功能不得用 macOS 证据
代替。

## 3. 三个 🔍 任务的补验收路径(别忘了收)

| 任务 | 升 ✅ 的时点 | 核对什么 |
| --- | --- | --- |
| LE-05 | LE-17 T5 | 真实 App 经正式网络桥写四表,库里行可核对 |
| LE-07 | LE-18 T6 | 正式 App 导入素材看到时长/画幅/声音 |
| LE-09 | LE-10 T4 + LE-20 + LE-17 之后 | 字幕真实进成片,字体装配有出厂门禁 |

## 4. 陷阱清单(全部实测过,别重新踩)

1. `check_user_facing_branding.py` 现在是**红的**,是前端既有文案问题,归 LE-19;
   看到它红不代表你改坏了;
2. mypy 全量基线 **17 errors / 8 files**(既有),判"没引入新错"要算**文件交集**,
   不能只看总数;`tsc` 用 `-b`,`-p frontend/tsconfig.json` 是空编译假绿;
3. executor 的素材探测测试(463 条)需要随包工具链缓存
   (`~/Library/Caches/automation-tool-build/media-toolchain`),没有就先跑
   `scripts/prepare_video_runtime.py`——30 条用例会硬红并报这句话,是有意设计;
4. `backend/tests/integration` **整目录跑**,别挑子集(组合不耐受);真实 PG 由
   fixture 起 docker compose 的 postgres-test,跑前确认端口没被占;
5. 变异/破坏性探针**一律在隔离副本**跑:复制 src + PYTHONPATH(或改 `.pth`)+
   `assert 副本路径 in module.__file__` + 清 `__pycache__` + `-B`;结束靠读
   `git diff` 核对还原,别信脚本打印的摘要;
6. `scripts/mutate_material_probe.py` 是素材探测的变异回归器(160 条五组,约 15
   分钟,支持组名过滤),改 `material_probe.py` 后要跑;新锚点先做唯一性预检;
7. 台账表格行**格子数必须是 5**,单元格里的竖线要写 `\|`——计数脚本会拒绝,这是
   守卫不是 bug;
8. `feature-audit` 分支上有两个本线没有的门禁(`test_shipped_package_boundary.py`
   AST 检查、`check_acceptance_evidence_depth.py` 验收深度)。**与那条线合流后必须
   跑验收深度脚本**,且不得把本线的证据文件加进它的豁免清单;
9. worktree 一律用 `python3 scripts/new_worktree.py <名称> [提交]` 建(默认基于
   origin/main,**要当前代码必须显式传 HEAD**),建完先跑一条最便宜的冒烟;细节见
   项目 CLAUDE.md §8.1;
10. colima/docker:启动前看 `colima list` + `docker context ls`,别停别人的
    profile;卡住先看 CPU——0% 是在等(网络/镜像拉取),非 0 才是在算。

## 5. 未收口的已知遗留(不阻塞,按台账登记)

- H8-23:六个仓储 17 处 try 缺 `SQLAlchemyError` 捕获(分布 2/7/4/2/1/1,定位在
  `docs/development/LE-05.md`),已登记主台账 Wave 8;
- schema.py 约束门禁只有 `editing_jobs` 是等值比对,其余三表单向 `>=`,抓不到
  autogenerate 漂移(LE-05 遗留);`revision` 域上界缺失;
- `_run_bounded` 的 C2 保证由"拒绝理由用返回值"结构撑着,T7 变异组没有
  return→raise 方向的变异——**动它的返回约定时要补两条**(LE-07.md 遗留节有记);
- 目录 fsync/单写者假设/registry 无 forget:交接 LE-18(T1 就是补 forget)。

## 6. 给 codex 的最后三句话

1. 台账状态永远先于汇报改对,找不到证据的"已完成"和"未完成"一样不可信;
2. 每写一条守卫,先弄坏一次看它红不红;
3. 一个任务做完必须过两轮审查再进下一个,审查抓出的每条要么修掉要么实测顶回,
   没有第三种处置。
