# 第三方源码、许可证与资产权利治理

> 适用范围：`vendor/moneyprinterturbo`、`vendor/hyperframes` 及其后续构建依赖、字体、素材、音乐、音效、地图、3D 模型和编解码器
>
> 权威机器清单：`contracts/quality/third-party-sources.v1.json`、`contracts/quality/asset-rights-policy.v1.json`、`third_party/source-submodules.cdx.json`

## 1. 上游源码只读

两个上游仓库只以 Git submodule 进入 `vendor/`，固定正式 tag 和完整 commit，不跟随 `main`、`latest` 或浮动分支。运行时禁止拉取、更新或安装上游源码；App 代码、构建脚本和模型都不能写入 submodule。

当前锁定版本：

| 内部用途 | Submodule | 正式版本 | Git commit | 许可证 |
| --- | --- | --- | --- | --- |
| 智能素材成片 | `vendor/moneyprinterturbo` | v1.3.2 | `b1588e1fdc6c5e54358f66ca2ff323e1dddf1364` | MIT |
| 品牌动效成片 | `vendor/hyperframes` | v0.7.68 | `71d84ff27f1c2b2828f4fdf9015c3da4157140ee` | Apache-2.0 |

Hyperframes 的 v0.7.68 是 annotated tag：tag object 为 `646be2278c159618bbbbb917b6063709bcbb3962`，实际 Gitlink 必须固定其 commit `71d84ff27f1c2b2828f4fdf9015c3da4157140ee`，不能把 tag object 当成可检出的提交。

`python3 scripts/check_third_party_sources.py` 必须同时验证：

- `.gitmodules` 只有登记过的 HTTPS 官方仓库且没有 branch；
- 父仓库 Gitlink、submodule HEAD、正式 tag、origin 与锁文件一致；
- submodule 无修改和未跟踪文件；
- 上游 LICENSE 的 SHA-256 与 SPDX 记录一致；
- 源码 SBOM、版本、commit 和许可证与锁文件一致；
- 未登记资产默认拒绝进入发行包。

任何适配、主题、离线资源替换和安全修复都写在 App 自有目录，以 Adapter、Overlay 或构建转换完成，不修改上游文件，不维护私有 Fork，不使用 Monkey Patch。上游暂时没有安全版本时，优先在外围禁用受影响能力或收紧输入；不能靠脏 submodule 临时发版。

## 2. 独立升级任务

每次升级只在一个独立升级任务和独立提交中进行，不与业务功能混合：

1. 确认父仓库与两个 submodule 都是干净工作树，先同步最新 `main`。
2. 从官方 GitHub Releases 和远端 tag 同时确认最新稳定版；预发布、移动 tag 和未发布 `main` 不进入候选。
3. 记录 tag object 与 `tag^{commit}`，验证来源、签名状态、完整 commit 和 release notes；不使用短 SHA。
4. 审计从旧锁到新锁的完整 diff，重点检查许可证、遥测、网络、Shell/文件权限、浏览器下载、模型工具、素材和依赖变化。
5. detached checkout 新 commit，更新 Gitlink、源码锁、LICENSE 摘要和 CycloneDX SBOM；如果许可证、维护方或仓库发生变化，必须重新做法务与安全评审。
6. 重新生成 Provider 专属依赖 SBOM、离线资产清单与第三方声明，运行源码检查、Provider 测试、134 项目录测试、双平台打包和失败矩阵。
7. 用户可操作能力必须从正式 App 的正常用户入口重新验收，不能用直接运行上游 WebUI、CLI、Mock 或单元测试代替。
8. 同一提交包含新 Gitlink、锁、SBOM、测试、独立证据文件和 Roadmap 状态；推送后合并最新 `main`。失败时回退整个升级提交，不混合回退部分文件。

## 3. 许可证与第三方声明

MIT 和 Apache-2.0 只授权对应仓库源码，不自动授权仓库或运行时引用的字体、照片、视频、音乐、音效、人物肖像、商标、地图数据、3D 模型、浏览器和编解码器。

