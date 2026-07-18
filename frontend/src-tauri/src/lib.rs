pub mod control_plane;
pub mod device_credentials;
pub mod device_identity;
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
        .create_task(&vault, "task:control:tauri-acceptance")
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
        .create_task(&vault, "task:stream:tauri-acceptance")
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
            .create_task(&vault, key)
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
        .create_task(&vault, "task:create:tauri-acceptance")
        .await
        .map_err(map_control_plane_error)?;
    let replay = client
        .create_task(&vault, "task:create:tauri-acceptance")
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
            let app_data_directory = app.path().app_data_dir()?;
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

    #[cfg(not(feature = "control-plane-e2e"))]
    let builder = builder.invoke_handler(tauri::generate_handler![check_control_plane_health]);
    #[cfg(feature = "control-plane-e2e")]
    let builder = builder.invoke_handler(tauri::generate_handler![
        check_control_plane_health,
        run_control_plane_acceptance,
        register_installation_for_revocation_acceptance,
        create_task_for_acceptance,
        query_tasks_for_acceptance,
        stream_task_events_for_acceptance,
        control_task_for_acceptance
    ]);

    builder
        .run(tauri::generate_context!())
        .expect("failed to run desktop application");
}
