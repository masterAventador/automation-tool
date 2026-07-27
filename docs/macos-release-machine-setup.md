# 在一台新 Mac 上把包出出来

写给**第一次在自己电脑上给本项目出 macOS 包**的人。仓库拉下来、依赖装好，并不等于能出包——还有五项**一次性前置**，每一项缺失都会让 `pnpm release:package` 中途失败，而其中三项的报错完全不指向真正的原因。

本文按"出包会在哪一步倒下"的顺序排列。全部装好之后，一次完整出包在 M 系列机器上约 20 分钟。

**先看这条判定规则**：`pnpm release:package` 的成败**以日志里的 `EXIT=` 为准**。如果你像下面这样把它包在别的命令里，shell 报的退出码是包装命令的，不是构建的：

```bash
cd frontend
{ pnpm release:package --platform macos > ../.local/release-build.log 2>&1; echo "EXIT=$?" >> ../.local/release-build.log; }
tail -20 ../.local/release-build.log
```

成功的结尾长这样：

```
[release] Release package built and every release gate passed
EXIT=0
```

---

## 一、Apple 签名身份与公证凭据

**症状**：`[release] Signing this release as ...` 这一行就出不来，或 codesign 找不到身份。

Developer ID 证书**每个账号上限 5 张**，不要各自去 Apple 后台申请。团队有一份现成的凭证包（含私钥、证书、公证密钥），换机器时整个目录拷过去（走 AirDrop，别过网盘和微信），然后：

```bash
bash setup-on-new-mac.sh
```

脚本做三件事：校验私钥与证书配对 → 把签名身份导入 login 钥匙串 → 用 `notarytool store-credentials` 建公证 profile。跑完验证：

```bash
security find-identity -v -p codesigning | grep "Developer ID Application"
xcrun notarytool history --keychain-profile at-tools-notary     # 能列出提交历史才算通
```

**第一次 codesign 会弹钥匙串授权框，一定要点"始终允许"**。点"允许"只放行一次，出包跑到执行器签名时会再弹一次并卡住。验证方法是连签两次同一个文件，第二次应在一秒内无弹窗完成。

**一个会指错方向的坑**：屏幕锁屏时，`notarytool` 报的是 `No Keychain password item found for profile: at-tools-notary`，`security find-generic-password` 也找不到——**看起来像凭据被删了**，实际只是锁屏时钥匙串授权弹不出来。出包前确认屏幕已解锁且不会自动锁。

---

## 二、`backend/.venv` 的解释器必须是 standalone CPython

**这一项最容易踩，报错也最不指向根因。**

**症状**（出包跑到执行器签名时）：

```
release assembly rejected: codesign failed:
  .../executor/automation-tool-executor/_internal/Python.framework:
  bundle format is ambiguous (could be app or framework)
```

**根因**：Homebrew 的 `python@3.12` 是 **framework 布局**，PyInstaller 会照着打出一个 `Python.framework`。而 `macos_candidate.py` 里 `shutil.copytree(staged_bundle, output_directory, symlinks=False)` 会把 framework 内的符号链接**实体化**——这是有意的，候选审计禁止产物含 symlink（见 `docs/backend-architecture.md`）。实体化之后，framework 顶层出现真实的 `Resources/Info.plist`，`Versions/Current` 也从符号链接变成真实目录副本，于是这个目录既像 app 又像 framework，codesign 拒绝签它。

**从 T129 起，候选审计会当场拒绝任何 `.framework` 并直接告诉你换解释器**，所以你多半是在构建早期看到一句能照做的话，而不是二十分钟后那句 codesign 的 `bundle format is ambiguous`。如果你确实看到了后者，说明你的检出早于 T129。

**修法**：换成 **uv 托管的 standalone CPython**（特征是 `lib/libpython3.12.dylib`、整个安装里没有任何 `.framework`），从源头就不会产生 framework：

```bash
cd backend
uv python install 3.12
uv venv --python ~/.local/share/uv/python/cpython-3.12-macos-aarch64-none/bin/python3.12 --clear
uv sync --locked
```

**怎么判断自己中了这一条**：

```bash
grep home backend/.venv/pyvenv.cfg
```

指向 `/opt/homebrew/...` 就是它。指向 `~/.local/share/uv/python/...` 才对。

**不要顺着报错去修 framework 结构**。把 `Versions/Current` 和顶层 `Resources` 恢复成符号链接确实能让 codesign 过（实测可签、`--verify --strict` 通过），但产物里就有了 symlink，会撞候选审计——那道审计是安全设计，不是绊脚石。

这条路项目自己一直在走：素材成片 Worker 锁定 CPython 3.11.15，产出的就是 `libpython3.11.dylib`，从来没有过这个问题。

