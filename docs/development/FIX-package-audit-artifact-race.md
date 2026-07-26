# FIX 生产包审计与共享 `frontend/dist` 的产物竞态

> 状态：✅ 已完成（缺陷修复 + 防复发门禁；用真实 EB-16 发布产物双向实证）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：`docs/development/RELEASE-package-clean-rebuild.md` 第 195–216 行与
> `docs/development/FIX-material-worker-audio-assets.md` 第 274–312 行各记了一次同一个隐患，
> 两次都建议单独立项。本文件是那个立项。

## 缺陷

`frontend/scripts/audit-production-package.mjs` 判定的是**运行时刻磁盘上的 `frontend/dist`**，
不是**构建那一刻被编进二进制的那一份**。

`frontend/dist` 是整个 checkout 里所有构建共用的一个目录：`pnpm build` 以生产模式写它，
42 个 `pnpm build:tauri:*-test` 每一个都以 `desktop-e2e` / `control-plane-e2e` 模式重写它。
而一次发布运行从 `tauri build` 结束到最后一次审计收尾要几分钟。这几分钟里任何并发构建
都能替换掉审计的输入。

五个调用点全部把共享目录直接交给审计：

```text
scripts/run_e4_15_acceptance.py:158          --dist  frontend/dist
scripts/run_eb_16_acceptance.py:344          --dist  frontend/dist
scripts/run_eb_16_windows_acceptance.py:375  --dist  frontend/dist
scripts/run_p9_03_acceptance.py:338          --dist  frontend/dist
scripts/run_p9_04_acceptance.py:702          --dist  frontend/dist
```

（`audit-release-bundle.mjs` **没有**这个问题：它只接受 `--bundle-root`，读的是构建出来的
`.app` / Windows 安装根本身，不碰任何共享目录。本次核对过全部 6 个调用点，未改它一行。）

### 2026-07-26 当晚两次显形

| 时刻 | 现象 | 后果 |
| --- | --- | --- |
| 05:36 一次出厂 | 审计报 `Production build contains a desktop test marker` | **假阳性**：产物是干净的，被拒的是并发 E2E 刚写进 dist 的另一份构建 |
| 08:17–08:21 干净树重建 | 生产 dist 在 08:17:50 产出，08:21:31 被另一代理的测试构建覆盖；第二趟审计收尾于 08:21:18 | **通过，但余量只有 13 秒**。结论没被污染是运气 |

两种方向都成立：既能**假阳性**拒掉干净包，也能**假阴性**——审计一份别人的干净 dist，
而真正装进二进制的那份带着测试标记，却没有人看它。

## 判据错在哪里

审计的结论必须是**被审计那个产物的属性**。原来的判据里，产物（二进制）和证据（dist）
之间没有任何绑定关系，谁都能把证据换掉。共享目录当前状态一变，同一个产物的结论就变。

## 方案

两层，各管一件事。

### 一、把结论绑到产物上（单点，覆盖全部 5 个调用点）

Vite 把每一份内容哈希产物写在 `assets/` 下；Tauri 把每一份内嵌资产的 **key 以明文字符串**
存在二进制里，紧挨着该资产自己的压缩负载。所以**内容不可从二进制恢复，但 key 可以**，
而两个源码不同的构建绝不会在这组 key 上一致。

于是 `auditProductionPackage` 新增一步：被审计的 dist 必须证明自己就是这个二进制的产物。

```text
正向：dist 里每一个 assets/<file>，其 key `/assets/<file>` 必须在二进制字节中出现
反向：二进制中 `/assets/` 的出现次数必须等于 dist 提供的哈希资产数
下界：dist 一个哈希资产都不提供，直接拒绝
```

**只做成员判定，不从二进制里解析 key**。key 后面紧跟压缩负载，解析出来的字符串会撞上
后一个字节（实测 Python 门禁的 fixture 上就撞了：`/assets/index-RELEASE.js` 被读成
`/assets/index-RELEASE.jsZGVtby1...`）。问"这个已知 key 在不在"永远不会错；
出现次数计数同样不受相邻字节影响。

顺序也是判据的一部分：

```text
依赖树 → Cargo 清单 → Tauri 配置 → 二进制标记/公钥 → 【dist 归属】 → dist 内容与路径
```

二进制先按自己的字节独立判定，所以真正有问题的产物保留原来的精确诊断；
二进制过关之后，dist 必须先证明自己属于它，才轮得到对它的内容下任何结论。
这正是把"审计说的话只关于被审计的产物"变成结构性质而不是运气。

### 二、把共享目录移出审计输入（`scripts/production_assets.py`）

