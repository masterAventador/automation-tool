# FIX：第三方软件声明页（法务页），CQ-02 白名单不再空转

> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 类型：交付补齐（闭合 CQ-02 遗留项「法务页本身不存在，白名单当前无对象」）

## 缺陷

`contracts/quality/user-facing-terminology.v1.json` 的 `allowedLegalDisclosurePaths` 早就声明了
`frontend/src/features/legal/third-party-software/**` 是唯一允许出现上游项目名的路径，
但**该页面一直不存在**，白名单是空转的。后果有两层：

1. 用户在产品里看不到任何第三方软件声明，许可证义务没有履行入口；
2. 品牌扫描的白名单从未被真实验证过——它可能写错了路径而没人发现。

## 交付

页面按项目现有的 `activePage` 导航机制接入左侧导航「第三方软件声明」，三个区域：
上游开源项目、字体与素材权利、以及"为什么这些名称只出现在本页"的说明。

**事实源单点**：版本、commit、许可证、各类计数全部来自
`third-party-sources.v1.json` / `asset-rights-policy.v1.json` / `motion-catalog-rights.v1.json`，
代码里没有第二份；项目名与 `owner/repo` 由锁定 URL 推导而非手抄。

## 投影：为什么不直接 import 源契约

第一版直接静态 import 三份源契约，页面能跑、门禁全绿，但**87 KB 的内部评审明细被打进了
用户端产物**——134 条动效零件的逐条评审结论，含商标指示词（apple / tiktok / vscode）、
示例素材路径、CDN 地址——而页面只用到其中约 10 个数字。

不是洁癖问题：项目里**已经有**这个问题的既定解法。`contracts/video/motion-catalog-ui.v1.json`
就是投影契约，由 `scripts/build_motion_catalog_ui_projection.py` 从源契约生成。
另写一套或者用 `React.lazy` 拆包都不对——后者只是把同样的数据挪到另一个 chunk，照样发给用户。

所以照既有模式补齐：

| 文件 | 作用 |
| --- | --- |
| `contracts/quality/third-party-notice-ui.v1.json` | 投影，2527 字节，只含页面真正渲染的字段 |
| `scripts/build_third_party_notice_ui_projection.py` | 生成器，确定性输出 |
| `scripts/check_third_party_notice_ui_projection.py` | 门禁：漂移检测 + 泄漏扫描 + 体积上限 + 前端 import 守卫 |
| `scripts/test_third_party_notice_ui_projection.py` | 契约测试，含 10 项篡改矩阵 |

投影模式唯一的风险是"源契约改了、投影没重新生成"，门禁那条漂移检测就是堵这个的。

## RED

Python 侧第一轮（脚本不存在）：

```text
AssertionError: scripts/build_third_party_notice_ui_projection.py is missing
```

Python 侧第二轮（import 守卫真的在工作——投影建好了，前端还在读源契约）：

```text
third-party notice ui projection check failed: frontend/src/features/legal/third-party-software/
third-party-software-notice.test.ts imports contracts/quality/third-party-sources.v1.json
instead of the projection
```

TypeScript 侧：

```text
Test Files 3 failed (3)   Tests 2 failed | 5 passed (7)
Error: Failed to resolve import "./third-party-software-notice"
TestingLibraryElementError: Unable to find an accessible element with the role "menuitem"
  and name "第三方软件声明"
```

Playwright 侧也真实红过一次：`strict mode violation: getByText("MoneyPrinterTurbo") resolved
to 2 elements`（同时命中标题和仓库行），改用 `getByRole("heading")` 后转绿。

## GREEN（主会话独立复跑，不是只看子代理报告）

```text
python3 scripts/check_third_party_notice_ui_projection.py   退出码 0
python3 scripts/test_third_party_notice_ui_projection.py    tests passed
python3 scripts/check_user_facing_branding.py               passed (50 frontend, 247 native files)
python3 scripts/check_third_party_sources.py                valid
cd frontend && npx tsc -b                                   退出码 0
cd frontend && npx eslint .                                 退出码 0
cd frontend && npx vitest run                               56 files / 386 tests passed
cd frontend && npx playwright test                          15 passed
```

品牌扫描仍是 **50 frontend** 个文件，与新增法务页之前一致——**说明这一页确实是被
`allowedLegalDisclosurePaths` 跳过的，不是靠放宽词表通过的**。词表和白名单一个字没动。

