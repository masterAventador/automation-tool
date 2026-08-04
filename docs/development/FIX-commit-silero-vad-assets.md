# FIX：Silero VAD 模型入库，并把「先验后用」补进两处 committed 读取

用户可操作：否

证据类型：分层实现

> 日期：2026-08-04
>
> 提交：本文件所在提交
>
> 类型：独立缺陷修复（不改任何 roadmap 任务状态）

## 缺陷

排查打包链路上剩余的联网点后，Silero VAD 是唯一一个既不需要编译、又没入库的静态资产：

```text
https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.1/src/silero_vad/data/…
silero_vad_16k_op15.onnx   1,289,603 bytes
SILERO-VAD-LICENSE.txt         1,075 bytes
```

1.3 MB 的语音活动检测权重（判断音频哪段有人说话），CPU-only、平台无关、不可变——
与刚入库的字体同一性质，却仍然每台干净机器都要下一次。

**第二个缺陷是本次改动自己引入的，由一条既有测试抓到。** 把 committed 副本做成
「文件在就用」之后，`test_assets_are_fetched_once_verified_and_cached_by_the_locked_contract`
立刻红了：它用合成契约 + 合成字节，而契约里的 `cachedName` 与仓库里的真实文件同名，
于是「文件在就用」读到了一个**它根本没在问的文件**，摘要不符被拒。

这条测试守的是对的东西：**决定什么算这个资产的，只能是锁里的摘要，不能是文件名。**

## RED

`scripts/test_committed_locked_assets.py` 新增第 4 条，注入「一被调用就抛异常」的
fetcher，先失败：

```text
ERROR test_model_builds_without_touching_the_network
  SileroVadAssetUnavailable: cannot fetch the locked Silero VAD asset
```

命名冲突那条缺陷的 RED 是既有测试给的（未改测试，改的是实现）：

```text
FAILED scripts/test_silero_vad_assets.py::
  test_assets_are_fetched_once_verified_and_cached_by_the_locked_contract
```

## GREEN

两个文件入库到 `assets/silero-vad/`，取字节入口改为**先验后用**：committed 副本先
算 SHA-256，与锁不符就落回网络，而不是把本地文件当答案。同一处修正补进
`subtitle_font_assets.acquire()`——它有一模一样的命名冲突隐患，只是当时没有测试撞上。

```text
backend/.venv/bin/python scripts/test_silero_vad_assets.py         9 passed
backend/.venv/bin/python scripts/test_committed_locked_assets.py   Ran 4 tests, OK
backend/.venv/bin/python scripts/test_le20_caption_font_assets.py  7 checks passed
backend/.venv/bin/python scripts/test_prepare_video_runtime.py     Ran 16 tests, OK
uvx ruff check（三个改动文件）                                      All checks passed
```

## 排查结论：打包链路上还剩几个联网点

| 资源 | 状态 | 判定 |
| --- | --- | --- |
| Chromium 归档 171 MB | 人工下载 | **保持**（用户指定除外；体积是其余总和的数倍） |
| 字幕字体 63 MB / 动效依赖 21 MB | 已入库 | 完成 |
| Silero VAD 1.3 MB | 本次入库 | 完成 |
| ffmpeg 11 MB + x264 1 MB **源码 tarball** | 下载→本机编译→缓存 | **保持**：源码必须按架构编译，属「没有就自动构建」那一类；构建产物已含源码副本（GPL 合规） |
| Node 22.23.1 运行时 108 MB | 下载→缓存 | **保持**（用户决定）：预编译二进制无法「自动构建」，且平台专属，入库需每平台一份 |
| PyPI / npm / crates.io 依赖 | 锁文件钉版本 | 常规依赖管理，不属资源 |

前端构建期零下载；`backend/src` 里的 `urlopen` 全部是运行时调用（模型 API、语音转写），
不在打包链路上。

## 真实边界

- 只在 macOS arm64 验证；committed 读取无平台分支；
- 摘要校验一处未放宽：committed 与下载走同一套断言，且现在多一层「先验后用」；
- 机器级 `silero-vad` 缓存保留，它现在从仓库副本构建。

## 清理

无临时产物；测试使用临时缓存根。

## 遗留项

无。
