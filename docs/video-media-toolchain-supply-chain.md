# 视频媒体工具供应链

## 1. 结论

App 只分发一对由项目从锁定源码构建的 FFmpeg/ffprobe，并同时提供给“智能素材成片”和“品牌动效成片”。生产环境不读取系统 PATH、不接受用户路径、不在运行时下载，也不采用上游各自携带的媒体程序。

版本固定为 FFmpeg 8.1.2 和 x264 `b35605ace3ddf7c1a5d67a2eb553f034aef41d55`。因为 H.264 软件编码依赖 GPL x264，整个独立媒体工具发行物按 `GPL-3.0-or-later` 管理，不能写成 LGPL。App 通过子进程调用工具，不把其库链接进 Rust、Python 或 Node 二进制。

## 2. 为什么从源码构建

FFmpeg 官网提供源码而不提供统一的 macOS/Windows 可执行文件。调研时实际下载的 Evermeet 8.1.2 macOS 文件是 x86_64，不能冒充首发 Apple Silicon 资源；Gyan Windows 文件虽然是 x86_64，也无法与 macOS 形成同源、同配置和同许可证集合。因此两个正式目标都从相同的 FFmpeg/x264 源码和同一脚本原生构建。

构建关闭自动依赖探测，只显式启用 x264 与 zlib。这样开发机上偶然安装的 SDL、Vulkan 或其他库不会进入正式产物。网络协议也在编译时关闭，媒体程序只处理 App 私有工作区中的本地文件和管道。

## 3. 能力基线

- 程序：FFmpeg、ffprobe。
- 编码：H.264/libx264、AAC、PNG。
- 解码：H.264、AAC、PNG。
- 容器与输入：MP4/MOV、concat、image2、image2pipe。
- 滤镜：缩放、叠加、拼接、音频混合与重采样。
- 协议：只要求本地文件与管道。

候选必须真实完成两条烟测：一条生成带 AAC 音频的 H.264 素材片段并用 concat 合并；另一条把 PNG 逐帧序列编码为 H.264 MP4。两条输出再由同一 ffprobe 独立读取 codec 信息。

## 4. 包布局与运行时边界

```text
media-toolchain/
├── manifest.json
├── BUILD-INFO.txt
├── COPYING.GPLv3
├── NOTICE.txt
├── bin/
│   ├── ffmpeg[.exe]
│   └── ffprobe[.exe]
└── source/
    ├── ffmpeg-8.1.2.tar.xz
    └── x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55.tar.gz
```

Rust 只从 Tauri `resource_dir/media-toolchain` 读取，逐文件核对路径、大小和 SHA-256，拒绝链接/reparse point、额外文件、缺失文件、目标不符、版本漂移和不可执行文件。绝对路径只作为受信 Worker 启动环境存在：Python 使用 `IMAGEIO_FFMPEG_EXE`，Node 使用 `HYPERFRAMES_FFMPEG_PATH` 与 `HYPERFRAMES_FFPROBE_PATH`；没有 Tauri command 将路径返回 React。

## 5. 构建与发布

`scripts/build_video_media_toolchain.sh` 只允许在 Apple Silicon macOS 或 Windows MINGW64 原生环境构建。源码下载后先核对摘要，构建结束后生成覆盖所有文件的运行时 Manifest。`.github/workflows/video-media-toolchain.yml` 在 `macos-15` 与 `windows-2025` 上分别构建、运行能力/真实编码烟测并上传待打包目录。

正式 Tauri 安装包只能消费对应 CI 验证通过的目录。源码归档、许可证、Notice 和构建参数必须与二进制一起进入安装包；后续若改变编解码器或增加外部库，必须重新做许可证审计和双平台烟测。
