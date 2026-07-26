# C6：Backend 失败矩阵静态审计

- 日期：2026-07-26
- 基线：`036c267`
- 状态：✅ 清单完成，未修改生产代码
- 范围：`backend/` 中跨进程、网络、文件系统和外部副作用能力
- 方法：逐项对照 `CLAUDE.md` §9，先定位生产边界，再检查对应单元/集成测试，最后用针对性 `rg` 搜索确认缺口不是已有测试的同义覆盖

## 结论

本轮找到 7 个有具体生产边界和零命中证据的缺口。以下按真实用户影响排序；“缺覆盖”不等于本轮已复现生产事故，也不等于建议立刻同时修复。主线应先处理前三项，再决定其余项的排期。

## P0：可能让用户上传或保存的内容与已确认内容不一致

### 1. B 站视频在摘要复验后仍可被同名替换

**用户影响：** 用户确认的是文件 A，但在摘要复验完成后、分片读取期间，同一路径可被替换成同尺寸文件 B。上传记录仍保存 A 的摘要，平台实际收到的却可能是 B；分片上传时还可能得到 A/B 混合内容。

**生产证据：**

- `backend/src/automation_tool/control_plane/infrastructure/bilibili/material.py:28-43` 只在 reader 构造时拒绝 symlink；之后仅保存路径。
- `backend/src/automation_tool/control_plane/infrastructure/bilibili/material.py:46-63`、`:76-105` 的摘要、状态和分段读取会分别重新按路径打开文件，没有固定 inode/句柄或比较文件 identity。
- `backend/src/automation_tool/control_plane/application/bilibili_archive_publishing.py:724-741` 在上传前复验一次摘要；`:753-783` 随后按路径重新读每一段，上传结束后不再复验完整摘要。
- `backend/tests/unit/control_plane/test_bilibili_archive_publishing.py:594-600` 已覆盖“上传开始前 reader 内容已经不同”，但没有覆盖“`_verify_reader_matches` 返回后再替换真实文件”。

**零命中证据：**

```text
rg -n "replace|after.*(sha|digest|validat)|during.*upload|same.size|TOCTOU|symlink.*after|rename" \
  backend/tests/unit/control_plane/test_bilibili_publish_material.py \
  backend/tests/integration/test_bilibili_publish_vertical.py
# exit 1，零命中
```

**建议补测：** 用真实临时文件分别在摘要复验后、第一分片后做同尺寸原子替换；要求上传在任何网络副作用继续前固定失败，且不能把任务记为 `VIDEO_UPLOADED`。

### 2. 阿里云剪辑产物同 ID 并发落盘可覆盖

**用户影响：** 两个恢复/导入协程同时持久化同一个 Artifact ID 时，都可能通过“目标不存在”检查；后完成者使用 `os.replace` 覆盖先完成者，导致数据库谱系指向内容不确定的成片。

**生产证据：**

- `backend/src/automation_tool/control_plane/domain/aliyun_ims_editing_output.py:213-232` 在 `final_path.exists()` 与 `os.replace()` 之间存在竞态。
- 两次写入还共用 `:219` 的同一个临时文件名，既没有排他创建，也没有每次调用的随机临时名。
- `backend/tests/unit/control_plane/domain/test_aliyun_ims_editing_output.py:338-356` 只验证顺序重复：第一次已经完成后，第二次才开始。

**零命中证据：**

```text
rg -n "concurr|race|simult|parallel|overwrite|replace.*existing|same.*artifact|duplicate.*concurr" \
  backend/tests/unit/control_plane/domain/test_aliyun_ims_editing_output.py
# exit 1，零命中
```

**建议补测：** 用两个受控 AsyncIterator 把两次 `persist()` 卡在 `exists()` 之后交错执行，断言只有一个调用成功，先成功的内容永不被覆盖，失败者不删除胜者的临时/最终文件。

## P1：可能让桌面能力无响应、留下残片或泄露本机信息

### 3. 品牌动效编排写盘失败会逃出封闭错误边界