- 源码 LICENSE 原文保留在只读 submodule，并用摘要防漂移；正式包的“第三方软件声明”由构建期从锁文件和 LICENSE 生成，不从网络读取。
- Apache-2.0 组件必须保留许可证、适用的版权/NOTICE 和修改说明；当前上游没有独立 NOTICE，升级时必须重新检查。
- 任何许可证不兼容、来源不明、只允许个人使用、禁止再分发或无法提供必要声明的内容都不得进入正式包。
- 产品功能 UI 不展示上游项目名；依法需要的名称、版权和许可证只出现在独立法务页面、SBOM 和内部诊断中。

## 4. 字体、素材与编解码器

`asset-rights-policy.v1.json` 使用默认拒绝：没有完整登记和审核就不能打包、缓存、生成预览或作为默认示例。

| 类别 | 必须证明的权利和信息 | 失败处理 |
| --- | --- | --- |
| 字体 | 来源、准确许可证版本、文件摘要、嵌入/再分发/修改权限和署名 | 换成权利明确的本地开放字体，禁止保留远程 Google Fonts 请求 |
| 图片/视频素材 | Provider 资产 ID、取得时间、许可版本、商用/再分发、人物与物权授权、商标审查 | 不下载或不启用；“免费”不能替代权利记录 |
| 音乐/音效 | 同步到视频、商用、再分发、署名与 Content ID 风险 | 不进入模板和安装包，不静默换源 |
| FFmpeg/编解码器 | 二进制来源、版本、构建选项、目标市场、许可证和专利评审 | 只启用通过评审的构建；缺项时对应输出能力不可用 |
| 地图/3D | 数据或模型来源、衍生与再分发权限、署名、文件摘要 | 保留零件结构但禁用示例资产 |
| AI 生成资产 | 模型/版本、生成记录、文件摘要和人物/商标/近似作品人工复核 | 复核失败即删除，不用“AI 生成”规避第三方权利 |

下载时的网页许可内容会变化，因此每项资产必须记录 `licenseVersionOrDate` 和取得时间；仅保存 URL 不足以证明发布时权利。最终包必须断网扫描，无登记远程脚本、字体、媒体或模型一律失败。

## 5. SBOM

`third_party/source-submodules.cdx.json` 是 AV-02 建立的源码级 CycloneDX 1.6 基线，记录两个只读 submodule 的版本、commit、许可证和安全公告入口。它不是最终安装包 SBOM：

- IM-02 必须加入 Python Worker、Python 包、系统库和实际分发文件；
- BM-12～BM-14 必须加入 Node/Bun 包、浏览器渲染依赖、134 项离线资源及 Overlay；
- VF-04 必须加入 FFmpeg/ffprobe 构建、编解码器和许可证；
- EB-05 必须加入 Chromium 发行物；
- CQ-05 才能以双平台正式包逐文件清单收口最终 SBOM。

SBOM 必须可复现，不写本机绝对路径、用户名、临时目录或构建密钥。组件从安装包删除时也必须从 SBOM 删除，不能只增不减。

## 6. 安全公告

每周一次且每个发布候选构建前检查源码锁中登记的 GitHub Security Advisories、上游 release notes，以及后续 Python/Node/Rust/Chromium/FFmpeg 依赖扫描结果。记录公告 ID、受影响范围、当前锁是否命中、产品可达性、严重级别、处理人、决定和证据。

- 可利用的严重/关键问题：立即关闭受影响功能或阻止发布，并用独立升级任务修复；没有已修复上游版本时保持功能关闭。
- 尚不可达或无可利用路径：必须写出基于当前打包与权限边界的理由、复查日期和触发条件，不能无期限忽略。
- 上游仓库删除、归档、所有权变化、tag 移动或许可证变化视为供应链事件，冻结升级并重新评审。
- 不把漏洞详情、私有补丁、用户素材、密钥或内部路径写入公开错误和普通日志。

## 7. 正常用户路径验收

本治理任务本身不新增用户功能，可由确定性源码锁、许可证、SBOM 和失败关闭检查完成。后续任何使用这些上游能力的功能，仍必须从正式 App 正常用户入口操作并核对真实输出或平台最终状态；submodule 能构建、上游 WebUI/CLI 能运行、Mock 或单元测试通过都不能让用户功能标为完成。
