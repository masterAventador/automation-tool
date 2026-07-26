# C8：品牌动效编排写盘失败封闭

- 日期：2026-07-27
- 基线：`eef677e`
- 对应审计格：`C6` 第 3 格“品牌动效编排写盘失败会逃出封闭错误边界”
- 状态：✅ 代表性写盘失败已封闭

## 用户影响与完成边界

演示主路径会依次落地 `DESIGN.json`、`SCRIPT.json`、`STORYBOARD.json`、
合成 HTML 和 `renderjob.json`。此前任一 `Path.write_text()` 因磁盘满、权限变化或
短写失败抛出 `OSError` 时，异常会越过一次性 Executor 的 JSON 边界；异常文本还可能
携带用户工作区绝对路径，先前已成功写出的文件也会留在工作区。

本次将此类持久化失败归一到既有固定结果：

```json
{"rejectionReason":"workspace_unusable","schemaVersion":1,"status":"app_request_invalid"}
```

这不是“模型拒绝了用户描述”。现有 Rust 消费端会把 `app_request_invalid` 归为通用
`authoring_crashed`，因此不会让用户误以为要重写描述。原始 `OSError`、本机路径、
模型密钥和模型输出均不进入响应。

`AuthoringWorkspace` 在每次新文件写入前记录目标，并且只有它会把写入过程中的
`OSError` 转为专用 `MotionAuthoringPersistenceError`。它先回滚本轮新建且不属于预置
资源的文件，再由入口把这一个专用类型映射到固定响应；预置的品牌素材与离线运行时
不删除。清理为尽力而为：磁盘或权限故障也可能阻止删除，但第二个清理异常不会替代
固定响应或泄漏本机路径。

锁定工作流、合同或其他非工作区 I/O 抛出的意外 `OSError` 不进入这个映射，避免把
安装损坏或其他内部故障伪装成 `workspace_unusable`。这些边界仍由各自已有的领域错误
转换负责。

## RED

先在正式 `serve_one_motion_authoring_request()` → `run_motion_authoring_entry()` →
`MotionAuthoringAgent.author()` 路径注入短写：`DESIGN.json` 先写入 8 个字符，再抛出
带私有绝对路径的 `OSError`。模型网络仅替换为确定性响应，写盘、编排和进程序列化边界
仍走生产实现。

命令：

```text
backend/.venv/bin/pytest -q \
  backend/tests/unit/executor/test_motion_authoring_entry.py::test_a_partial_workspace_write_is_closed_and_rolled_back
```

结果：`1 failed`。失败是业务断言
`workspace persistence failure escaped the closed process boundary: OSError`，不是导入、
文件缺失或环境错误。

## GREEN

最小实现：

1. 写入前记录本轮新目标，使“已短写后抛错”的残片也可识别；
2. `AuthoringWorkspace.write_text()` 只把自身写入阶段的 `OSError` 转为专用持久化
   异常，并先回滚本轮新文件；
3. `run_motion_authoring_entry()` 只捕获该专用类型并复用既有 `workspace_unusable`
   封闭映射，其他 `OSError` 不会被误分类；
4. 不新增任意错误字符串、第二份状态表或前端特判。

同一命令结果：`1 passed`。

质量复核后增加了边界收窄测试：

```text
backend/.venv/bin/pytest -q \
  backend/tests/unit/executor/test_motion_authoring_entry.py::test_a_non_workspace_oserror_is_not_misclassified_as_workspace_unusable
```

增量 RED：`1 failed`，`pytest.raises(OSError)` 实际收到
`MotionAuthoringEntryRejected`，证明原先入口级宽泛捕获确会把锁定工作流读取故障错误
映射为 `workspace_unusable`。

增量 GREEN：专用异常下沉后，上述测试与短写回归一起执行为 `2 passed`。

## 回归验证

```text
backend/.venv/bin/pytest -q \
  backend/tests/unit/executor/test_motion_authoring_entry.py
# 43 passed

backend/.venv/bin/python scripts/test_motion_authoring_agent.py
# Ran 72 tests ... OK

git diff --check
# exit 0
```

第一条入口测试必须在沙盒外运行，因为既有模型超时用例会绑定隔离 loopback 端口；
沙盒内的 `PermissionError: Operation not permitted` 是环境权限，不是产品失败。

现有成功用例继续证明用户演示路径能产出全部五类文件并返回 `authored`；新增故障用例
证明首个 JSON 短写时响应稳定、私有路径和密钥不泄漏、残片删除且预置运行时仍在。

## 未覆盖边界

- 本任务没有用真实磁盘占满或真实 ACL 变化做目标系统验收；证据是生产入口上的确定性
  故障注入，因此只完成 C6 第 3 格的代码级失败边界。
- 清理本身若因同一权限故障失败，固定响应仍成立，但磁盘残片只能由工作区生命周期的
  上层清理兜底。
- 修复轮次 HTML 和最终 `renderjob.json` 使用同一个 `AuthoringWorkspace.write_text()`
  与同一个入口异常边界，本次没有为每个文件名复制同义测试。

## C6 其余六格

本任务没有降低或删除其余缺口，仍需分别处理：

1. B 站视频摘要复验后的同名替换竞态；
2. 阿里云剪辑同 Artifact ID 并发落盘覆盖；
3. 本地注册交接目录 symlink / identity 替换；
4. 本地注册交接文件并发与崩溃耐久性；
5. Executor manifest 与签名双文件写入事务性；
6. macOS / Windows PyInstaller 超时失败矩阵。
