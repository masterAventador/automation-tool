#!/usr/bin/env python3
"""CQ-03：三条业务线并发用同一个浏览器二进制时的隔离判据。

RPA 的运营 Profile 里装着用户的平台登录态。Browser Use 的独立会话与动效渲染进程
共用同一个包内 Chromium 二进制，但**不得接触那个 Profile**——否则一次渲染崩溃就能
带走用户的登录态，而这两条线的输入（网页内容、生成的 HTML）都是不可信的。

两条判据：

1. **路径互不相交**。同一个目录当然不行；一条线的 Profile 嵌在另一条里更隐蔽——
   路径字符串不同，写入却落进对方的树。
2. **运营 Profile 在并发期间原样未动**。取运行前后的目录指纹逐项对比，新增、修改、
   删除都算被碰过。目录不存在与目录为空必须可区分，否则监控一个打错的路径会永远
   显示通过。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_FINGERPRINT_CHUNK = 1024 * 1024


class ConcurrentIsolationRejected(RuntimeError):
    """并发的业务线之间发生了不该有的接触。"""


def _reject(message: str) -> None:
    raise ConcurrentIsolationRejected(f"concurrent isolation rejected: {message}")


def require_disjoint_profiles(profiles: dict[str, Path]) -> None:
    """拒绝任何两条业务线共用、或互相嵌套的 Profile 路径。"""
    if len(profiles) < 2:
        _reject(
            "isolation needs at least two concurrent lines to mean anything; "
            f"got {sorted(profiles)}"
        )
    resolved = {name: path.resolve() for name, path in profiles.items()}
    names = sorted(resolved)
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            one, other = resolved[first], resolved[second]
            if one == other:
                _reject(f"{first} and {second} share one profile: {one}")
            if one.is_relative_to(other) or other.is_relative_to(one):
                _reject(f"{first} and {second} nest into each other: {one} / {other}")


def directory_fingerprint(root: Path) -> dict[str, str] | None:
    """返回目录内每个文件的内容摘要；目录不存在时返回 ``None``。

    ``None`` 与空字典有意区分：把两者都当"空"，会让一个路径打错的监控永远通过。
    """
    if not root.is_dir():
        return None
    fingerprint: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            fingerprint[path.relative_to(root).as_posix()] = f"symlink:{path.readlink()}"
            continue
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(_FINGERPRINT_CHUNK):
                    digest.update(chunk)
        except OSError as error:
            fingerprint[path.relative_to(root).as_posix()] = f"unreadable:{error.errno}"
            continue
        fingerprint[path.relative_to(root).as_posix()] = digest.hexdigest()
    return fingerprint


def require_untouched(
    profile: Path,
    before: dict[str, str] | None,
    after: dict[str, str] | None,
) -> None:
    """拒绝运营 Profile 在并发期间被另一条线新增、改写或删除内容。"""
    if before is None or after is None:
        _reject(
            f"{profile} was missing on one side of the comparison "
            f"(before={'present' if before is not None else 'missing'}, "
            f"after={'present' if after is not None else 'missing'})"
        )
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(name for name in set(before) & set(after) if before[name] != after[name])
    if added or removed or changed:
        _reject(
            f"{profile} was touched during the concurrent run: "
            f"added={added} removed={removed} changed={changed}"
        )


def require_isolated_transition(
    *,
    before: dict[str, bool],
    after: dict[str, bool],
    stopped: set[str],
    scenario: str,
) -> None:
    """验证一次取消、崩溃或全局停止只影响点名的进程树。

    ``before`` 必须是所有参与线都存活的非空基线；否则“另一条线仍然存活”可能在动作
    发生前就是假命题。动作后，``stopped`` 中的线必须全部退出，其余线必须全部存活。
    """
    if not before:
        _reject(f"{scenario}: no concurrent lines were observed before the transition")
    if set(before) != set(after):
        _reject(
            f"{scenario}: line set changed across the transition "
            f"(before={sorted(before)}, after={sorted(after)})"
        )
    unknown = stopped - set(before)
    if unknown:
        _reject(f"{scenario}: unknown stopped lines: {sorted(unknown)}")
    if not stopped:
        _reject(f"{scenario}: no stopped line was declared")
    for name, alive in before.items():
        if not alive:
            _reject(f"{scenario}: {name} was not running before the transition")
    for name, alive in after.items():
        if name in stopped:
            if alive:
                _reject(f"{scenario}: {name} is still running")
        elif not alive:
            _reject(f"{scenario}: {name} unexpectedly stopped")


__all__ = [
    "ConcurrentIsolationRejected",
    "directory_fingerprint",
    "require_disjoint_profiles",
    "require_isolated_transition",
    "require_untouched",
]
