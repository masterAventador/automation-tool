"""FastAPI application factory and process lifespan."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from math import isfinite

from fastapi import FastAPI

from automation_tool import __version__
from automation_tool.control_plane.api.device_credentials import (
    router as device_credential_router,
)
from automation_tool.control_plane.api.device_sessions import (
    router as device_session_router,
)
from automation_tool.control_plane.api.errors import (
    install_request_context,
    register_error_handlers,
)
from automation_tool.control_plane.api.executor_websocket import (
    router as executor_websocket_router,
)
from automation_tool.control_plane.api.installation_access import (
    router as installation_access_router,
)
from automation_tool.control_plane.api.platform_sessions import (
    router as platform_session_router,
)
from automation_tool.control_plane.api.registrations import router as registration_router
from automation_tool.control_plane.api.system import router as system_router
from automation_tool.control_plane.api.task_controls import router as task_control_router
from automation_tool.control_plane.api.task_discoveries import router as task_discovery_router
from automation_tool.control_plane.api.task_event_stream import router as task_event_stream_router
from automation_tool.control_plane.api.task_target_previews import (
    router as task_target_preview_router,
)
from automation_tool.control_plane.api.task_target_results import (
    router as task_target_result_router,
)
from automation_tool.control_plane.api.tasks import router as task_router
from automation_tool.control_plane.api.workbench import router as workbench_router
from automation_tool.control_plane.application.device_credentials import DeviceCredentialService
from automation_tool.control_plane.application.device_sessions import DeviceSessionService
from automation_tool.control_plane.application.executor_connection_registry import (
    ExecutorConnectionRegistry,
)
from automation_tool.control_plane.application.executor_connections import (
    ExecutorConnectionService,
)
from automation_tool.control_plane.application.platform_session_health import (
    PlatformSessionHealthService,
)
from automation_tool.control_plane.application.registration import InstallationRegistrationService
from automation_tool.control_plane.application.task_command_delivery import (
    TaskCommandDeliveryService,
)
from automation_tool.control_plane.application.task_controls import TaskControlService
from automation_tool.control_plane.application.task_discovery import (
    TaskDiscoveryConvergenceService,
    TaskDiscoveryStartService,
)
from automation_tool.control_plane.application.task_event_convergence import (
    TaskEventConvergenceService,
)
from automation_tool.control_plane.application.task_event_stream import TaskEventStreamService
from automation_tool.control_plane.application.task_queries import TaskQueryService
from automation_tool.control_plane.application.task_target_previews import (
    TaskTargetPreviewService,
)
from automation_tool.control_plane.application.task_target_results import (
    TaskTargetResultService,
)
from automation_tool.control_plane.application.tasks import TaskCreationService
from automation_tool.control_plane.application.workbench_metrics import WorkbenchMetricsService
from automation_tool.control_plane.bootstrap.database import database_from_environment
from automation_tool.control_plane.bootstrap.device_credentials import (
    device_credential_service as build_device_credential_service,
)
from automation_tool.control_plane.bootstrap.device_sessions import (
    device_session_service as build_device_session_service,
)
from automation_tool.control_plane.bootstrap.platform_sessions import (
    platform_session_health_service as build_platform_session_health_service,
)
from automation_tool.control_plane.bootstrap.registration import (
    registration_service_from_environment,
)
from automation_tool.control_plane.bootstrap.task_commands import (
    task_command_delivery_service as build_task_command_delivery_service,
)
from automation_tool.control_plane.bootstrap.task_commands import (
    task_control_service as build_task_control_service,
)
from automation_tool.control_plane.bootstrap.task_discovery import (
    task_discovery_services as build_task_discovery_services,
)
from automation_tool.control_plane.bootstrap.task_event_stream import (
    task_event_stream_service as build_task_event_stream_service,
)
from automation_tool.control_plane.bootstrap.task_events import (
    task_event_convergence_service as build_task_event_convergence_service,
)
from automation_tool.control_plane.bootstrap.task_target_previews import (
    task_target_preview_service as build_task_target_preview_service,
)
from automation_tool.control_plane.bootstrap.task_target_results import (
    task_target_result_service as build_task_target_result_service,
)
from automation_tool.control_plane.bootstrap.tasks import (
    task_creation_service as build_task_creation_service,
)
from automation_tool.control_plane.bootstrap.tasks import (
    task_query_service as build_task_query_service,
)
from automation_tool.control_plane.bootstrap.workbench import (
    workbench_metrics_service as build_workbench_metrics_service,
)
from automation_tool.control_plane.domain import DatabaseLifecycle
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.logging import install_control_plane_log_redaction


class _FromEnvironment:
    """Sentinel that distinguishes production defaults from an explicit no-database app."""


_FROM_ENVIRONMENT = _FromEnvironment()


def _positive_finite_seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError("Executor connection timeouts must be positive")
    if not isfinite(value) or value <= 0:
        raise ValueError("Executor connection timeouts must be positive")
    return float(value)


def _positive_finite_stream_seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError("Task event stream timing must be positive")
    if not isfinite(value) or value <= 0:
        raise ValueError("Task event stream timing must be positive")
    return float(value)


@asynccontextmanager
async def control_plane_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own resources that must exist for exactly one application lifespan."""

    app.state.lifecycle_state = "running"
    try:
        yield
    finally:
        registry = app.state.executor_connection_registry
        if isinstance(registry, ExecutorConnectionRegistry):
            await registry.shutdown()
        database: DatabaseLifecycle | None = app.state.database
        if database is not None:
            await database.close()
        app.state.lifecycle_state = "stopped"