`snapshot_production_assets()` 在 `tauri build` 返回的**下一行**把 dist 冻结进本次运行
自己的目录，审计读冻结副本。窗口从"几分钟"缩到"一次目录拷贝"。

`require_frozen_distribution()` 给 EB-16 的 `--skip-build` 用：重新审计一个已构建的包时
必须复用当初那份冻结副本，找不到就失败，**不允许回退到共享目录**——回退就等于把本缺陷装回去。
所以 macOS / Windows 两个 EB-16 的快照放在 `build/audited-distribution`（随工作目录留存），
E4-15 / P9-03 / P9-04 放在各自的临时目录（一次性）。

**第二层不是证明，只是把窗口收窄**——快照本身仍取自共享状态。第一层才是让残余竞态
**fail closed** 而不是产出一个关于别人构建的结论。两层的分工写在模块 docstring 里。

同时把 5 份重复的 `PRODUCTION_ASSETS = FRONTEND_ROOT / "dist"` 收敛到这一个模块。

## RED

```text
$ cd frontend && node --test tests/production-package-audit.test.mjs

✖ E4-15 refuses a distribution the audited binary never embedded
  AssertionError: The input did not match the regular expression
  /Production distribution does not belong to the audited binary/. Input:
  'Error: Production build contains the UI Harness entry'
      at assertProductionBoundaries (check-production-boundaries.mjs:30:11)
      at async assertNoTestAssets (audit-production-package.mjs:158:3)

✖ E4-15 refuses a distribution that drops an asset the audited binary embedded
  AssertionError: Missing expected rejection.

✖ every package audit runner freezes the distribution it audits instead of reading the shared one
  AssertionError: run_e4_15_acceptance.py must audit a frozen copy of the distribution
  the build consumed

ℹ pass 8
ℹ fail 3
```

第一条的失败输出就是缺陷本身：审计对着一份**外来构建**得出了 `UI Harness entry` 这个结论，
而被审计的二进制干干净净。

## GREEN

```text
$ cd frontend && node --test tests/production-package-audit.test.mjs
ℹ pass 11
ℹ fail 0

$ backend/.venv/bin/python scripts/test_embedded_browser_package.py
Ran 35 tests in 0.676s
OK

$ backend/.venv/bin/python -m ruff check scripts/production_assets.py scripts/run_e4_15_acceptance.py \
    scripts/run_p9_03_acceptance.py scripts/run_p9_04_acceptance.py \
    scripts/run_eb_16_acceptance.py scripts/run_eb_16_windows_acceptance.py --select F,E9
All checks passed!
```

## 真实产物验收（不是 fixture）

被审计对象是 `RELEASE-package-clean-rebuild.md` 出厂的那一个真实发布产物，只读：

```text
.local/eb-16/clean/cargo-target/release/bundle/macos/自动化运营工具.app
  /Contents/MacOS/automation-tool-desktop      21,307,552 B   2026-07-26 08:18
```

验收时机是**竞态正在发生的当下**：另一代理 09:38 跑 `pnpm build:tauri:task-run-test`，
把共享 `frontend/dist` 换成了 `control-plane-e2e` 构建。两边实际持有：

```text
binary embeds : /assets/index-Bow_WG7i.js   /assets/index-CSregXsR.css
dist provides : /assets/core-0_kFspMt.js    /assets/event-Cq8FfK9q.js
                /assets/index-BmVN2yc3.js   /assets/index-CSregXsR.css
```

### 1. 同一份真实二进制，对着被并发构建换掉的共享 dist

修复前的判据（`check-production-boundaries.mjs` 一字未改，正是当晚报错的那一条）：

```text
$ node scripts/check-production-boundaries.mjs
Error: Production build contains a desktop test marker
```

——对一个干净发布包的**假陈述**。修复后，同样的二进制、同样的 dist，走正式命令行入口：

```text
$ node scripts/audit-production-package.mjs \
    --binary .../自动化运营工具.app/Contents/MacOS/automation-tool-desktop \
    --cargo-manifest src-tauri/Cargo.toml \
    --tauri-config .local/eb-16/clean/build/tauri.eb-16.effective.json \
    --dist dist

Error: Production distribution does not belong to the audited binary:
       4 hashed asset(s) offered, 3 of them not embedded, 2 embedded by the binary
```

——真陈述，并且直接点到真正的问题。

### 2. 同一份真实二进制，对着它自己的生产 dist

生产资产在私有 `--outDir` 重建（**不碰共享 dist**，因此没有干扰另一代理正在跑的 wdio）：

```text
$ pnpm exec vite build --outDir dist-audit-proof --emptyOutDir
dist-audit-proof/assets/index-Bow_WG7i.js   1,397.91 kB
```