**用户影响：** 磁盘满、权限变化或中途写失败时，子进程可能只在 stderr 输出 traceback，App 收不到约定的拒绝 JSON；原始 `OSError` 还可能携带用户工作区绝对路径。已写了一半的 `DESIGN.json`、`STORYBOARD.json`、HTML 或 `renderjob.json` 也没有清理约束。

**生产证据：**

- `backend/src/automation_tool/executor/motion_authoring/agent.py:377-383` 直接 `Path.write_text()`，没有原子临时文件、`fsync`、失败清理或固定错误转换。
- `backend/src/automation_tool/executor/motion_authoring/agent.py:1481-1518` 一次任务连续写多个文件，任一中途失败都会留下前序文件。
- `backend/src/automation_tool/executor/motion_authoring/entry.py:385-400` 只把 `MotionAuthoringUnavailable` / `MotionAuthoringRejected` 转为封闭拒绝。
- `backend/src/automation_tool/executor/motion_authoring/entry.py:421-444` 的进程序列化边界也不接住 `OSError`。

**零命中证据：**

```text
rg -n "disk|permission|partial|atomic|fsync|write.*fail|path.*leak|OSError.*write" \
  scripts/test_motion_authoring_agent.py \
  backend/tests/unit/executor/test_motion_authoring_entry.py
# exit 1，零命中
```

**建议补测：** 分别在首个 JSON、修复轮次 HTML、最终 `renderjob.json` 注入短写/磁盘满/权限拒绝；断言 stdout 仍是封闭拒绝文档、stdout/stderr 不含路径或模型密钥，并明确任务级残片清理策略。

### 4. 本地注册交接目录未防 symlink/identity 替换

**用户影响：** AppData 目录被链接或在检查后替换时，短期注册 grant 可能写入非预期目录；本机首启可能泄露注册凭据或错误地修改目标目录权限。

**生产证据：**

- `backend/src/automation_tool/control_plane/bootstrap/local_provisioning.py:91-110` 对目录执行 `mkdir(..., exist_ok=True)` 和 `chmod`，但没有 `lstat`、symlink/reparse point 拒绝、owner/ACL 校验或目录 identity 复验。
- 同一段代码按完整路径 `os.open` 临时文件和 `os.replace` 最终文件，没有 `dir_fd`/`O_NOFOLLOW` 约束。
- `backend/tests/unit/control_plane/test_local_provisioning.py:103-110` 只验证正常目录的 POSIX mode；`:126-135` 只验证“路径已是普通文件”的失败。

**零命中证据：**

```text
rg -n "symlink|link.*directory|directory replacement|path replacement" \
  backend/tests/unit/control_plane/test_local_provisioning.py \
  backend/tests/integration/test_local_provisioning_registration.py
# exit 1，零命中
```

**建议补测：** 覆盖 AppData 根 symlink、父目录 symlink、创建后替换目录、Windows reparse point；要求固定失败且不写出 grant、不修改链接目标权限。

### 5. 本地注册交接文件没有并发和崩溃耐久性覆盖

**用户影响：** 同一进程并发发起两次 provision 会共用 PID 临时名；上次崩溃遗留该临时文件时，后续每次首启都可能被 `O_EXCL` 永久挡住。成功 `replace` 后也未同步目录项，断电/系统崩溃后的 handoff 是否存在没有保证。

**生产证据：**

- `backend/src/automation_tool/control_plane/bootstrap/local_provisioning.py:92` 的临时名只含 PID，没有调用级唯一值。
- `:97-110` 使用 `O_EXCL`，但进入前不识别/清理同进程遗留临时文件。
- `:103-110` 只同步文件内容，`os.replace` 后没有目录 `fsync`。
- `backend/tests/unit/control_plane/test_local_provisioning.py:113-123` 只验证两次顺序成功调用，不模拟并发、遗留临时文件或替换后崩溃。

**零命中证据：**

```text
rg -n "concurrent provision|preexisting temporary|stale temporary|same PID|fsync.*directory|power loss|write crash" \
  backend/tests/unit/control_plane/test_local_provisioning.py \
  backend/tests/integration/test_local_provisioning_registration.py
# exit 1，零命中
```