def create_app(
    *,
    database: DatabaseLifecycle | None | _FromEnvironment = _FROM_ENVIRONMENT,
    registration_service: InstallationRegistrationService | None = None,
    device_credential_service: DeviceCredentialService | None = None,
    device_session_service: DeviceSessionService | None = None,
    executor_connection_service: ExecutorConnectionService | None = None,
    executor_connection_registry: ExecutorConnectionRegistry | None = None,
    platform_session_health_service: PlatformSessionHealthService | None = None,
    task_creation_service: TaskCreationService | None = None,
    task_query_service: TaskQueryService | None = None,
    task_command_delivery_service: TaskCommandDeliveryService | None = None,
    task_control_service: TaskControlService | None = None,
    task_discovery_start_service: TaskDiscoveryStartService | None = None,
    task_discovery_convergence_service: TaskDiscoveryConvergenceService | None = None,
    task_target_preview_service: TaskTargetPreviewService | None = None,
    task_target_result_service: TaskTargetResultService | None = None,
    task_event_convergence_service: TaskEventConvergenceService | None = None,
    task_event_stream_service: TaskEventStreamService | None = None,
    workbench_metrics_service: WorkbenchMetricsService | None = None,
    executor_connection_hello_timeout_seconds: float = 5.0,
    executor_connection_recheck_interval_seconds: float = 1.0,
    task_event_stream_poll_interval_seconds: float = 0.25,
    task_event_stream_keepalive_interval_seconds: float = 15.0,
    task_event_stream_max_connection_seconds: float = 55.0,
) -> FastAPI:
    """Create an isolated Control Plane application instance."""

    install_control_plane_log_redaction()
    resolved_database = (
        database_from_environment() if isinstance(database, _FromEnvironment) else database
    )
    resolved_registration_service = registration_service
    resolved_device_credential_service = device_credential_service
    resolved_device_session_service = device_session_service
    resolved_executor_connection_service = executor_connection_service
    resolved_executor_connection_registry = (
        executor_connection_registry or ExecutorConnectionRegistry()
    )
    resolved_platform_session_health_service = platform_session_health_service
    resolved_task_creation_service = task_creation_service
    resolved_task_query_service = task_query_service
    resolved_task_command_delivery_service = task_command_delivery_service
    resolved_task_control_service = task_control_service
    resolved_task_discovery_start_service = task_discovery_start_service
    resolved_task_discovery_convergence_service = task_discovery_convergence_service
    resolved_task_target_preview_service = task_target_preview_service
    resolved_task_target_result_service = task_target_result_service
    resolved_task_event_convergence_service = task_event_convergence_service
    resolved_task_event_stream_service = task_event_stream_service
    resolved_workbench_metrics_service = workbench_metrics_service
    if (
        resolved_registration_service is None
        and isinstance(database, _FromEnvironment)
        and isinstance(resolved_database, Database)
    ):
        resolved_registration_service = registration_service_from_environment(resolved_database)
    if resolved_device_credential_service is None and isinstance(resolved_database, Database):
        resolved_device_credential_service = build_device_credential_service(resolved_database)
    if resolved_device_session_service is None and isinstance(resolved_database, Database):
        resolved_device_session_service = build_device_session_service(resolved_database)
    if resolved_executor_connection_service is None and resolved_device_session_service is not None:
        resolved_executor_connection_service = ExecutorConnectionService(
            resolved_device_session_service
        )
    if resolved_platform_session_health_service is None and isinstance(resolved_database, Database):
        resolved_platform_session_health_service = build_platform_session_health_service(
            resolved_database
        )
    if resolved_task_creation_service is None and isinstance(resolved_database, Database):
        resolved_task_creation_service = build_task_creation_service(resolved_database)
    if resolved_task_query_service is None and isinstance(resolved_database, Database):
        resolved_task_query_service = build_task_query_service(resolved_database)
    if resolved_task_command_delivery_service is None and isinstance(resolved_database, Database):
        resolved_task_command_delivery_service = build_task_command_delivery_service(
            resolved_database,
            resolved_executor_connection_registry,
        )
    if resolved_task_control_service is None and isinstance(resolved_database, Database):
        resolved_task_control_service = build_task_control_service(resolved_database)
    if (
        resolved_task_discovery_start_service is None
        and resolved_task_discovery_convergence_service is None
        and isinstance(resolved_database, Database)
    ):
        (
            resolved_task_discovery_start_service,
            resolved_task_discovery_convergence_service,
        ) = build_task_discovery_services(resolved_database)
    if resolved_task_target_preview_service is None and isinstance(resolved_database, Database):
        resolved_task_target_preview_service = build_task_target_preview_service(resolved_database)
    if resolved_task_target_result_service is None and isinstance(resolved_database, Database):
        resolved_task_target_result_service = build_task_target_result_service(resolved_database)
    if resolved_task_event_convergence_service is None and isinstance(resolved_database, Database):
        resolved_task_event_convergence_service = build_task_event_convergence_service(
            resolved_database
        )
    if resolved_task_event_stream_service is None and isinstance(resolved_database, Database):
        resolved_task_event_stream_service = build_task_event_stream_service(resolved_database)
    if resolved_workbench_metrics_service is None and isinstance(resolved_database, Database):
        resolved_workbench_metrics_service = build_workbench_metrics_service(resolved_database)
    hello_timeout_seconds = _positive_finite_seconds(executor_connection_hello_timeout_seconds)
    recheck_interval_seconds = _positive_finite_seconds(
        executor_connection_recheck_interval_seconds
    )
    stream_poll_interval_seconds = _positive_finite_stream_seconds(
        task_event_stream_poll_interval_seconds
    )
    stream_keepalive_interval_seconds = _positive_finite_stream_seconds(
        task_event_stream_keepalive_interval_seconds
    )
    stream_max_connection_seconds = _positive_finite_stream_seconds(
        task_event_stream_max_connection_seconds
    )

    app = FastAPI(
        title="automation-tool Control Plane",
        version=__version__,
        lifespan=control_plane_lifespan,
    )
    app.state.lifecycle_state = "created"
    app.state.database = resolved_database
    app.state.registration_service = resolved_registration_service
    app.state.device_credential_service = resolved_device_credential_service
    app.state.device_session_service = resolved_device_session_service
    app.state.executor_connection_service = resolved_executor_connection_service
    app.state.executor_connection_registry = resolved_executor_connection_registry
    app.state.platform_session_health_service = resolved_platform_session_health_service
    app.state.task_creation_service = resolved_task_creation_service
    app.state.task_query_service = resolved_task_query_service
    app.state.task_command_delivery_service = resolved_task_command_delivery_service
    app.state.task_control_service = resolved_task_control_service
    app.state.task_discovery_start_service = resolved_task_discovery_start_service
    app.state.task_discovery_convergence_service = resolved_task_discovery_convergence_service
    app.state.task_target_preview_service = resolved_task_target_preview_service
    app.state.task_target_result_service = resolved_task_target_result_service
    app.state.task_event_convergence_service = resolved_task_event_convergence_service
    app.state.task_event_stream_service = resolved_task_event_stream_service
    app.state.workbench_metrics_service = resolved_workbench_metrics_service
    app.state.executor_connection_hello_timeout_seconds = hello_timeout_seconds
    app.state.executor_connection_recheck_interval_seconds = recheck_interval_seconds
    app.state.task_event_stream_poll_interval_seconds = stream_poll_interval_seconds
    app.state.task_event_stream_keepalive_interval_seconds = stream_keepalive_interval_seconds
    app.state.task_event_stream_max_connection_seconds = stream_max_connection_seconds
    install_request_context(app)
    register_error_handlers(app)
    app.include_router(system_router)
    app.include_router(registration_router)
    app.include_router(device_credential_router)
    app.include_router(device_session_router)
    app.include_router(installation_access_router)
    app.include_router(platform_session_router)
    app.include_router(task_event_stream_router)
    app.include_router(task_control_router)
    app.include_router(task_target_preview_router)
    app.include_router(task_target_result_router)
    app.include_router(task_discovery_router)
    app.include_router(task_router)
    app.include_router(workbench_router)
    app.include_router(executor_websocket_router)
    return app
