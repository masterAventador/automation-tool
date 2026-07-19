pub mod browser_discovery;
pub mod browser_settings;
pub mod control_plane;
pub mod device_credentials;
pub mod device_identity;
pub mod executor_bootstrap;
mod executor_diagnostics;
pub mod executor_manager;
pub mod executor_package;
pub mod executor_platform;
pub mod executor_protocol;
pub mod secure_store;

use device_credentials::initialize_production_device_credential_vault;
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
use device_credentials::ProductionDeviceCredentialVault;
#[cfg(all(feature = "desktop-e2e", not(feature = "control-plane-e2e")))]
use device_identity::initialize_ephemeral_identity;
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
use device_identity::initialize_production_identity;
#[cfg(feature = "control-plane-e2e")]
use device_identity::ProductionDeviceIdentity;
use tauri::Manager;

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct ControlPlaneCommandError {
    code: &'static str,
    retryable: bool,
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct ExecutorPlatformCommandError {
    code: &'static str,
    retryable: bool,
}

#[derive(serde::Serialize)]
struct ExecutorDiagnosticsSnapshot {
    lines: Vec<String>,
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct BrowserSettingsCommandError {
    code: &'static str,
    retryable: bool,
}

fn map_browser_settings_error(
    error: browser_settings::BrowserSettingsError,
) -> BrowserSettingsCommandError {
    let code = match error.code() {
        browser_settings::BrowserSettingsErrorCode::BrowserUnavailable => "browser_unavailable",
        browser_settings::BrowserSettingsErrorCode::DiscoveryUnavailable => {
            "browser_discovery_unavailable"
        }
        browser_settings::BrowserSettingsErrorCode::StorageUnavailable => "storage_unavailable",
    };
    BrowserSettingsCommandError {
        code,
        retryable: false,
    }
}

#[tauri::command]
fn get_browser_settings(
    settings: tauri::State<'_, browser_settings::BrowserSettingsService>,
) -> Result<browser_settings::BrowserSettingsSnapshot, BrowserSettingsCommandError> {
    settings.snapshot().map_err(map_browser_settings_error)
}

#[tauri::command]
fn select_browser(
    browser: browser_discovery::SupportedBrowser,
    settings: tauri::State<'_, browser_settings::BrowserSettingsService>,
) -> Result<browser_settings::BrowserSettingsSnapshot, BrowserSettingsCommandError> {
    settings
        .select_browser(browser)
        .map_err(map_browser_settings_error)
}

fn map_executor_platform_error(
    error: executor_platform::ExecutorPlatformError,
) -> ExecutorPlatformCommandError {
    let (code, retryable) = match error.code() {
        executor_platform::ExecutorPlatformErrorCode::ConfigurationInvalid => {
            ("configuration_invalid", false)
        }
        executor_platform::ExecutorPlatformErrorCode::StorageUnavailable => {
            ("storage_unavailable", false)
        }
        executor_platform::ExecutorPlatformErrorCode::AlreadyRunning => ("already_running", true),
        executor_platform::ExecutorPlatformErrorCode::AuthenticationRejected => {
            ("authentication_rejected", false)
        }
        executor_platform::ExecutorPlatformErrorCode::PackageRejected => {
            ("package_rejected", false)
        }
        executor_platform::ExecutorPlatformErrorCode::ProcessUnavailable => {
            ("process_unavailable", true)
        }
        executor_platform::ExecutorPlatformErrorCode::TimedOut => ("timed_out", true),
    };
    ExecutorPlatformCommandError { code, retryable }
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
fn map_executor_connection_error(
    error: control_plane::ControlPlaneError,
) -> ExecutorPlatformCommandError {
    let retryable = error.retryable();
    let code = match error.code() {
        control_plane::ControlPlaneErrorCode::CredentialMissing => "credential_missing",
        control_plane::ControlPlaneErrorCode::InstallationAccessDenied => {
            "installation_access_denied"
        }
        control_plane::ControlPlaneErrorCode::TransportUnavailable
        | control_plane::ControlPlaneErrorCode::OutcomeUncertain => "transport_unavailable",
        control_plane::ControlPlaneErrorCode::IdentityUnavailable
        | control_plane::ControlPlaneErrorCode::StorageUnavailable => "storage_unavailable",
        control_plane::ControlPlaneErrorCode::ProtocolInvalid
        | control_plane::ControlPlaneErrorCode::RequestRejected => "operation_unavailable",
    };
    ExecutorPlatformCommandError { code, retryable }
}

#[tauri::command]
fn get_executor_status(
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<executor_manager::ExecutorManagerStatus, ExecutorPlatformCommandError> {
    platform.status().map_err(map_executor_platform_error)
}

#[tauri::command]
fn get_executor_diagnostics(
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<ExecutorDiagnosticsSnapshot, ExecutorPlatformCommandError> {
    platform
        .diagnostics()
        .map(|lines| ExecutorDiagnosticsSnapshot { lines })
        .map_err(map_executor_platform_error)
}

#[tauri::command]
fn emergency_stop_executor(
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<executor_manager::ExecutorManagerStatus, ExecutorPlatformCommandError> {
    platform
        .emergency_stop()
        .map_err(map_executor_platform_error)
}

#[tauri::command]
#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
async fn restart_executor(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<executor_manager::ExecutorManagerStatus, ExecutorPlatformCommandError> {
    let connection = client
        .issue_executor_connection(&vault)
        .await
        .map_err(map_executor_connection_error)?;
    let service = platform.inner().clone();
    tauri::async_runtime::spawn_blocking(move || service.restart(connection))
        .await
        .map_err(|_| ExecutorPlatformCommandError {
            code: "process_unavailable",
            retryable: true,
        })?
        .map_err(map_executor_platform_error)
}

#[tauri::command]
#[cfg(all(feature = "desktop-e2e", not(feature = "control-plane-e2e")))]
async fn restart_executor(
) -> Result<executor_manager::ExecutorManagerStatus, ExecutorPlatformCommandError> {
    Err(ExecutorPlatformCommandError {
        code: "operation_unavailable",
        retryable: false,
    })
}

#[tauri::command]
#[cfg(feature = "desktop-e2e")]
async fn check_control_plane_health(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
) -> Result<control_plane::ControlPlaneHealth, ControlPlaneCommandError> {
    client.check_health().await.map_err(map_control_plane_error)
}

#[tauri::command]
#[cfg(not(feature = "desktop-e2e"))]
async fn check_control_plane_health(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::ControlPlaneHealth, ControlPlaneCommandError> {
    let health = client
        .check_health()
        .await
        .map_err(map_control_plane_error)?;
    client
        .check_installation_access_if_registered(&vault)
        .await
        .map_err(map_control_plane_error)?;
    Ok(health)
}

fn map_control_plane_error(error: control_plane::ControlPlaneError) -> ControlPlaneCommandError {
    let code = match error.code() {
        control_plane::ControlPlaneErrorCode::TransportUnavailable => "transport_unavailable",
        control_plane::ControlPlaneErrorCode::CredentialMissing => "credential_missing",
        control_plane::ControlPlaneErrorCode::IdentityUnavailable => "identity_unavailable",
        control_plane::ControlPlaneErrorCode::StorageUnavailable => "storage_unavailable",
        control_plane::ControlPlaneErrorCode::OutcomeUncertain => "outcome_uncertain",
        control_plane::ControlPlaneErrorCode::InstallationAccessDenied => {
            "installation_access_denied"
        }
        control_plane::ControlPlaneErrorCode::ProtocolInvalid
        | control_plane::ControlPlaneErrorCode::RequestRejected => "operation_unavailable",
    };
    ControlPlaneCommandError {
        code,
        retryable: error.retryable(),
    }
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskProjectionStreamSummary {
    last_sequence: u64,
    terminal: bool,
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn get_workbench_status(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::WorkbenchRuntimeStatus, ControlPlaneCommandError> {
    client
        .get_workbench_status(&vault)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn create_douyin_search_exposure_task(
    definition: control_plane::DouyinSearchExposureTaskDefinition,
    idempotency_key: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::TaskSnapshot, ControlPlaneCommandError> {
    client
        .create_task(&vault, &idempotency_key, &definition)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(feature = "control-plane-e2e")]
fn acceptance_task_definition() -> control_plane::DouyinSearchExposureTaskDefinition {
    control_plane::DouyinSearchExposureTaskDefinition::new(
        "新能源汽车".to_owned(),
        control_plane::DouyinSearchExposureAction::Browse,
        None,
        10,
        30,
        90,
    )
    .expect("acceptance Task definition must remain valid")
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn emergency_stop_workbench_task(
    task_id: String,
    idempotency_key: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::TaskControlCommand, ControlPlaneCommandError> {
    client
        .emergency_stop_task(&vault, &task_id, &idempotency_key)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn pause_task_run(
    task_id: String,
    idempotency_key: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::TaskControlCommand, ControlPlaneCommandError> {
    client
        .pause_task(&vault, &task_id, &idempotency_key)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn resume_task_run(
    task_id: String,
    idempotency_key: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::TaskControlCommand, ControlPlaneCommandError> {
    client
        .resume_task(&vault, &task_id, &idempotency_key)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn cancel_task_run(
    task_id: String,
    idempotency_key: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::TaskControlCommand, ControlPlaneCommandError> {
    client
        .cancel_task(&vault, &task_id, &idempotency_key)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn emergency_stop_task_run(
    task_id: String,
    idempotency_key: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::TaskControlCommand, ControlPlaneCommandError> {
    client
        .emergency_stop_task(&vault, &task_id, &idempotency_key)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn get_task_snapshot(
    task_id: String,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::TaskSnapshot, ControlPlaneCommandError> {
    client
        .get_task(&vault, &task_id)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn list_task_snapshots(
    cursor: Option<String>,
    limit: u16,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<control_plane::TaskListPage, ControlPlaneCommandError> {
    client
        .list_tasks(&vault, cursor.as_deref(), limit)
        .await
        .map_err(map_control_plane_error)
}

#[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
#[tauri::command]
async fn stream_task_projection_events(
    task_id: String,
    after_sequence: u64,
    on_event: tauri::ipc::Channel<control_plane::TaskEvent>,
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskProjectionStreamSummary, ControlPlaneCommandError> {
    let result = client
        .stream_task_events_with(&vault, &task_id, Some(after_sequence), None, |event| {
            on_event.send(event.clone()).is_ok()
        })
        .await
        .map_err(map_control_plane_error)?;
    let mut last_sequence = after_sequence;
    for event in result.events() {
        last_sequence = event.sequence();
    }
    Ok(TaskProjectionStreamSummary {
        last_sequence,
        terminal: result.terminal(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct ControlPlaneAcceptanceSummary {
    health_available: bool,
    installation_id: String,
    initial_version: u32,
    first_capability: &'static str,
    rotated_version: u32,
    second_capability: &'static str,
    revoked_version: u32,
    app_secret_removed: bool,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct InstallationRevocationAcceptanceRegistration {
    installation_id: String,
    revision: u32,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskCreationAcceptanceSummary {
    installation_id: String,
    task_id: String,
    status: String,
    revision: u32,
    replayed: bool,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskCreateFormAcceptancePreparation {
    installation_id: String,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskRunAcceptancePreparation {
    installation_id: String,
    controlled_task_id: String,
    emergency_task_id: String,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskQueryAcceptanceSummary {
    installation_id: String,
    first_page_count: usize,
    second_page_count: usize,
    detail_matched: bool,
    foreign_hidden: bool,
    cursor_opaque: bool,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskEventStreamAcceptanceSummary {
    installation_id: String,
    task_id: String,
    initial_sequences: Vec<u64>,
    resumed_sequences: Vec<u64>,
    terminal: bool,
    progress_percent: Option<u8>,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskProjectionAcceptancePreparation {
    installation_id: String,
    task_id: String,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct WorkbenchAcceptancePreparation {
    installation_id: String,
    task_id: String,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskControlAcceptanceSummary {
    installation_id: String,
    task_id: String,
    pause_command_type: String,
    pause_command_status: String,
    pause_sequence: u64,
    paused_event_type: String,
    resume_command_type: String,
    resume_command_status: String,
    resume_sequence: u64,
    resumed_event_type: String,
    final_status: String,
    final_revision: u32,
}

#[cfg(feature = "control-plane-e2e")]
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskTerminationAcceptanceSummary {
    installation_id: String,
    cancel_task_id: String,
    cancel_command_type: String,
    cancel_command_status: String,
    cancel_sequence: u64,
    cancel_event_type: String,
    cancel_final_status: String,
    cancel_final_revision: u32,
    emergency_task_id: String,
    emergency_command_type: String,
    emergency_command_status: String,
    emergency_sequence: u64,
    emergency_event_type: String,
    emergency_final_status: String,
    emergency_final_revision: u32,
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn control_task_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskControlAcceptanceSummary, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T313_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T313_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    let task = client
        .create_task(
            &vault,
            "task:control:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    let initial = client
        .stream_task_events(&vault, task.task_id(), None, Some(2))
        .await
        .map_err(map_control_plane_error)?;
    let initial_sequence = initial
        .events()
        .last()
        .map(control_plane::TaskEvent::sequence)
        .ok_or(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        })?;
    let pause = client
        .pause_task(
            &vault,
            task.task_id(),
            "task:control:pause:tauri-acceptance",
        )
        .await
        .map_err(map_control_plane_error)?;
    let paused = client
        .stream_task_events(&vault, task.task_id(), Some(initial_sequence), Some(1))
        .await
        .map_err(map_control_plane_error)?;
    let paused_event = paused.events().first().ok_or(ControlPlaneCommandError {
        code: "operation_unavailable",
        retryable: false,
    })?;
    let resume = client
        .resume_task(
            &vault,
            task.task_id(),
            "task:control:resume:tauri-acceptance",
        )
        .await
        .map_err(map_control_plane_error)?;
    let resumed = client
        .stream_task_events(
            &vault,
            task.task_id(),
            Some(paused_event.sequence()),
            Some(1),
        )
        .await
        .map_err(map_control_plane_error)?;
    let resumed_event = resumed.events().first().ok_or(ControlPlaneCommandError {
        code: "operation_unavailable",
        retryable: false,
    })?;
    let final_snapshot = client
        .get_task(&vault, task.task_id())
        .await
        .map_err(map_control_plane_error)?;

    Ok(TaskControlAcceptanceSummary {
        installation_id: registration.installation_id().to_owned(),
        task_id: task.task_id().to_owned(),
        pause_command_type: pause.command_type().to_owned(),
        pause_command_status: pause.status().to_owned(),
        pause_sequence: pause.sequence(),
        paused_event_type: paused_event.event_type().to_owned(),
        resume_command_type: resume.command_type().to_owned(),
        resume_command_status: resume.status().to_owned(),
        resume_sequence: resume.sequence(),
        resumed_event_type: resumed_event.event_type().to_owned(),
        final_status: final_snapshot.status().to_owned(),
        final_revision: final_snapshot.revision(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn terminate_tasks_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskTerminationAcceptanceSummary, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T314_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T314_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;

    let cancel_task = client
        .create_task(
            &vault,
            "task:termination:cancel:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    let cancel_initial = client
        .stream_task_events(&vault, cancel_task.task_id(), None, Some(2))
        .await
        .map_err(map_control_plane_error)?;
    let cancel_cursor = cancel_initial
        .events()
        .last()
        .map(control_plane::TaskEvent::sequence)
        .ok_or(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        })?;
    let cancel = client
        .cancel_task(
            &vault,
            cancel_task.task_id(),
            "task:termination:cancel-command:tauri-acceptance",
        )
        .await
        .map_err(map_control_plane_error)?;
    let cancel_terminal = client
        .stream_task_events(&vault, cancel_task.task_id(), Some(cancel_cursor), Some(1))
        .await
        .map_err(map_control_plane_error)?;
    let cancel_event = cancel_terminal
        .events()
        .first()
        .ok_or(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        })?;
    let cancel_final = client
        .get_task(&vault, cancel_task.task_id())
        .await
        .map_err(map_control_plane_error)?;

    let emergency_task = client
        .create_task(
            &vault,
            "task:termination:emergency:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    let emergency_initial = client
        .stream_task_events(&vault, emergency_task.task_id(), None, Some(2))
        .await
        .map_err(map_control_plane_error)?;
    let emergency_cursor = emergency_initial
        .events()
        .last()
        .map(control_plane::TaskEvent::sequence)
        .ok_or(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        })?;
    let emergency = client
        .emergency_stop_task(
            &vault,
            emergency_task.task_id(),
            "task:termination:emergency-command:tauri-acceptance",
        )
        .await
        .map_err(map_control_plane_error)?;
    let emergency_terminal = client
        .stream_task_events(
            &vault,
            emergency_task.task_id(),
            Some(emergency_cursor),
            Some(1),
        )
        .await
        .map_err(map_control_plane_error)?;
    let emergency_event = emergency_terminal
        .events()
        .first()
        .ok_or(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        })?;
    let emergency_final = client
        .get_task(&vault, emergency_task.task_id())
        .await
        .map_err(map_control_plane_error)?;

    Ok(TaskTerminationAcceptanceSummary {
        installation_id: registration.installation_id().to_owned(),
        cancel_task_id: cancel_task.task_id().to_owned(),
        cancel_command_type: cancel.command_type().to_owned(),
        cancel_command_status: cancel.status().to_owned(),
        cancel_sequence: cancel.sequence(),
        cancel_event_type: cancel_event.event_type().to_owned(),
        cancel_final_status: cancel_final.status().to_owned(),
        cancel_final_revision: cancel_final.revision(),
        emergency_task_id: emergency_task.task_id().to_owned(),
        emergency_command_type: emergency.command_type().to_owned(),
        emergency_command_status: emergency.status().to_owned(),
        emergency_sequence: emergency.sequence(),
        emergency_event_type: emergency_event.event_type().to_owned(),
        emergency_final_status: emergency_final.status().to_owned(),
        emergency_final_revision: emergency_final.revision(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn stream_task_events_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskEventStreamAcceptanceSummary, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T312_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T312_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    let task = client
        .create_task(
            &vault,
            "task:stream:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    let initial = client
        .stream_task_events(&vault, task.task_id(), None, Some(2))
        .await
        .map_err(map_control_plane_error)?;
    let last_event_id = initial
        .events()
        .last()
        .map(control_plane::TaskEvent::sequence)
        .ok_or(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        })?;
    let resumed = client
        .stream_task_events(&vault, task.task_id(), Some(last_event_id), None)
        .await
        .map_err(map_control_plane_error)?;
    let progress_percent = resumed
        .events()
        .iter()
        .find(|event| event.event_type() == "step.progress")
        .and_then(control_plane::TaskEvent::progress_percent);

    Ok(TaskEventStreamAcceptanceSummary {
        installation_id: registration.installation_id().to_owned(),
        task_id: task.task_id().to_owned(),
        initial_sequences: initial
            .events()
            .iter()
            .map(control_plane::TaskEvent::sequence)
            .collect(),
        resumed_sequences: resumed
            .events()
            .iter()
            .map(control_plane::TaskEvent::sequence)
            .collect(),
        terminal: resumed.terminal(),
        progress_percent,
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_task_projection_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskProjectionAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T315_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T315_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    let task = client
        .create_task(
            &vault,
            "task:projection:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskProjectionAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
        task_id: task.task_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_workbench_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<WorkbenchAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T316_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T316_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    let task = client
        .create_task(
            &vault,
            "task:workbench:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    Ok(WorkbenchAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
        task_id: task.task_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_task_create_form_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskCreateFormAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T317_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T317_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskCreateFormAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_task_run_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskRunAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T318_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T318_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    let controlled_task = client
        .create_task(
            &vault,
            "task:run:controlled:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    let emergency_task = client
        .create_task(
            &vault,
            "task:run:emergency:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskRunAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
        controlled_task_id: controlled_task.task_id().to_owned(),
        emergency_task_id: emergency_task.task_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_task_lifecycle_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskCreateFormAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T319_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T319_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskCreateFormAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_executor_lifecycle_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskCreateFormAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_E414_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_E414_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskCreateFormAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
fn inject_executor_crash_for_acceptance(
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<(), ExecutorPlatformCommandError> {
    platform
        .inject_crash_for_acceptance()
        .map_err(map_executor_platform_error)
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
fn inject_executor_hang_for_acceptance(
    platform: tauri::State<'_, executor_platform::ExecutorPlatformService>,
) -> Result<(), ExecutorPlatformCommandError> {
    platform
        .inject_hang_for_acceptance()
        .map_err(map_executor_platform_error)
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
fn exit_app_for_acceptance(app: tauri::AppHandle) {
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(250));
        app.exit(0);
    });
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn prepare_task_restart_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskCreateFormAcceptancePreparation, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T320_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T320_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    Ok(TaskCreateFormAcceptancePreparation {
        installation_id: registration.installation_id().to_owned(),
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn query_tasks_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskQueryAcceptanceSummary, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T307_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T307_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let foreign_task_id = std::env::var("AUTOMATION_TOOL_T307_FOREIGN_TASK_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;

    let mut expected_ids = Vec::new();
    for key in [
        "task:query:tauri-acceptance:1",
        "task:query:tauri-acceptance:2",
        "task:query:tauri-acceptance:3",
    ] {
        let task = client
            .create_task(&vault, key, &acceptance_task_definition())
            .await
            .map_err(map_control_plane_error)?;
        expected_ids.push(task.task_id().to_owned());
    }

    let first_page = client
        .list_tasks(&vault, None, 2)
        .await
        .map_err(map_control_plane_error)?;
    let cursor = first_page.next_cursor().ok_or(ControlPlaneCommandError {
        code: "operation_unavailable",
        retryable: false,
    })?;
    let cursor_opaque = !expected_ids.iter().any(|task_id| cursor.contains(task_id));
    let second_page = client
        .list_tasks(&vault, Some(cursor), 2)
        .await
        .map_err(map_control_plane_error)?;
    let detail_task_id = expected_ids[1].clone();
    let detail = client
        .get_task(&vault, &detail_task_id)
        .await
        .map_err(map_control_plane_error)?;

    let mut listed_ids = first_page
        .items()
        .iter()
        .chain(second_page.items())
        .map(|task| task.task_id().to_owned())
        .collect::<Vec<_>>();
    listed_ids.sort();
    expected_ids.sort();
    let detail_matched = detail.task_id() == detail_task_id;
    let foreign_hidden = client
        .get_task(&vault, &foreign_task_id)
        .await
        .is_err_and(|error| error.code() == control_plane::ControlPlaneErrorCode::RequestRejected);
    if listed_ids != expected_ids
        || first_page.items().len() != 2
        || second_page.items().len() != 1
        || second_page.next_cursor().is_some()
        || !detail_matched
        || !foreign_hidden
        || !cursor_opaque
    {
        return Err(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        });
    }

    Ok(TaskQueryAcceptanceSummary {
        installation_id: registration.installation_id().to_owned(),
        first_page_count: first_page.items().len(),
        second_page_count: second_page.items().len(),
        detail_matched,
        foreign_hidden,
        cursor_opaque,
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn create_task_for_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<TaskCreationAcceptanceSummary, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_T306_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_T306_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    let first = client
        .create_task(
            &vault,
            "task:create:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    let replay = client
        .create_task(
            &vault,
            "task:create:tauri-acceptance",
            &acceptance_task_definition(),
        )
        .await
        .map_err(map_control_plane_error)?;
    let replayed = first.task_id() == replay.task_id()
        && first.status() == replay.status()
        && first.revision() == replay.revision();
    if !replayed {
        return Err(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        });
    }

    Ok(TaskCreationAcceptanceSummary {
        installation_id: registration.installation_id().to_owned(),
        task_id: first.task_id().to_owned(),
        status: first.status().to_owned(),
        revision: first.revision(),
        replayed,
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn register_installation_for_revocation_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<InstallationRevocationAcceptanceRegistration, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_I214_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_I214_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    client
        .check_installation_access_if_registered(&vault)
        .await
        .map_err(map_control_plane_error)?;

    Ok(InstallationRevocationAcceptanceRegistration {
        installation_id: registration.installation_id().to_owned(),
        revision: 1,
    })
}

#[cfg(feature = "control-plane-e2e")]
#[tauri::command]
async fn run_control_plane_acceptance(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
    identity: tauri::State<'_, ProductionDeviceIdentity>,
    vault: tauri::State<'_, ProductionDeviceCredentialVault>,
) -> Result<ControlPlaneAcceptanceSummary, ControlPlaneCommandError> {
    let token = std::env::var("AUTOMATION_TOOL_I209_BOOTSTRAP_TOKEN").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let environment_id = std::env::var("AUTOMATION_TOOL_I209_ENVIRONMENT_ID").map_err(|_| {
        ControlPlaneCommandError {
            code: "acceptance_configuration_unavailable",
            retryable: false,
        }
    })?;
    let bootstrap = control_plane::DemoBootstrap::new(token, environment_id)
        .map_err(map_control_plane_error)?;

    client
        .check_health()
        .await
        .map_err(map_control_plane_error)?;
    let registration = client
        .register_installation(&bootstrap, &identity, &vault)
        .await
        .map_err(map_control_plane_error)?;
    let first_session = client
        .exchange_device_session(
            &vault,
            control_plane::DeviceSessionCapability::AppControlPlane,
        )
        .await
        .map_err(map_control_plane_error)?;
    let rotated_version = client
        .rotate_device_credential(&vault)
        .await
        .map_err(map_control_plane_error)?;
    let second_session = client
        .exchange_device_session(
            &vault,
            control_plane::DeviceSessionCapability::ExecutorConnect,
        )
        .await
        .map_err(map_control_plane_error)?;
    let revoked_version = client
        .revoke_device_credential(&vault)
        .await
        .map_err(map_control_plane_error)?;
    let app_secret_removed = vault
        .load()
        .map_err(|_| ControlPlaneCommandError {
            code: "storage_unavailable",
            retryable: false,
        })?
        .is_none();
    if !app_secret_removed {
        return Err(ControlPlaneCommandError {
            code: "operation_unavailable",
            retryable: false,
        });
    }

    Ok(ControlPlaneAcceptanceSummary {
        health_available: true,
        installation_id: registration.installation_id().to_owned(),
        initial_version: registration.credential_version(),
        first_capability: first_session.capability().as_str(),
        rotated_version,
        second_capability: second_session.capability().as_str(),
        revoked_version,
        app_secret_removed,
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default().setup(|app| {
        app.manage(control_plane::ControlPlaneClient::local()?);
        let app_data_directory = app.path().app_data_dir()?;
        app.manage(browser_settings::BrowserSettingsService::initialize(
            &app_data_directory,
        )?);
        app.manage(executor_platform::ExecutorPlatformService::initialize(
            &app_data_directory,
        )?);
        #[cfg(all(feature = "desktop-e2e", not(feature = "control-plane-e2e")))]
        {
            let _production_identity_boundary = device_identity::initialize_production_identity;
            let _production_credential_boundary = initialize_production_device_credential_vault;
            let device_identity = initialize_ephemeral_identity()?;
            debug_assert_eq!(device_identity.as_bytes().len(), 32);
            app.manage(device_identity);
        }

        #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
        {
            let device_identity = initialize_production_identity(&app_data_directory)?;
            let device_credential_vault =
                initialize_production_device_credential_vault(&app_data_directory)?;
            debug_assert_eq!(device_identity.public_key().len(), 32);
            app.manage(device_identity);
            app.manage(device_credential_vault);
        }
        Ok(())
    });

    #[cfg(feature = "desktop-test-driver")]
    let builder = builder
        .plugin(tauri_plugin_wdio::init())
        .plugin(tauri_plugin_wdio_webdriver::init());

    #[cfg(all(not(feature = "control-plane-e2e"), feature = "desktop-e2e"))]
    let builder = builder.invoke_handler(tauri::generate_handler![
        check_control_plane_health,
        get_executor_status,
        restart_executor,
        get_executor_diagnostics,
        emergency_stop_executor,
        get_browser_settings,
        select_browser
    ]);
    #[cfg(all(not(feature = "control-plane-e2e"), not(feature = "desktop-e2e")))]
    let builder = builder.invoke_handler(tauri::generate_handler![
        check_control_plane_health,
        create_douyin_search_exposure_task,
        get_workbench_status,
        emergency_stop_workbench_task,
        pause_task_run,
        resume_task_run,
        cancel_task_run,
        emergency_stop_task_run,
        get_task_snapshot,
        list_task_snapshots,
        stream_task_projection_events,
        get_executor_status,
        restart_executor,
        get_executor_diagnostics,
        emergency_stop_executor,
        get_browser_settings,
        select_browser
    ]);
    #[cfg(feature = "control-plane-e2e")]
    let builder = builder.invoke_handler(tauri::generate_handler![
        check_control_plane_health,
        create_douyin_search_exposure_task,
        get_workbench_status,
        emergency_stop_workbench_task,
        pause_task_run,
        resume_task_run,
        cancel_task_run,
        emergency_stop_task_run,
        get_task_snapshot,
        list_task_snapshots,
        stream_task_projection_events,
        run_control_plane_acceptance,
        register_installation_for_revocation_acceptance,
        create_task_for_acceptance,
        query_tasks_for_acceptance,
        stream_task_events_for_acceptance,
        prepare_task_projection_for_acceptance,
        prepare_task_create_form_for_acceptance,
        prepare_task_run_for_acceptance,
        prepare_task_lifecycle_for_acceptance,
        prepare_executor_lifecycle_for_acceptance,
        prepare_task_restart_for_acceptance,
        prepare_workbench_for_acceptance,
        control_task_for_acceptance,
        terminate_tasks_for_acceptance,
        get_executor_status,
        restart_executor,
        get_executor_diagnostics,
        emergency_stop_executor,
        inject_executor_crash_for_acceptance,
        inject_executor_hang_for_acceptance,
        exit_app_for_acceptance,
        get_browser_settings,
        select_browser
    ]);

    let app = builder
        .build(tauri::generate_context!())
        .expect("failed to build desktop application");
    app.run(|app_handle, event| {
        if matches!(
            event,
            tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
        ) {
            if let Some(platform) =
                app_handle.try_state::<executor_platform::ExecutorPlatformService>()
            {
                platform.shutdown_for_app_exit();
            }
        }
    });
}