**建议补测：** 同进程双调用交错、预置同 PID `.tmp`、`replace` 前后崩溃与目录同步失败；明确“一个赢家/另一个确定失败”及重启自愈语义。

## P2：出包失败时可能留下难诊断或不可复用的状态

### 6. Executor manifest 与签名的双文件写入不是事务

**用户影响：** manifest 写成功、签名写失败时，候选目录保留一份无配套签名的可信清单；重试或后续审计看到的是半成品，实际错误会延迟到装配/启动阶段。

**生产证据：**

- `backend/src/automation_tool/executor/package_manifest.py:228-236` 顺序 `write_bytes()` 两个最终文件，没有临时文件、原子发布、失败清理或目录同步。
- `backend/tests/unit/executor/test_package_manifest.py:346-363` 将所有 `Path.write_bytes` 都改成首次即失败，只证明固定错误映射，没有让第二次写入单独失败，也没有检查目录残留。

**零命中证据：**

```text
rg -n "second.*write|signature.*write.*fail|partial.*manifest|manifest.*without.*signature|atomic|fsync|stale.*manifest|cleanup.*manifest" \
  backend/tests/unit/executor/test_package_manifest.py
# exit 1，零命中
```

**建议补测：** 让 manifest 成功、signature 失败，并模拟目录同步失败；要求失败后两个最终文件要么都是旧的一致版本，要么都不存在。

### 7. PyInstaller 超时只存在实现，没有失败矩阵测试

**用户影响：** macOS/Windows 出包时 PyInstaller 挂起超过 600 秒，应固定失败并清理 scratch；当前行为依赖 `subprocess.TimeoutExpired` 恰好被上层宽泛捕获，后续重构可能无声破坏。

**生产证据：**

- `backend/src/automation_tool/executor/macos_candidate.py:307-337`、`backend/src/automation_tool/executor/windows_candidate.py:214-244` 为 PyInstaller 设置 600 秒超时。
- `backend/src/automation_tool/executor/macos_candidate.py:340-395`、`backend/src/automation_tool/executor/windows_candidate.py:247-300` 依赖 `subprocess.SubprocessError` 归一化和临时目录上下文清理。
- 现有候选测试只覆盖非零退出及输出有/无内容，没有构造 `TimeoutExpired`。

**零命中证据：**

```text
rg -n "TimeoutExpired|timeout|hang|hung" \
  backend/tests/unit/executor/test_macos_candidate.py \
  backend/tests/unit/executor/test_windows_candidate.py
# exit 1，零命中
```

**建议补测：** 两个平台分别让 `subprocess.run` 抛 `TimeoutExpired`，断言固定脱敏错误、输出目录不存在、临时目录退出后无残留；macOS 的签名/验签 30 秒超时也应纳入同一矩阵。

## 已核对但本轮不列缺口

以下区域已有与 §9 对应的具体覆盖，因此没有为了凑数量重复登记：

- `backend/tests/unit/executor/test_local_artifact.py` 已覆盖配额/空间压力、写入与同步失败无残片、权限、symlink、目录和文件 identity 替换、短读/增读、unlink 竞态。
- `backend/tests/unit/control_plane/test_runtime_secrets.py` 已覆盖固定目录/文件 symlink、权限与 owner、超长/坏编码/换行、读错误和读取中增长。
- B 站发布/对账纵向测试已覆盖网络断开、超时、断点续传、创建结果不确定且禁止重发；第 1 项只登记其尚未覆盖的“摘要复验之后的真实文件替换”。
- 阿里云剪辑输出测试已覆盖顺序重复、流中断无残片、超限、清理失败；第 2 项只登记多协程同 Artifact ID 的竞争。

## 验证说明

本任务按交接要求只做静态审计，没有改生产代码，也没有运行会产生外部副作用的真实验收。上述 `rg` 均在冻结基线 `036c267` 的 `codex/batch-3` worktree 执行；零命中命令均退出 1。