**已加防御（T129）**：候选审计现在拒绝任何 `.framework`，理由是"签它需要 `Versions/Current` 是符号链接，而本载荷禁止 symlink，两者不可兼得"，并在同一句里给出修法。判据是实测出来的——五种 framework 形状里只有两种签得过，一种要 symlink，另一种是被当成老式 bundle 的侥幸，详见 `docs/development/T129.md`。

---

## 三、内置 Chromium 的锁定归档要手工下载一次

**症状**（出包第一步就停）：

```
release failed: locked archive is not downloaded yet:
  .local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip
```

**这不是缺陷，是有意的**。`contracts/browser/embedded-chromium-staging.v1.json` 的 `policy` 是 `fail_closed`：179 MB 的浏览器是要分发给客户的东西，契约给它锁了 sha256、锁了允许的重定向主机和路径前缀。构建脚本**不会**"发现缺了就自己上网抓一个回来"——那样一旦上游被投毒或 URL 被劫持，坏东西会静默进包。所以宁可停下来，要求运维显式下载一次、显式核对摘要。

下载（URL、摘要都以契约文件为准，下面是 2026-07-24 那版的值）：

```bash
mkdir -p .local/embedded-browser-video-studio/eb-03-cache
curl -fL --retry 3 -o .local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip \
  https://cdn.playwright.dev/builds/cft/149.0.7827.55/mac-arm64/chrome-mac-arm64.zip
shasum -a 256 .local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip
# 必须等于契约里的 archive_sha256，不等就别用
```

正常会 307 重定向到 `storage.googleapis.com/chrome-for-testing-public/149.0.7827.55/mac-arm64/`，落在契约的白名单里，最终 179,277,110 字节。

---

## 四、视频运行时四个产物

**症状**：出包在装配视频资源时失败，或者更糟——**静默用了过期的旧产物**（这正是历史上"提交在包里、效果不在"那类问题的机制）。

```bash
uv run --project backend --locked python scripts/prepare_video_runtime.py --platform macos
```

新机器要从零建四个产物，其中 **ffmpeg 从源码编译**，整体几分钟。产物落在 `~/Library/Caches/automation-tool-build/`（**不是** `.local/video-runtime`，那是旧布局残留）：

```
material-video-worker  357M
motion-video-worker    108M
media-toolchain         42M
subtitle-fonts          32M
```

**这个脚本成功时不打印任何东西**。别把"日志是空的"当成没干活，去看上面那个目录。

**出包前先核缓存是否仍然命中，别凭"我记得预建过"**。缓存键摘要了构建驱动脚本本身，改过它们的话旧产物就失效了：

```bash
backend/.venv/bin/python -c "
import sys, json; sys.path.insert(0,'scripts')
from prepare_video_runtime import MEDIA_TOOLCHAIN_INPUTS, MOTION_WORKER_INPUTS, MATERIAL_WORKER_INPUTS
from video_runtime_cache import contract_fingerprint, cache_root
root = cache_root()
for name, inputs in [('media-toolchain', MEDIA_TOOLCHAIN_INPUTS), ('motion-video-worker', MOTION_WORKER_INPUTS), ('material-video-worker', MATERIAL_WORKER_INPUTS)]:
    was = json.loads((root / f'{name}.stamp.json').read_text()).get('fingerprint')
    print(name, '命中' if was == contract_fingerprint(inputs) else '已失效')
"
```

失效的用 `--only <名字>` 单独补。**不要和出包并发跑**——四者共用每机一份的缓存目录，`ensure_cached` 会先 `rmtree` 再建。

---

## 五、网络：字体是构建期下载的

**症状**：

```
subtitle_font_assets.SubtitleFontUnavailable: cannot fetch
  https://raw.githubusercontent.com/notofonts/noto-cjk/.../NotoSansCJKsc-Regular.otf:
  [SSL] record layer failure
```

字幕字体由 `subtitle_font_assets.py` 在构建时从 `raw.githubusercontent.com` 下载并比对锁定摘要（与第三节的 Chromium 不同，这一项是有自动下载的）。国内网络下它对 DNS 和 hosts 很敏感。

排查顺序：

```bash
grep raw.githubusercontent /etc/hosts          # 过期的手工 IP 是最常见原因，hosts 优先级高于 DNS
dig +short raw.githubusercontent.com           # 与 dig +short @1.1.1.1 ... 对比看是否被污染
curl -sSI https://raw.githubusercontent.com/... # 看能不能真连上
```

`SSL: no alternative certificate subject name matches` 这种报错**几乎总是 hosts 里一条过时的 IP 把域名劫持到了别处**，而不是证书或代理出问题。删掉那条通常就好了。

### 五之二、零件目录也是构建期下载的，而且它落在各自的检出里（PC-16）

