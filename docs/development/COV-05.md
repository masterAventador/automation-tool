# COV-05 发布链 178 点

用户可操作：否
证据类型：分层实现

> 状态：✅ 已完成。178 → **0**。
> 上游计划：`docs/development/2026-08-03-backend-coverage-debt-plan.md`
> 前置：[COV-04](COV-04.md)
> 分支：`coverage/backend-100`

## 1. 起点核对

| 模块 | 起点 | 现在 |
|---|---:|---:|
| `rpa/douyin/publish_release.py` | 43 | **0** |
| `control_plane/api/bilibili_publishing.py` | 29 | **0** |
| `rpa/douyin/publish_page.py` | 26 | **0** |
| `infrastructure/bilibili/token_provider.py` | 24 | **0** |
| `rpa/douyin/publish_artifact.py` | 13 | **0** |
| `infrastructure/database/bilibili_publish_repository.py` | 13 | **0** |
| `rpa/douyin/publish_preflight.py` | 11 | **0** |
| `rpa/douyin/search_page.py` | 10 | **0** |
| `rpa/douyin/side_effect_recovery.py` | 12 | **0** |
| `bootstrap/bilibili_publishing.py` | 7 | **0** |
| `infrastructure/bilibili/open_api_client.py` | 2 | **0** |

## 2. 真实边界

### 2.1 页面对象的时间窗只能靠「两次读之间改页面」制造

抖音发布页与搜索页的多数缺口都是同一形状：某个锚点在**观察时在、取用时
不在**。这类窗口用「按选择器计数、到第 N 次请求时改页面」的 FakePage 子类
制造，例如：

- 发布控件在「问它是否可按」与「把它交出去按」之间消失；
- 交接对话框在 `observe()` 与 `_first_visible()` 之间离场；
- 信息流行数报三条、去取第二条时已经没有；
- 行被固定成快照之后立刻隐藏。

判据是拒绝，而不是「取到了另一个元素」——取错元素正是这些闸要挡的事。

### 2.2 派发前的最后一道窗口交还

`DouyinPublishRelease.run` 一共问三次「窗口还是我的吗」：进场、上传前、
**按下前**。第三次此前没有用例。用「第 N 次授权后交给操作者」的租约把它
制造出来，断言 `page.clicked == []`——按钮没被按过才是这条闸的意义。

### 2.3 另一个进程抢先登记派发

`begin_publish_dispatch` 返回重放记录这条路，此前也没有用例：`prepare` 与
`begin` 之间有真实的竞争窗口。用一个「第二次读时钟时顺手替这个作业登记
派发」的时钟把竞争造出来，断言得到 `REPLAY_UNCERTAIN` 且**没有按下**。

### 2.4 两处确实不可达，处理方式不同

- `open_publish_artifact` 里的 `except DouyinPublishArtifactRejected: raise`：
  三个下游助手（路径、媒体类型、摘要）只抛 `ValueError`，这一支没有来源。
  **删掉**——它与下面那支产出的异常类型相同，删掉不改变任何调用方看到的结果。
- `_settle_against_works_list` 里的 `except Exception` 包着
  `open_works_list()`：那个方法自己把 goto、等待、观察全部接住了，不会抛。
  但它守的是**一次不可逆点击之后**的路径，删掉等于让异常穿过 `run()`，
  比留着更糟。改为让用例把 `_WORKS_LIST_TIMEOUT_MILLISECONDS` 打成非法值，
  从真实调用路径触发这条守卫：点击已经发生，去取证的路上被拒绝，结果必须是
  「结果不确定」，不能是异常。

### 2.5 抖音发布制品的读窗口

`_digest_stable_file` 有四种拒绝：提前读空、超出声明尺寸、描述符前后身份
漂移、关闭后路径被顶替。全部靠 hook `os.read` / `os.fstat` / `os.close`
制造；非本人属主那条用 `monkeypatch os.getuid` 造（平台值注入，断言落在
「拒绝了」）。

## 3. 失败矩阵

| 场景 | 结果 |
|---|---|
| 制品在预检与上传之间被换掉（同尺寸不同内容） | 拒绝 |
| 制品有第二个硬链接名字 | 拒绝 |
| 表单打完字后消失 | 报告，不按 |
| 发布控件答非布尔 / 拒答 / 消失 | 报告，不按 |
| 同一字段出现两个锚点 | 判为页面改版 |
| 按下前窗口被交还 | 不按 |
| 另一进程抢先登记派发 | 重放为结果不确定，不按 |
| 作品列表打不开 / 行读不出来 | 结果不确定 |
| 校验写不进台账 | 结果不确定 |
| B 站网关可达但回的不是 UTF-8 JSON | 判为网关不可用 |
| B 站契约文件三处都找不到 / 解析不了 | 配置错误，运行时不启动 |

## 4. 清理

新增用例只用内存 Fake 页面与 `tmp_path`；B 站那条真实 loopback 用例在
`finally` 里 `server.shutdown()` + `server_close()` + `thread.join()`，
不留监听端口。

## 5. 证据

- 提交：`49ad4144`、`45633237`、`3be5e23c`、`f2196487`、`04b3fd81`、
  `e6a2b393`、`ad6c0998`
- 收口测量：上表全部模块的 `missing_lines` 与 `missing_branches` 均为 `[]`
- 全量：见 [COV-06](COV-06.md) §5
