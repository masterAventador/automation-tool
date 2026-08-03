# Windows 本机交互式会话待补验收（给在 Windows 上执行的 agent）

> 日期：2026-07-26
>
> 读者：在 Windows 本机运行的 Claude Code。
>
> 前提：**你必须运行在 Windows 桌面的交互式会话里**，不是 `ssh winbox`。
> 如果你是通过 SSH 进来的，下面三项都会失败，且失败原因与产品无关——先读第 0 节。

## 0. 先确认你不是从 SSH 进来的

那台机器的 sshd 以 SYSTEM 运行，SSH 会话拿到**已提权的完整管理员令牌**。该会话新建的
任何目录，属主是 `BUILTIN\Administrators` 而不是登录用户。

产品的运营 Profile 校验要求目录属主 == 当前用户：`browser_profiles_windows.rs` 的
`verify_private_acl_parts` 比对属主，而 `apply_private_acl_with_flags` **只设 DACL、
从不设 owner**。于是提权会话建的目录必然校验失败，App 在
`lib.rs` 的 `BrowserProfileStore::initialize(&app_data_directory)` 处 panic。

**这是环境限制，不是产品缺陷。不要去改属主校验来"修"它。**

自检：

```powershell
whoami /groups | Select-String "S-1-16-12288"   # 有输出 = 高完整性级别 = 提权
```

若确认提权且无法换到非提权会话，见第 4 节。

## 开跑前

```powershell
cd F:\automation-tool
git pull
git log --oneline -1     # 至少到 dd578dd
```

---

## 1. CQ-01 普通用户可理解性（桌面 E2E）

**入口**

```powershell
cd F:\automation-tool
py -3.12 scripts\run_cq_01_acceptance.py
```

需要 Python 3.10+。脚本自建隐藏测试 App、按左侧菜单真实路径逐页走，`finally` 里恢复
生产 Vite 资产并删除隐藏 App 私有数据目录。

**判据**：退出码 0。

**此前卡在哪**：SSH 会话里卡在 App 启动，就是第 0 节那条属主校验。前置依赖（vitest 回归、
Tauri 测试 App 构建）已在 SSH 会话里跑通过，包括一个已解决的坑——pnpm 默认用 junction
组织 `node_modules`，该机判为 untrusted mount point 导致模块不可达，改 hoisted 布局后正常。

**Windows 特有、macOS 上验不到的两点**，跑完要单独确认：
- antd 中文 locale 是否生效；
- 系统中文字体回退后，卡片必答项与概念区分文案有没有被截断或换行吞掉。
  这一条脚本断言不了（它比对文本内容，不比对渲染宽高），需要你截图看。

**另有一批需要真实 Control Plane/PostgreSQL 的用例**（`control-plane-recovery`、
`network-recovery`、`task-restart`、`app-crash-recovery`、`workbench-control`、
`workbench-metrics`、`executor-crash-recovery`、`task-run`、`model-service`），
因为五处改名（控制服务、本机执行器、本机安装授权、剩余用量、客户演示版）受影响。
若本机能起 Docker + PostgreSQL 就一并跑，起不来就如实记为未跑。

---

## 2. EB-15 有头运营窗口与人工接管

**入口**

```powershell
cd F:\automation-tool\backend
$env:AUTOMATION_TOOL_EB15_HEADED = "1"
uv run pytest tests\integration\test_embedded_browser_lifecycle.py
Remove-Item Env:\AUTOMATION_TOOL_EB15_HEADED
```

**判据**：`5 passed`。不设该环境变量时是 `4 passed, 1 skipped`——被 skip 的正是有头这一项，
所以**看到 skipped 就说明环境变量没生效，不算通过**。

**会弹出真实浏览器窗口**，这是该用例的验收对象本身。不要关它，让用例自己走完。

**已在 SSH 会话通过的四项**（进程树完全拆除、Profile 解锁后同一 Profile 干净重启、
外部强杀后 fail-closed 并可恢复、诊断有界且不泄漏路径）不需要重跑，但重跑也无害。

