"""H8-23: repositories must redact the two connection failures they never caught.

Every repository here already normalises `SQLAlchemyError`, and every existing
test proves it by injecting one. That is why the gap survived: the two failures
that actually happen in production are **not** `SQLAlchemyError` subclasses, so
an injected `SQLAlchemyError` can never reveal them.

1. **Connection refused** surfaces as asyncio's `OSError`, and its message
   carries the host and port it tried.
2. **Wrong password / missing role / connection limit** surfaces as asyncpg's
   `PostgresError`, and its message carries the role name and the database name.

Both would travel out of the repository unchanged, reach the caller and be
written to a log — the boundary CLAUDE.md §7 draws around credentials, hosts and
private paths.

These tests inject nothing. They point a real engine at a closed port and let
the real driver produce the real exception, because the whole defect is that the
real exception has a type the tests never used.
"""

from __future__ import annotations

import ast
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest
from automation_tool.control_plane.domain import InstallationId, UserId
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.workbench_metrics_repository import (
    SqlAlchemyWorkbenchMetricsRepository,
)

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
USER_ID = UserId.parse("123e4567-e89b-42d3-a456-426614174000")
INSTALLATION_ID = InstallationId.parse("223e4567-e89b-42d3-a456-426614174000")

# A distinctive role and database name: if either reaches the caller, the
# assertion can name exactly what leaked instead of guessing.
LEAK_ROLE = "h823roleleak"
LEAK_DATABASE = "h823databaseleak"


def closed_loopback_port() -> int:
    """Return a port that is bound and immediately released, so nothing listens."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def refused_database() -> Database:
    port = closed_loopback_port()
    return Database.from_url(
        f"postgresql+asyncpg://{LEAK_ROLE}:unused@127.0.0.1:{port}/{LEAK_DATABASE}",
        connect_timeout_seconds=1.0,
    )
@pytest.mark.asyncio
async def test_workbench_metrics_repository_redacts_a_refused_connection() -> None:
    repository = SqlAlchemyWorkbenchMetricsRepository(refused_database())

    with pytest.raises(Exception) as raised:  # 断言在下面：抓什么类型正是被测行为
        await repository.get(installation_id=INSTALLATION_ID)

    rendered = f"{type(raised.value).__name__}: {raised.value}"
    assert not isinstance(raised.value, OSError), (
        f"connection refused escaped the repository unchanged: {rendered}"
    )
    assert "127.0.0.1" not in rendered, f"the host reached the caller: {rendered}"
    assert LEAK_DATABASE not in rendered, f"the database name reached the caller: {rendered}"


def test_no_repository_catches_sqlalchemy_without_oserror() -> None:
    """结构断言：仓储层不许再出现只接 `SQLAlchemyError` 的 except。

    上面三条是行为断言，但它们只覆盖三个入口。这条覆盖**整个仓储目录**，包括将来
    新写的——判据是 AST 而不是 grep，改个换行躲不掉。

    这个缺口当初能存活，正是因为没有任何东西看着「接的是哪些异常类型」：每个仓储
    各自看起来都合理，而两类真实故障恰好都在 `SQLAlchemyError` 之外。

    台账（`docs/development-roadmap.md` H8-23 行）说范围是六个仓储、其余「已正确收口」。
    实测不是：`bilibili_publish` 与 `platform_session_health` 同样是裸的、零 `OSError`，
    共八个仓储 20 处。本断言按代码事实划范围，不按台账。
    """
    database = (
        Path(__file__).resolve().parents[3]
        / "src/automation_tool/control_plane/infrastructure/database"
    )
    assert database.is_dir(), database

    def caught_names(handler: ast.ExceptHandler) -> set[str]:
        node = handler.type
        if node is None:
            return {"<bare>"}
        parts = node.elts if isinstance(node, ast.Tuple) else [node]
        return {part.id for part in parts if isinstance(part, ast.Name)}

    offenders: list[str] = []
    for path in sorted(database.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            names = caught_names(node)
            if "SQLAlchemyError" in names and "OSError" not in names:
                offenders.append(f"{path.name}:{node.lineno} 只接 {sorted(names)}")

    assert offenders == [], (
        "这些 except 接不住连接被拒（asyncio OSError，消息带 host:port）：\n"
        + "\n".join(offenders)
    )