## 漂移检测的有效性验证

不是只篡改候选文件。主会话实测：真改源契约再还原。

```text
把 third-party-sources.v1.json 的 tag 改成 "<原值>-drifted"，不重新生成投影
  → third-party notice ui projection check failed:
    candidate projection drifted from the locked source, asset rights and motion rights contracts
  退出码 1
还原后 → check passed，退出码 0
```

## 产物实测

| | 产物体积 | gzip |
| --- | ---: | ---: |
| 改造前（直接 import 源契约） | 1,410.74 kB | 408.48 kB |
| 改造后（读投影） | 1,327.41 kB | 401.05 kB |
| 减少 | **-83.33 kB** | -7.43 kB |

`grep` 构建产物核对（主会话独立执行）：

| 内部评审数据 | 出现次数 |
| --- | ---: |
| `pending_bm12_localization` | 0 |
| `trademarkIndicators` | 0 |
| `cdn.jsdelivr.net` | 0 |
| `fonts.googleapis.com` | 0 |
| `needs_localization` | 0 |

| 该留的法务事实 | 出现次数 |
| --- | ---: |
| `MoneyPrinterTurbo` | 3 |
| `hyperframes` | 5 |
| `Apache-2.0` | 3 |

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 源契约改了、投影没重新生成 | 门禁变红 | 已实测注入 |
| 生成器开始吐内部评审字段 | 泄漏扫描变红 | 直测（临时改成 `return` 证明会红后还原） |
| 前端直接 import 源契约 | import 守卫变红 | 已实测（RED 第二轮） |
| 投影超过 8 KB | 拒绝 | 门禁 |
| 契约缺版本 / 缺许可证 / 无中文说明 | 构建期抛错，宁可页面崩也不显示残缺的法务声明 | 单测 |
| 权利策略默认从 deny 改成 allow | 拒绝 | 单测 |
| 上游名出现在法务页以外的任何页面 | 品牌扫描 + 运行时可访问性树扫描变红 | CQ-02 既有 |

## 真实边界

1. **没有做正式 Tauri App 的用户路径验收**。UI Harness + Playwright 只能证明 React 业务交互，
   按项目规则不能替代正式 App 验收。要真正闭环还需要一次桌面 E2E。
2. **中文文案没进投影**。既有的 `motion-catalog-ui.v1.json` 把中文标签放进了投影，
   这里没有：投影只装事实（名称、版本、许可证、计数），中文说明留在 TypeScript 按 id 挂接。
   好处是"新增上游项目却没补中文说明"会在构建期报错。这是与既有投影不一致的地方，
   是有意的取舍，不是疏漏。
3. **`asset-rights-policy.v1.json` 的 `entries` 是空的**，页面如实写"登记册是空的，本产品
   尚未随安装包分发任何第三方素材"，没有编造已清权的素材清单。
4. **`requiredCategories` 的字段名是英文**（`embeddingAllowed` 等），页面只显示条数不逐条列出，
   避免未解释英文术语进用户界面。法务若要求逐字段公示，需先在契约里补中文字段名。

## 清理

Playwright 无头运行，进程由其自身回收；`dist/` 是构建产物且已被 gitignore；
端口 1420 已释放；注入验证改动的源契约已按备份还原并复跑确认干净。

## 文档

- `contracts/quality/third-party-notice-ui.v1.json`（新增）
- `scripts/build_third_party_notice_ui_projection.py`（新增）
- `scripts/check_third_party_notice_ui_projection.py`（新增）
- `scripts/test_third_party_notice_ui_projection.py`（新增）
- `frontend/src/features/legal/third-party-software/`（新增，4 个文件）
- `frontend/src/app/WorkbenchShell.tsx` 与其测试（导航接入）
- `frontend/e2e/third-party-software-notice.spec.ts`（新增）
- `frontend/e2e/upstream-name-leak.spec.ts`（加注释说明法务页是登记在案的例外）
- `docs/development/CQ-02.md`（遗留项「法务页本身」转为已闭合）
- 本文件

## 遗留项

| 项 | 状态 | 归属 |
| --- | --- | --- |
| 正式 Tauri App 用户路径验收 | 未做 | 随 EB-17 / CQ-04 的真实环境 |
| Windows 侧同一批文案的显示核对 | 未做 | Windows 验收队列 |
| 词表随新依赖同步的强制机制 | 未做 | CQ-05 |
