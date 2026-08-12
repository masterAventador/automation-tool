"""单进程本地服务：控制面 HTTP + 执行器循环，一个 sidecar 全包。

App（Tauri）把它当作唯一的本地 sidecar 启动：

- stdin 收执行器 bootstrap 文档与平台命令（协议与旧执行器完全一致）；
- stdout 回执行器事件与平台命令结果（同旧协议）；
- 进程内起 uvicorn 服务控制面 HTTP（loopback），执行器循环跑在主线程，
  经 127.0.0.1 连本进程的 /api/v1/executors/connect —— 对外只有一个进程。

`--serve-only` 只起 HTTP（本机开发直接跑控制面用），不读 stdin。
"""

from __future__ import annotations

import os
import sys
import threading
import time

import uvicorn

from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES

LOCAL_SERVICE_PORT_ENVIRONMENT = "AUTOMATION_TOOL_LOCAL_SERVICE_PORT"
DEFAULT_LOCAL_SERVICE_PORT = 8765
_SERVER_START_TIMEOUT_SECONDS = 30.0


class LocalServiceStartupFailed(RuntimeError):
    """The in-process control plane did not come up."""


def service_port() -> int:
    configured = os.environ.get(LOCAL_SERVICE_PORT_ENVIRONMENT)
    if configured is None:
        return DEFAULT_LOCAL_SERVICE_PORT
    try:
        port = int(configured)
    except ValueError:
        raise LocalServiceStartupFailed("local service port is invalid") from None
    if not 1024 <= port <= 65535:
        raise LocalServiceStartupFailed("local service port is invalid")
    return port


def _build_server(port: int) -> uvicorn.Server:
    from automation_tool.control_plane.bootstrap.cli import local_app
    from automation_tool.control_plane.logging import install_control_plane_log_redaction

    install_control_plane_log_redaction()
    configuration = uvicorn.Config(
        local_app(),
        host="127.0.0.1",
        port=port,
        access_log=False,
        ws="websockets-sansio",
        ws_max_size=MAX_EXECUTOR_MESSAGE_BYTES,
        log_level="warning",
    )
    return uvicorn.Server(configuration)


def _start_server_thread(server: uvicorn.Server) -> threading.Thread:
    thread = threading.Thread(
        target=server.run,
        name="automation-tool-local-service-http",
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + _SERVER_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if server.started:
            return thread
        if not thread.is_alive():
            break
        time.sleep(0.05)
    raise LocalServiceStartupFailed("local service HTTP did not start")


def main() -> None:
    if sys.argv[1:] == ["--author-motion"]:
        # 一次性动效创作子进程：答一个请求就退出，不需要 HTTP 与执行器循环。
        from automation_tool.executor.motion_authoring import (
            serve_one_motion_authoring_request,
        )

        buffered = sys.stdin.buffer
        stream = getattr(buffered, "raw", buffered)
        raise SystemExit(serve_one_motion_authoring_request(stream, sys.stdout))

    port = service_port()
    server = _build_server(port)

    if "--serve-only" in sys.argv[1:]:
        server.run()
        return

    from automation_tool.executor.bootstrap import (
        ExecutorBootstrapRejected,
        read_executor_bootstrap,
    )
    from automation_tool.executor.cli import run_executor_with_bootstrap

    buffered_stdin = sys.stdin.buffer
    input_stream = getattr(buffered_stdin, "raw", buffered_stdin)
    try:
        bootstrap = read_executor_bootstrap(input_stream)
    except ExecutorBootstrapRejected:
        print("Local service bootstrap is rejected", file=sys.stderr, flush=True)
        raise SystemExit(2) from None

    thread = _start_server_thread(server)
    try:
        exit_code = run_executor_with_bootstrap(
            bootstrap,
            input_stream,
            sys.stdout,
            sys.stderr,
        )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
