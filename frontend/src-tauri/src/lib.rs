pub mod control_plane;
pub mod device_credentials;
pub mod device_identity;
pub mod secure_store;

use device_credentials::initialize_production_device_credential_vault;
#[cfg(feature = "control-plane-e2e")]
use device_credentials::ProductionDeviceCredentialVault;
#[cfg(feature = "desktop-e2e")]
use device_identity::initialize_ephemeral_identity;
#[cfg(not(feature = "desktop-e2e"))]
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
async fn check_control_plane_health(
    client: tauri::State<'_, control_plane::ControlPlaneClient>,
) -> Result<control_plane::ControlPlaneHealth, ControlPlaneCommandError> {
    client.check_health().await.map_err(map_control_plane_error)
}

fn map_control_plane_error(error: control_plane::ControlPlaneError) -> ControlPlaneCommandError {
    let code = match error.code() {
        control_plane::ControlPlaneErrorCode::TransportUnavailable => "transport_unavailable",
        control_plane::ControlPlaneErrorCode::CredentialMissing => "credential_missing",
        control_plane::ControlPlaneErrorCode::IdentityUnavailable => "identity_unavailable",
        control_plane::ControlPlaneErrorCode::StorageUnavailable => "storage_unavailable",
        control_plane::ControlPlaneErrorCode::OutcomeUncertain => "outcome_uncertain",
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
        #[cfg(feature = "desktop-e2e")]
        {
            let _production_identity_boundary = device_identity::initialize_production_identity;
            let _production_credential_boundary = initialize_production_device_credential_vault;
            let device_identity = initialize_ephemeral_identity()?;
            debug_assert_eq!(device_identity.as_bytes().len(), 32);
            app.manage(device_identity);
        }

        #[cfg(not(feature = "desktop-e2e"))]
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
        run_control_plane_acceptance
    ]);

    builder
        .run(tauri::generate_context!())
        .expect("failed to run desktop application");
}