出包会把 134 个动效零件的发布树装进包（`Contents/Resources/motion-catalog`，46 MB）。
**这棵树按各自检出解析路径**，所以出包用的那棵树自己要有它：

```bash
backend/.venv/bin/python scripts/build_offline_motion_catalog.py   # 下载并本地化，含 7.42 MB 中文字体
backend/.venv/bin/python scripts/build_motion_catalog_release.py   # 合成只读发布树（46 MB / 337 文件）
```

**症状**（2026-07-27 实测踩到）：

```
download attempt 1/4 failed for https://fonts.gstatic.com/s/figtree/...woff2:
  [SSL: UNEXPECTED_EOF_WHILE_READING]
offline motion catalog build failed: download failed after 3 attempt(s)
```

第一步要从 `fonts.gstatic.com` 与 `raw.githubusercontent.com` 拉几十个字体文件，
国内不稳，重试四次也可能全灭。

**处置：从本机另一个已经建好的检出把 `.local/offline-motion-deps` 拷过来，再用目标树自己的门禁校验。**

```bash
cp -c -p -R <已建好的检出>/.local/offline-motion-deps .local/offline-motion-deps
backend/.venv/bin/python scripts/check_offline_motion_catalog.py   # 必须 rc=0
backend/.venv/bin/python scripts/build_motion_catalog_release.py
```

这不是取巧：离线目录**逐文件锁摘要**，可信度来自那道校验，不来自谁下载的。
反过来说，**没跑校验就直接用拷来的目录是不行的**——那才是取巧。

如果整台机器都是新的、没有任何建好的检出，就只能想办法让第一步的网络通
（代理、换网络、或先在别的机器上建好再拷盘）。错误信息指向的是「下载失败」，
不会告诉你还有「换个办法拿」这条路，所以记在这里。

---

## 出包与验收

```bash
cd frontend
{ pnpm release:package --platform macos > ../.local/release-build.log 2>&1; echo "EXIT=$?" >> ../.local/release-build.log; }
```

产物：

```
.local/release/cargo-target/release/bundle/dmg/自动化运营工具_0.1.0.dmg     ~469 MB
.local/release/cargo-target/release/bundle/macos/自动化运营工具.app
```

出包脚本自己会跑全部发版门禁，但**门禁通过不等于产物可用，自己再验一遍**。注意**卷内那个 `.app` 要单独验**——客户拖出去的是它，票据只贴在 DMG 上的话，那个副本还得联网找 Apple 才能开：

```bash
DMG=.local/release/cargo-target/release/bundle/dmg/自动化运营工具_0.1.0.dmg
MP=$(mktemp -d)                                        # 挂到私有 mountpoint，避开 /Volumes 的卷名冲突

spctl -a -t open --context context:primary-signature -vvv "$DMG"   # 要 accepted / Notarized Developer ID
xcrun stapler validate "$DMG"                                      # 要 The validate action worked!

hdiutil attach "$DMG" -nobrowse -readonly -mountpoint "$MP"
ls "$MP"                                               # 必须有 .app 和 Applications 拖拽链接
codesign --verify --deep --strict --verbose=2 "$MP/自动化运营工具.app"
spctl -a -vvv "$MP/自动化运营工具.app"
xcrun stapler validate "$MP/自动化运营工具.app"
find "$MP/自动化运营工具.app/Contents/Resources"/* -maxdepth 0 -type d | while read d; do
  echo "$(basename "$d") $(find "$d" -type f | wc -l)"
done
hdiutil detach "$MP"
```

五类资源的参考计数（2026-07-27，两台机器逐项一致）：

| 资源 | 文件数 |
| --- | --- |
| embedded-browser | 336 |
| local-executor | 293 |
| material-video-worker | 2107 |
| media-toolchain | 8 |
| motion-video-worker | 4 |

执行器里应该是 `_internal/libpython3.12.dylib`。**如果你在那里看到 `Python.framework`，说明第二节那条没做对**——这次的包能签出来只是因为侥幸，下次就未必。

---

## 速查

| 报错原文片段 | 真正的原因 | 去看 |
| --- | --- | --- |
| `bundle format is ambiguous (could be app or framework)` | venv 建在 Homebrew Python 上 | 第二节 |
| `locked archive is not downloaded yet` | Chromium 归档没下载，且脚本有意不自动下 | 第三节 |
| `SubtitleFontUnavailable: [SSL] record layer failure` | hosts 里有过期的 GitHub IP | 第五节 |
| `No Keychain password item found for profile` | 多半是屏幕锁了，不是凭据丢了 | 第一节 |
| 构建"成功"但功能不在包里 | 视频运行时缓存已失效却被静默复用 | 第四节 |