`index-Bow_WG7i.js` / 1,397,917 B 与 `RELEASE-package-clean-rebuild.md` 记录的
08:17:50 生产构建**逐字节一致**，即这确实是那个二进制当初吃进去的那一份。

```text
$ node scripts/audit-production-package.mjs ... --dist dist-audit-proof
[E4-15] Production desktop package audit passed
EXIT=0
```

**这两条一起才是本次要的结论**：共享目录全程被另一份构建占着，审计对同一个产物
给出的两个判定只取决于交给它的是不是这个产物自己的 dist。

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 审计期间共享 dist 被换成带测试标记的构建 | 拒绝，且诊断为"不属于本二进制"，不是关于外来构建的内容结论 | 真实产物实跑 + `refuses a distribution the audited binary never embedded` |
| 交给审计的是本产物自己的 dist，而共享目录同时是别人的 | 通过 | 真实产物实跑 + `keeps its verdict on the audited artifact while the shared build directory is overwritten` |
| dist 少了二进制确实内嵌的资产（截断/清空） | 拒绝 | `refuses a distribution that drops an asset the audited binary embedded` |
| dist 一个哈希资产都不提供（审了个无关目录） | 拒绝（下界判据） | `provided.size === 0` |
| 二进制内嵌了 dist 没有交代的资产 | 拒绝（出现次数不等） | 计数方向 |
| key 后紧跟的压缩字节导致解析越界 | 不解析，只做成员判定与计数 | Python 门禁 fixture 实测过这个越界 |
| `--skip-build` 重新审计已构建的包 | 必须复用当初的冻结副本，缺失即失败，不回退共享目录 | `require_frozen_distribution` |
| 构建产物真的带测试标记 / 缺发布公钥 | 仍先由二进制判据给出原来的精确诊断 | 判定顺序；原有 6 个用例未改一行 |
| 新增或改写发布 runner 又去读共享 dist | 门禁点名该 runner | `every package audit runner freezes the distribution it audits` |

## 真实边界

1. **绑定锚点是 `assets/` 下的哈希文件名**，不是逐字节内容。Tauri 把资产内容压缩后内嵌，
   从二进制恢复内容需要复刻 Tauri 内部的内嵌格式，属于依赖第三方内部实现，未做。
   因此一个"资产引用完全相同、只有 `index.html` 被换掉"的 dist 不会被归属判据拦下——
   它仍由原有的内容标记判据负责。实际发生的竞态形态（另一模式的构建）必然改哈希名，已覆盖。
2. **`/assets/` 计数依赖该串在发布二进制里不作他用**。在真实 21 MB 发布二进制上实测
   恰好 2 次、全部是真资产 key。若将来第三方引入同名串，判据会**误拒**而不是误放，
   方向是安全的，但会拦住发布，届时需要换更强的锚点。
3. **快照仍取自共享目录**，只是把窗口压到一次拷贝。它不是证明，证明是第一层。
4. **Windows 侧未实机执行。** `run_eb_16_windows_acceptance.py` 与 `run_p9_04_acceptance.py`
   的接线与 macOS 同形，本机只做了语法与导入校验，未在 Windows 上跑过。
5. **未实跑 E4-15 / P9-03 / EB-16 三个 runner 的完整流程**。本次验收用的是这些 runner 调用的
   同一个正式审计命令行加真实发布产物，runner 侧改动由静态门禁 + 导入校验覆盖；
   跑一次完整 EB-16 会重写共享 `frontend/dist`，与另一代理正在进行的 E2E 冲突，故未跑。
   下一次正式出厂即是这条链路的真实验收。

## 清理

- `frontend/dist-audit-proof/` 验证后立即删除；
- **共享 `frontend/dist` 全程未被本次改动写过**——正向验证走私有 `--outDir`，
  正是为了不污染另一代理正在跑的 wdio；
- `.local/eb-16/` 只读，未写入；
- `~/Library/Application Support/com.aventador.automationtool/` 未触碰
  （前后 675 条目 / inode 8941483 / 抖音档案 `df1c89f0-…` 一致）；
- 未启动任何服务、容器或浏览器。

## 文档

- `frontend/scripts/audit-production-package.mjs`（归属判据 + 判定顺序）
- `frontend/tests/production-package-audit.test.mjs`（4 项新用例，fixture 二进制改为携带资产 key）
- `scripts/production_assets.py`（新增）
- `scripts/run_e4_15_acceptance.py` / `run_p9_03_acceptance.py` / `run_p9_04_acceptance.py` /
  `run_eb_16_acceptance.py` / `run_eb_16_windows_acceptance.py`（接线 + 去掉 5 份重复常量）
- `scripts/test_embedded_browser_package.py`（发布 fixture 二进制补上资产 key）
- 本文件