前置改造已在 `bdc9715` 完成：`pgrep -f` 与 `SIGKILL` 在 Windows 不存在，已下沉到
`backend/tests/integration/conftest.py` 的 `process_ids_matching()` 与 `terminate_process()`，
POSIX 走 pgrep/SIGKILL，Windows 走 CIM 进程表与 `taskkill /F`。

---

## 3. BM-07 原生文件选择器上传品牌字体与 Logo（✅ 2026-08-02 已完成）

已在 Windows 可见桌面会话中安装无 WebDriver/test feature 的生产 NSIS 包，并从正常
“创作 → 品牌动效成片 → 完整制作面板”入口完成：

- 两次点击生产文件输入，真实操作 Windows 原生文件对话框，选择普通绝对 NTFS 路径下的
  `AcmeSans-Regular.woff2` 与 `bm07-logo.png`，没有构造 `File` 或调用上传注入。
- 预览实际加载 `Acme Sans` 和 128×128 PNG；相同草稿两次提交获得真实 RenderJob
  `2e1f1dc9-b478-4c89-8172-9bc6d6ecb2e8`、`33001ff2-845d-481a-89f1-ba078c88832b`。
- 两个工作区的字体、Logo、frame、style-freeze、composition、脚本和分镜摘要逐项一致；
  两个 H.264 1920×1080 / 30 fps / 90 帧 / 3 秒 MP4 的 SHA-256 均为
  `DA03B11237F3BEEA21C1F688F5ECF782F66DFCC2F87EB7D800CB6F6D7C94E54E`。
- App 内播放到结束并抽看首、中、尾三段画面后，从 App 删除成片；隔离 App、端口、进程、
  数据库、计划任务、安装目录和 AppData 均已清理。

正式包样本使用 WOFF2 + PNG；其余支持格式由扩展名、魔数、大小和协议测试覆盖。生产链路只把
文件名和读取后的字节传给 Rust，不传源路径，因此旧清单中的 reparse point、大小写和 8.3
路径字符串矩阵不再作为 BM-07 功能完成条件，也不声称已做过这些真人路径操作。完整终态证据、
冻结摘要及包体积边界见 `docs/development/BM-07.md`。

---

## 4. 如果只能拿到提权会话

有一条没验证过的路子：Windows 任务计划可以用**非提权令牌**启动进程。

```powershell
schtasks /Create /TN at-cq01 /TR "<命令>" /SC ONCE /ST <时间> /RL LIMITED /F
schtasks /Run /TN at-cq01
```

它不需要改 `sshd_config`、防火墙或 sing-box 代理配置——**那三样一律不许动，代理是用户的
对话通道**。

对第 1 节可能有用（它只是被属主校验挡住，不需要图形界面）。**对第 2、3 节没用**，
那两项需要真实可见窗口和真实系统对话框。

未验证的点：非提权任务计划下 WebView2 与 msedgedriver 能否正常挂载。

---

## 5. 跑完怎么记

写进对应证据文件，不要只留在终端输出里：

| 项 | 文件 | 要写的 |
| --- | --- | --- |
| CQ-01 | `docs/development/CQ-01.md` | 日期、命令、**终态输出原文**、退出码；字体回退那条附截图结论 |
| EB-15 有头 | `docs/development/EB-15.md` | 同上；明确写 `5 passed` 还是 `4 passed, 1 skipped` |
| BM-07 | `docs/development/BM-07.md` | ✅ 已完成；保留正式包、原生文件框、RenderJob、冻结摘要和成片终态证据 |

规则：**失败记报错原文，不要转述**。跑不了的记"未跑"和原因，不要含糊成"待补"。

改完跑一遍门禁再提交：

```powershell
python scripts\check_embedded_browser_video_roadmap.py
```

三项都通过后，对应任务的状态由你按 `docs/embedded-browser-video-studio-roadmap.md`
的状态定义判断是否可以从 `🔍 待验收` 改成 `✅ 已完成`——注意还要看它们各自遗留项里
有没有别的未闭合条目，不是这一项过了就等于完成。
